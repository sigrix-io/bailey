"""The local index the spike measures: convert, split, embed, index and search, all on the machine.

    python pipeline.py <corpus> <workdir> [--model-dir DIR] [--model NAME]

The knowledge pipeline Bailey plans, cut to what has to be measured:

1. **Convert** every file to Markdown with MarkItDown. A PDF comes back as plain text with a form
   feed between pages and no headings, so it is split by page instead of by heading.
2. **Split** by heading, then into passages of at most `WORDS` words with `OVERLAP` words of
   overlap; a table keeps its header row in every passage. Each passage keeps its file, heading
   and page. The index is built twice: with each passage's text starting with its document's
   title and heading, and without, to measure what that prefix buys.
3. **Embed** with fastembed, from `--model-dir` when the model is already on disk, otherwise
   downloaded by fastembed on first use.
4. **Index** in one SQLite file: the passages (with each vector as a BLOB), FTS5 over their text,
   and a sqlite-vec `vec0` table over the vectors.
5. **Search** four ways for each of questions.json's questions: keywords (FTS5, bm25), vectors
   through sqlite-vec, the same vectors through numpy with no extension loaded, and keywords and
   vectors merged by reciprocal rank fusion.

Prints one JSON report and writes `passages.jsonl`, `vectors.npy` and `question_vectors.npy` to
the workdir for `lance_check.py`, which runs the same questions through LanceDB.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sqlite3
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

WORDS = 180
OVERLAP = 30
RRF_K = 60
DEPTH = 50
HERE = Path(__file__).parent
STOPWORDS = frozenset(
    "a an and are as at be but by can do does for from have how i if in is it its me my of on or our so than "
    "that the their them then there these this to too us was we what whats when where which who will with "
    "would you your".split()
)
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Passage:
    id: int
    file: str
    title: str
    heading: str
    page: int | None
    body: str

    def text(self, context: bool) -> str:
        return f"{self.title} › {self.heading}\n{self.body}" if context and self.heading else self.body

    def judged(self) -> str:
        """What a reader sees of the passage, its citation included: the text relevance is judged on."""
        return " ".join(f"{self.title} {self.heading} {self.body}".lower().split())


def is_table(lines: Sequence[str]) -> bool:
    return len(lines) > 2 and lines[0].startswith("|") and set(lines[1].replace("|", "").strip()) <= set("-: ")


def windows(text: str) -> list[str]:
    lines = [line for line in text.splitlines() if line.strip()]
    if is_table(lines):
        header, rows, out, chunk, size = lines[:2], lines[2:], [], [], 0
        for row in rows:
            if chunk and size + len(row.split()) > WORDS:
                out.append("\n".join(header + chunk))
                chunk, size = [], 0
            chunk.append(row)
            size += len(row.split())
        return [*out, "\n".join(header + chunk)] if chunk else out
    words = text.split()
    if len(words) <= WORDS:
        return [text.strip()]
    step = WORDS - OVERLAP
    return [" ".join(words[start : start + WORDS]) for start in range(0, len(words) - OVERLAP, step)]


def sections(markdown: str, is_pdf: bool) -> list[tuple[str, int | None, str]]:
    if is_pdf:
        pages = markdown.split("\x0c")
        return [(f"page {number}", number, page.strip()) for number, page in enumerate(pages, 1) if page.strip()]
    found: list[tuple[str, int | None, str]] = []
    heading, lines = "", []
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


def title_of(path: Path, markdown: str, is_pdf: bool) -> str:
    for line in markdown.splitlines():
        if is_pdf and line.strip():
            return line.strip()
        if match := HEADING.match(line):
            return match.group(2).strip()
    return path.stem.replace("_", " ")


def split(documents: list[tuple[Path, str]], corpus: Path) -> list[Passage]:
    passages: list[Passage] = []
    for path, markdown in documents:
        is_pdf = path.suffix.lower() == ".pdf"
        title = title_of(path, markdown, is_pdf)
        for heading, page, text in sections(markdown, is_pdf):
            for body in windows(text):
                passages.append(
                    Passage(len(passages) + 1, str(path.relative_to(corpus)), title, heading or title, page, body)
                )
    return passages


def convert(corpus: Path) -> tuple[list[tuple[Path, str]], dict[str, dict[str, float]]]:
    from markitdown import MarkItDown

    converter = MarkItDown()
    documents, by_type = [], {}
    for path in sorted(p for p in corpus.rglob("*") if p.is_file()):
        started = time.perf_counter()
        markdown = converter.convert(str(path)).text_content
        took = time.perf_counter() - started
        stats = by_type.setdefault(path.suffix.lower(), {"files": 0, "seconds": 0.0, "words": 0})
        stats["files"] += 1
        stats["seconds"] = round(stats["seconds"] + took, 3)
        stats["words"] += len(markdown.split())
        documents.append((path, markdown))
    return documents, by_type


def embedder(model: str, model_dir: str | None) -> tuple[Callable[[list[str]], np.ndarray], dict[str, Any]]:
    started = time.perf_counter()
    from fastembed import TextEmbedding

    kwargs = {"specific_model_path": model_dir} if model_dir else {}
    encoder = TextEmbedding(model, **kwargs)
    loaded = time.perf_counter() - started

    def embed(texts: list[str]) -> np.ndarray:
        return np.vstack(list(encoder.embed(texts, batch_size=64))).astype(np.float32)

    return embed, {"model": model, "from": model_dir or "fastembed's download", "load_s": round(loaded, 2)}


def load_vec(db: sqlite3.Connection) -> None:
    import sqlite_vec

    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)


def build_index(path: Path, passages: list[Passage], vectors: np.ndarray, model: str, context: bool) -> dict[str, Any]:
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    started = time.perf_counter()
    db.executescript(
        """
        CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE passages(id INTEGER PRIMARY KEY, file TEXT, title TEXT, heading TEXT, page INTEGER,
                              text TEXT, vector BLOB);
        CREATE VIRTUAL TABLE passages_fts USING fts5(text, content='passages', content_rowid='id',
                                                     tokenize='porter unicode61 remove_diacritics 2');
        """
    )
    # The model is stored with the vectors, so changing it re-indexes rather than mixing two.
    db.executemany(
        "INSERT INTO meta VALUES (?, ?)", [("embedding_model", model), ("dimensions", str(vectors.shape[1]))]
    )
    db.executemany(
        "INSERT INTO passages VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (p.id, p.file, p.title, p.heading, p.page, p.text(context), v.tobytes())
            for p, v in zip(passages, vectors, strict=True)
        ],
    )
    db.execute("INSERT INTO passages_fts(rowid, text) SELECT id, text FROM passages")
    db.commit()
    keyword_s = time.perf_counter() - started
    started = time.perf_counter()
    load_vec(db)
    db.execute(
        f"CREATE VIRTUAL TABLE passages_vec USING vec0(embedding float[{vectors.shape[1]}] distance_metric=cosine)"
    )
    db.executemany(
        "INSERT INTO passages_vec(rowid, embedding) VALUES (?, ?)",
        [(p.id, v.tobytes()) for p, v in zip(passages, vectors, strict=True)],
    )
    db.commit()
    vec_s = time.perf_counter() - started
    db.close()
    return {
        "passages_and_fts_s": round(keyword_s, 2),
        "vec0_s": round(vec_s, 2),
        "file_mb": round(path.stat().st_size / 1e6, 1),
    }


def fts_query(question: str) -> str:
    terms = [t for t in re.findall(r"\w+", question.lower()) if t not in STOPWORDS]
    return " OR ".join(f'"{term}"' for term in terms)


def search_keywords(db: sqlite3.Connection, question: str) -> list[int]:
    sql = "SELECT rowid FROM passages_fts WHERE passages_fts MATCH ? ORDER BY bm25(passages_fts) LIMIT ?"
    return [row[0] for row in db.execute(sql, (fts_query(question), DEPTH))]


def search_vec(db: sqlite3.Connection, vector: np.ndarray) -> list[int]:
    sql = "SELECT rowid FROM passages_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance"
    return [row[0] for row in db.execute(sql, (vector.tobytes(), DEPTH))]


def numpy_vectors(db: sqlite3.Connection) -> tuple[np.ndarray, list[int]]:
    """Every vector, read from its BLOB with no extension loaded."""
    rows = db.execute("SELECT id, vector FROM passages ORDER BY id").fetchall()
    return np.vstack([np.frombuffer(blob, dtype=np.float32) for _, blob in rows]), [pid for pid, _ in rows]


def search_numpy(matrix: np.ndarray, ids: list[int], vector: np.ndarray) -> list[int]:
    scores = matrix @ vector
    top = np.argpartition(-scores, DEPTH)[:DEPTH]
    return [ids[i] for i in top[np.argsort(-scores[top])]]


def fuse(*rankings: list[int]) -> list[int]:
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, pid in enumerate(ranking, 1):
            scores[pid] += 1 / (RRF_K + rank)
    return sorted(scores, key=lambda pid: scores[pid], reverse=True)[:DEPTH]


def first_hit(ranking: list[int], relevant: set[int]) -> int | None:
    return next((rank for rank, pid in enumerate(ranking, 1) if pid in relevant), None)


def metrics(ranks: list[int | None]) -> dict[str, float]:
    return {
        "hit@1": sum(r == 1 for r in ranks if r) / len(ranks),
        "hit@3": sum(r <= 3 for r in ranks if r) / len(ranks),
        "hit@5": sum(r <= 5 for r in ranks if r) / len(ranks),
        "mrr@10": round(sum(1 / r for r in ranks if r and r <= 10) / len(ranks), 3),
    }


def evaluate(
    db_path: Path, passages: list[Passage], embed: Callable[[list[str]], np.ndarray], questions: dict[str, Any]
) -> dict[str, Any]:
    db = sqlite3.connect(db_path)
    load_vec(db)
    plain = sqlite3.connect(db_path)  # no extension: the degraded path
    matrix, ids = numpy_vectors(plain)
    answerable = questions["answerable"]
    question_vectors = embed([q["question"] for q in answerable + questions["unanswerable"]])
    methods: dict[str, Callable[[str, np.ndarray], list[int]]] = {
        "keywords": lambda q, v: search_keywords(plain, q),
        "vectors_sqlite_vec": lambda q, v: search_vec(db, v),
        "vectors_numpy": lambda q, v: search_numpy(matrix, ids, v),
        "hybrid_rrf": lambda q, v: fuse(search_keywords(plain, q), search_numpy(matrix, ids, v)),
    }
    ranks: dict[str, dict[str, int | None]] = {name: {} for name in methods}
    latency: dict[str, float] = {}
    for name, method in methods.items():
        timings = []
        for question, vector in zip(answerable, question_vectors, strict=False):
            relevant = {p.id for p in passages if all(s in p.judged() for s in question["must_contain"])}
            started = time.perf_counter()
            ranking = method(question["question"], vector)
            timings.append(time.perf_counter() - started)
            ranks[name][question["id"]] = first_hit(ranking, relevant)
        latency[name] = round(statistics.median(timings) * 1000, 2)
    top_similarity = {
        q["id"]: round(float(np.max(matrix @ v)), 3)
        for q, v in zip(answerable + questions["unanswerable"], question_vectors, strict=True)
    }
    vec_matches_numpy = all(
        search_vec(db, v)[:10] == search_numpy(matrix, ids, v)[:10] for v in question_vectors[: len(answerable)]
    )
    return {
        "metrics": {name: metrics(list(r.values())) for name, r in ranks.items()},
        "first_relevant_rank": ranks,
        "median_query_ms": latency,
        "sqlite_vec_and_numpy_agree_on_top10": vec_matches_numpy,
        "top_cosine_similarity": top_similarity,
        "relevant_passages": {
            q["id"]: sum(all(s in p.judged() for s in q["must_contain"]) for p in passages) for q in answerable
        },
    }


def scale(sizes: Sequence[int] = (10_000, 50_000, 100_000), dim: int = 384, queries: int = 20) -> dict[str, Any]:
    """Exact top-k over random unit vectors: sqlite-vec's vec0 against numpy, at the sizes a business corpus reaches."""
    rng = np.random.default_rng(0)
    results = {}
    for size in sizes:
        data = rng.standard_normal((size, dim)).astype(np.float32)
        data /= np.linalg.norm(data, axis=1, keepdims=True)
        probes = data[rng.choice(size, queries, replace=False)]
        db = sqlite3.connect(":memory:")
        load_vec(db)
        db.execute(f"CREATE VIRTUAL TABLE v USING vec0(embedding float[{dim}] distance_metric=cosine)")
        db.executemany(
            "INSERT INTO v(rowid, embedding) VALUES (?, ?)", ((i + 1, row.tobytes()) for i, row in enumerate(data))
        )
        db.execute("CREATE TABLE blobs(id INTEGER PRIMARY KEY, vector BLOB)")
        db.executemany("INSERT INTO blobs VALUES (?, ?)", ((i + 1, row.tobytes()) for i, row in enumerate(data)))
        vec_ms = []
        for probe in probes:
            started = time.perf_counter()
            db.execute(
                "SELECT rowid FROM v WHERE embedding MATCH ? AND k = 20 ORDER BY distance", (probe.tobytes(),)
            ).fetchall()
            vec_ms.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        rows = db.execute("SELECT vector FROM blobs ORDER BY id").fetchall()
        loaded = np.vstack([np.frombuffer(blob, dtype=np.float32) for (blob,) in rows])
        load_ms = (time.perf_counter() - started) * 1000
        numpy_ms = []
        for probe in probes:
            started = time.perf_counter()
            scores = loaded @ probe
            np.argpartition(-scores, 20)[:20]
            numpy_ms.append((time.perf_counter() - started) * 1000)
        results[str(size)] = {
            "sqlite_vec_query_ms": round(statistics.median(vec_ms), 2),
            "numpy_query_ms": round(statistics.median(numpy_ms), 2),
            "numpy_load_from_sqlite_ms": round(load_ms, 1),
            "numpy_memory_mb": round(loaded.nbytes / 1e6, 1),
        }
        db.close()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("corpus", type=Path)
    parser.add_argument("workdir", type=Path)
    parser.add_argument("--model", default="sentence-transformers/all-MiniLM-L6-v2")
    parser.add_argument("--model-dir", help="a directory holding the model's ONNX and tokenizer files")
    parser.add_argument("--no-scale", action="store_true", help="skip the synthetic scale test")
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    questions = json.loads((HERE / "questions.json").read_text(encoding="utf-8"))
    report: dict[str, Any] = {
        "versions": {
            n: importlib.metadata.version(n) for n in ("markitdown", "fastembed", "onnxruntime", "sqlite-vec", "numpy")
        },
        "sqlite": sqlite3.sqlite_version,
        "python": sys.version.split()[0],
    }

    started = time.perf_counter()
    documents, by_type = convert(args.corpus)
    report["convert"] = {"s": round(time.perf_counter() - started, 2), "by_type": by_type}
    words = sum(len(markdown.split()) for _, markdown in documents)
    report["corpus"] = {"files": len(documents), "words": words, "pages_at_500_words": round(words / 500)}

    started = time.perf_counter()
    passages = split(documents, args.corpus)
    report["split"] = {
        "s": round(time.perf_counter() - started, 3),
        "passages": len(passages),
        "max_words": WORDS,
        "overlap": OVERLAP,
    }

    embed, report["model"] = embedder(args.model, args.model_dir)
    report["variants"] = {}
    for context in (True, False):
        started = time.perf_counter()
        vectors = embed([p.text(context) for p in passages])
        embed_s = time.perf_counter() - started
        name = "with_title_and_heading" if context else "body_only"
        db_path = args.workdir / f"index_{name}.sqlite"
        variant = {
            "embed_s": round(embed_s, 1),
            "passages_per_s": round(len(passages) / embed_s, 1),
            "index": build_index(db_path, passages, vectors, args.model, context),
        }
        variant |= evaluate(db_path, passages, embed, questions)
        report["variants"][name] = variant
        if context:
            with (args.workdir / "passages.jsonl").open("w", encoding="utf-8") as out:
                for p in passages:
                    out.write(json.dumps({"id": p.id, "text": p.text(True), "judged": p.judged()}) + "\n")
            np.save(args.workdir / "vectors.npy", vectors)
            all_questions = questions["answerable"] + questions["unanswerable"]
            np.save(args.workdir / "question_vectors.npy", embed([q["question"] for q in all_questions]))
    if not args.no_scale:
        report["scale"] = scale()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
