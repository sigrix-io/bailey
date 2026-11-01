"""Knowledge: the person's documents, converted, split, indexed and searched on this computer.

Design §3.7, on S1's ruling (Decision 6, sigrix-io/sigrix#6558; the measurements are in
``docs/spikes/0001-agent-core-and-local-index.md``), ported from ``spike/s1/index/pipeline.py``:

1. **Convert** each document to Markdown. MarkItDown takes PDF, Word and HTML; Markdown and
   text go in as they are, CSV as a table and JSON as its text.
2. **Split** it by heading, a PDF by page, into passages of at most :data:`WORDS` words with
   :data:`OVERLAP` of them overlapping, for an embedding model that reads 256 tokens. A table
   keeps its header row in every passage, and a passage's indexed text opens with its
   document's title and its heading, which put one more of the spike's questions first at no
   cost at query time.
3. **Embed** with all-MiniLM-L6-v2 through fastembed, on this computer. The model is fetched
   once, from Hugging Face, when a collection first needs searching, and Bailey says so with
   its size before it starts; after that it loads with nothing fetched.
4. **Index** in ``bailey.db``: FTS5 over each passage's text, and its vector as a BLOB with
   the name of the model that made it, so a different model re-indexes rather than mixing
   two. Nothing loads a SQLite extension.
5. **Search** both ways, keywords (bm25) and vectors (cosine, in numpy), and merge the two
   rankings by reciprocal rank fusion.

A collection small enough goes into the prompt whole instead, the ruling's one change:
there it is part of the cached prefix, the model reads all of it, and nothing waits for the
model download. :func:`mode` decides. Either way each passage the model sees is numbered,
and the answer cites it as ``[P12]`` (:func:`cited`).

MarkItDown, fastembed and numpy load only when a document needs them.
"""

from __future__ import annotations

import csv
import io
import json
import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import numpy as np

    from bailey.store import Store

#: A passage's most words, and how many of them the next passage repeats.
WORDS = 180
OVERLAP = 30
#: Reciprocal rank fusion's constant, how deep each ranking is read, and how many passages a search returns.
RRF_K = 60
DEPTH = 50
TOP = 5

#: The documents a collection may hold before it is searched rather than sent whole, in tokens,
#: on a provider that caches the prompt's stable start. Set by the cost per message
#: (``spike/s8/whole_or_searched.py``): at 10,000 tokens, whole costs 0.76 (Anthropic) and
#: 0.85 (OpenAI) of a searched message with the cache warm, and 2.2 and 1.8 times one when it
#: has gone cold, as it does after a few idle minutes; at 20,000 the cold case is 3.8 and 3.0
#: times. Below the limit the model also reads every passage, so no search can miss one.
WHOLE_LIMIT = 10_000
#: The providers whose context Bailey knows to be long enough, and cached, for documents sent whole.
WHOLE_PROVIDERS = frozenset({"anthropic", "openai"})
#: A local server's context window is not Bailey's to know, and local servers often open a model
#: with 4,096 tokens unless told otherwise. A collection this small still fits there beside the
#: instructions, the latest message and its answer, spares a small model a tool call, and needs
#: no model download.
LOCAL_WHOLE_LIMIT = 2_000

MAX_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_TOKENS = 2_000_000

TEXT_TYPES = frozenset({"md", "markdown", "txt", "text"})
CONVERTED_TYPES = frozenset({"pdf", "docx", "html", "htm"})
ACCEPTED_TYPES = TEXT_TYPES | CONVERTED_TYPES | {"csv", "json"}

HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
CITATION = re.compile(r"\[P(\d+)\]")
STOPWORDS = frozenset(
    "a an and are as at be but by can could do does for from had has have how i if in is it its me my no not of "
    "on or our so than that the their them then there these they this to too us was we what whats when where "
    "which who why will with would you your".split()
)


class DocumentRefused(ValueError):
    """A document Bailey will not take, with the sentence that says why."""


@dataclass(frozen=True)
class Documents:
    """How an assistant's documents reach the model for one run: whole, or searched."""

    mode: str
    #: The collection as the prompt carries it, in ``whole`` mode.
    text: str = ""
    #: The search behind ``search_knowledge``, in ``search`` mode: a query in, passages out.
    search: Callable[[str], list[dict[str, Any]]] | None = None
    #: Answer only from the documents, never from what the model knows.
    grounded_only: bool = False


# ---------------------------------------------------------------------------
# Convert and split
# ---------------------------------------------------------------------------


def extension_of(name: str) -> str:
    return Path(name).suffix.lower().lstrip(".")


def convert(name: str, data: bytes) -> str:
    """The document as Markdown: as it is when it is text, converted when it is not."""

    extension = extension_of(name)
    if extension not in ACCEPTED_TYPES:
        raise DocumentRefused(
            f"{name}: Bailey reads {', '.join(sorted(ACCEPTED_TYPES))} documents, and this is a .{extension or '?'} file."
        )
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentRefused(f"{name} is over {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB.")
    if extension in CONVERTED_TYPES:
        return _converted(name, data, extension)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise DocumentRefused(f"{name} is not UTF-8 text.") from error
    if extension == "csv":
        return _csv_table(text, name)
    if extension == "json":
        try:
            return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except ValueError as error:
            raise DocumentRefused(f"{name} is not JSON.") from error
    return text


def _converted(name: str, data: bytes, extension: str) -> str:
    """The one MarkItDown converter for the type, called directly.

    MarkItDown's own dispatch tries each converter in turn, and its plain-text one takes a
    damaged PDF's bytes as text; called directly, a damaged file says so instead.
    """

    from markitdown import StreamInfo
    from markitdown.converters import DocxConverter, HtmlConverter, PdfConverter

    converter = {"pdf": PdfConverter, "docx": DocxConverter, "html": HtmlConverter, "htm": HtmlConverter}[extension]()
    try:
        result = converter.convert(io.BytesIO(data), StreamInfo(extension=f".{extension}", filename=name))
    except Exception as error:  # a damaged or protected file is said so, never a traceback
        raise DocumentRefused(f"{name} could not be read: {type(error).__name__}.") from error
    text = result.text_content or ""
    if not text.strip():
        raise DocumentRefused(f"{name} has no text Bailey can read, as a scanned page has none.")
    return text


def _cell(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def _csv_table(text: str, name: str) -> str:
    try:
        rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    except csv.Error as error:
        raise DocumentRefused(f"{name} is not CSV Bailey can read.") from error
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    lines = []
    for index, row in enumerate(rows):
        cells = [_cell(cell) for cell in row] + [""] * (width - len(row))
        lines.append("| " + " | ".join(cells) + " |")
        if index == 0:
            lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
    return "\n".join(lines)


@dataclass(frozen=True)
class Passage:
    heading: str
    page: int | None
    #: Its window's place in its section: 0 opens the section, and a later one repeats the
    #: :data:`OVERLAP` words before it.
    seq: int
    body: str

    def text(self, title: str) -> str:
        """What is indexed: the body, opening with the document's title and the heading."""
        return (
            f"{title} › {self.heading}\n{self.body}"
            if self.heading and self.heading != title
            else f"{title}\n{self.body}"
        )


def _is_table(lines: Sequence[str]) -> bool:
    return len(lines) > 2 and lines[0].startswith("|") and set(lines[1].replace("|", "").strip()) <= set("-: ")


def windows(text: str) -> list[str]:
    """A section in passages: a table by rows under its header, prose by words with an overlap."""

    lines = [line for line in text.splitlines() if line.strip()]
    if _is_table(lines):
        header, rows = lines[:2], lines[2:]
        out: list[str] = []
        chunk: list[str] = []
        size = 0
        for row in rows:
            if chunk and size + len(row.split()) > WORDS:
                out.append("\n".join(header + chunk))
                chunk, size = [], 0
            chunk.append(row)
            size += len(row.split())
        return [*out, "\n".join(header + chunk)] if chunk else out
    words = text.split()
    if len(words) <= WORDS:
        # Kept as written, as the spike kept it: a short section's lists and lines read as they were.
        return ["\n".join(lines)] if words else []
    step = WORDS - OVERLAP
    return [" ".join(words[start : start + WORDS]) for start in range(0, len(words) - OVERLAP, step)]


def _sections(markdown: str, is_pdf: bool) -> list[tuple[str, int | None, str]]:
    if is_pdf and "\x0c" in markdown:
        pages = markdown.split("\x0c")
        return [(f"page {number}", number, page.strip()) for number, page in enumerate(pages, 1) if page.strip()]
    found: list[tuple[str, int | None, str]] = []
    heading = ""
    lines: list[str] = []
    for line in markdown.splitlines():
        if match := HEADING.match(line):
            if "".join(lines).strip():
                found.append((heading, None, "\n".join(lines).strip()))
            heading, lines = match.group(2).strip(), []
        else:
            lines.append(line)
    if "".join(lines).strip():
        found.append((heading, None, "\n".join(lines).strip()))
    return found


def title_of(name: str, markdown: str) -> str:
    for line in markdown.splitlines():
        if match := HEADING.match(line):
            return match.group(2).strip()
        if extension_of(name) == "pdf" and line.strip():
            return line.strip()[:120]
    return Path(name).stem.replace("_", " ").replace("-", " ")


def split(name: str, markdown: str) -> tuple[str, list[Passage]]:
    """The document's title and its passages, each keeping its heading and page."""

    title = title_of(name, markdown)
    passages = []
    for heading, page, text in _sections(markdown, extension_of(name) == "pdf"):
        for seq, body in enumerate(windows(text)):
            passages.append(Passage(heading or title, page, seq, body))
    return title, passages


def estimate_tokens(text: str) -> int:
    """About how many tokens a text costs: a quarter of its characters, the usual rate for English."""
    return len(text) // 4 + 1


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmbeddingModel:
    name: str
    #: How the person sees it named, its vectors' length, and what its one download weighs.
    label: str
    dimensions: int
    download_mb: int
    #: The cosine under which a passage no keyword search found is left out (:func:`search`).
    floor: float


#: The model S1 measured: the right passage first for 9 of the spike's 10 questions with
#: hybrid search. fastembed fetches it from Hugging Face (``Qdrant/all-MiniLM-L6-v2-onnx``).
MINILM = EmbeddingModel("sentence-transformers/all-MiniLM-L6-v2", "all-MiniLM-L6-v2", 384, 90, floor=0.2)


class Embedder(Protocol):
    model: EmbeddingModel

    def ready(self) -> bool: ...

    def embed(self, texts: Sequence[str]) -> np.ndarray: ...


class LocalEmbedder:
    """fastembed's model on this computer: in the data folder once fetched, or in a folder the person named.

    It loads with nothing fetched (``local_files_only``); :meth:`download` is the one way it
    reaches the network, and only when the person has said yes to it.
    """

    def __init__(self, cache_dir: Path, folder: Path | None = None, model: EmbeddingModel = MINILM) -> None:
        self.model = model
        self.cache_dir = cache_dir
        self.folder = folder
        self._loaded: Any = None

    def _load(self, *, fetch: bool) -> Any:
        import os

        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        from fastembed import TextEmbedding

        if self.folder is not None:
            return TextEmbedding(self.model.name, specific_model_path=str(self.folder))
        return TextEmbedding(self.model.name, cache_dir=str(self.cache_dir), local_files_only=not fetch)

    def ready(self) -> bool:
        if self._loaded is not None:
            return True
        try:
            self._loaded = self._load(fetch=False)
        except Exception:  # not fetched yet, or a folder without the model in it
            return False
        return True

    def download(self) -> None:
        """Fetch the model from Hugging Face, once. The caller has told the person first."""
        self._loaded = self._load(fetch=True)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        import numpy as np

        if not self.ready():
            raise RuntimeError("the search model is not on this computer")
        vectors = np.vstack(list(self._loaded.embed(list(texts), batch_size=64))).astype(np.float32)
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        return vectors / np.where(norms == 0, 1, norms)


# ---------------------------------------------------------------------------
# A collection: add, index, search
# ---------------------------------------------------------------------------


def add_document(
    store: Store, assistant_id: str, name: str, data: bytes, *, slot: str = "", kind: str = "own"
) -> dict[str, Any]:
    """Convert, split and keep a document in the assistant's collection; one in the same slot is replaced."""

    markdown = convert(name, data)
    title, passages = split(name, markdown)
    if not passages:
        raise DocumentRefused(f"{name} has no text to keep.")
    tokens = sum(estimate_tokens(passage.body) for passage in passages)
    if tokens > MAX_DOCUMENT_TOKENS:
        raise DocumentRefused(
            f"{name} is about {tokens:,} tokens; Bailey keeps documents up to {MAX_DOCUMENT_TOKENS:,}."
        )
    document = {
        "id": uuid.uuid4().hex,
        "assistant_id": assistant_id,
        "slot": slot,
        # An upload is named by its file; a reference keeps its path, which names its skill.
        "name": name if kind == "reference" else Path(name).name,
        "kind": kind,
        "title": title,
        "tokens": tokens,
    }
    store.add_document(
        document,
        [{"heading": p.heading, "page": p.page, "seq": p.seq, "body": p.body, "text": p.text(title)} for p in passages],
    )
    return document


def collection_tokens(store: Store, assistant_id: str) -> int:
    return sum(int(document["tokens"]) for document in store.documents(assistant_id))


def mode(tokens: int, provider: str) -> str:
    """How the documents reach the model: ``whole`` in the prompt, ``search`` through the tool, or ``none``."""

    if tokens <= 0:
        return "none"
    if tokens <= (WHOLE_LIMIT if provider in WHOLE_PROVIDERS else LOCAL_WHOLE_LIMIT):
        return "whole"
    return "search"


def index(store: Store, assistant_id: str, embedder: Embedder) -> int:
    """Embed each passage that has no vector from this model, a different model's included. How many."""

    import numpy as np

    pending = store.unembedded(assistant_id, embedder.model.name)
    for start in range(0, len(pending), 256):
        batch = pending[start : start + 256]
        vectors = embedder.embed([text for _, text in batch]).astype(np.float32)
        store.set_vectors(
            embedder.model.name, [(pid, vector.tobytes()) for (pid, _), vector in zip(batch, vectors, strict=True)]
        )
    return len(pending)


def match_query(query: str) -> str:
    """An FTS5 query for the words that carry meaning, any of them."""

    terms = [term for term in re.findall(r"\w+", query.lower()) if term not in STOPWORDS]
    return " OR ".join(f'"{term}"' for term in dict.fromkeys(terms))


def _fused(*rankings: Sequence[int]) -> list[int]:
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, passage_id in enumerate(ranking, start=1):
            scores[passage_id] += 1 / (RRF_K + rank)
    return sorted(scores, key=lambda passage_id: scores[passage_id], reverse=True)


def search(store: Store, assistant_id: str, query: str, embedder: Embedder, top: int = TOP) -> list[dict[str, Any]]:
    """The passages that best answer ``query``: keywords and vectors, merged by reciprocal rank fusion.

    A passage only the vectors found, scoring under the model's floor, is left out: it
    matched nothing the question says. Whether what is left answers the question is the
    model's to judge, as the grounding clause asks it to.
    """

    import numpy as np

    keywords = store.keyword_search(assistant_id, match_query(query), DEPTH)
    stored = store.vectors(assistant_id, embedder.model.name)
    near: list[int] = []
    cosine: dict[int, float] = {}
    if stored:
        ids = [passage_id for passage_id, _ in stored]
        matrix = np.vstack([np.frombuffer(blob, dtype=np.float32) for _, blob in stored])
        scores = matrix @ embedder.embed([query])[0]
        order = np.argsort(-scores)[:DEPTH]
        near = [ids[i] for i in order]
        cosine = {ids[i]: float(scores[i]) for i in order}
    found = set(keywords)
    kept = [pid for pid in _fused(keywords, near) if pid in found or cosine.get(pid, 0.0) >= embedder.model.floor]
    chosen = kept[:top]
    by_id = {passage["id"]: passage for passage in store.passages(assistant_id, chosen)}
    return [{**by_id[pid], "cosine": round(cosine.get(pid, 0.0), 3)} for pid in chosen if pid in by_id]


def cited(text: str) -> list[int]:
    """The passages an answer cites, ``[P12]``, once each, in the order it cites them."""
    return list(dict.fromkeys(int(number) for number in CITATION.findall(text)))


def passage_text(passage: dict[str, Any]) -> str:
    """A passage as the model reads it in a search result: its number, its source and its text."""
    where = f"{passage['document']} › {passage['heading']}" + (f", page {passage['page']}" if passage["page"] else "")
    return f"[P{passage['id']}] {where}\n{passage['body']}"


def whole_text(passages: Sequence[dict[str, Any]]) -> str:
    """Every passage of a collection as the prompt carries it: by document and section, each numbered.

    A later window of a section repeats the :data:`OVERLAP` words before it; here that
    repeat is left out, so a section reads once, end to end, with its passages' numbers in it.
    """

    lines: list[str] = []
    document = section = None
    for passage in passages:
        if passage["document"] != document:
            document, section = passage["document"], None
            lines.append(f"\n### Document: {passage['document']} ({passage['title']})")
        if (passage["heading"], passage["page"]) != section:
            section = (passage["heading"], passage["page"])
            lines.append(f"\n#### {passage['heading']}")
        body = passage["body"]
        if passage["seq"] > 0 and not body.startswith("|"):
            body = " ".join(body.split()[OVERLAP:])
        lines.append(f"[P{passage['id']}] {body}")
    return "\n".join(lines).strip()
