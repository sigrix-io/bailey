"""Knowledge: a document converted, split, kept, indexed and searched, all on this computer.

The pipeline is S1's, ported from ``spike/s1/index/pipeline.py``. These tests run it on the
fixture solution's samples (``tests/fixtures/shop-replies``) with a stand-in embedder
(``fakes.FakeEmbedder``), so no test downloads a model; one test runs the real model where
one is on the machine or can be fetched.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeEmbedder

from bailey import knowledge
from bailey.knowledge import (
    LOCAL_WHOLE_LIMIT,
    MINILM,
    OVERLAP,
    WHOLE_LIMIT,
    WORDS,
    DocumentRefused,
    LocalEmbedder,
    add_document,
    cited,
    collection_tokens,
    convert,
    index,
    match_query,
    mode,
    search,
    split,
    whole_text,
)
from bailey.store import Store

SAMPLES = Path(__file__).parent / "fixtures" / "shop-replies" / "samples"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    opened = Store(tmp_path / "bailey.db")
    yield opened
    opened.close()


#: Each table that holds a document's text, counted as it stands.
COUNTS = {
    "passages": "SELECT count(*) FROM passages",
    "passages_fts": "SELECT count(*) FROM passages_fts",
}


def _rows(store: Store, table: str) -> int:
    """A table's rows, read apart from the store. Its own reads join a passage to its document and an
    index row to its passage, so they cannot see a row left behind, which still holds a document's text."""

    with closing(sqlite3.connect(store.path)) as reader:
        return int(reader.execute(COUNTS[table]).fetchone()[0])


def _fill(store: Store, assistant_id: str = "a1") -> None:
    for sample in sorted(SAMPLES.iterdir()):
        add_document(store, assistant_id, sample.name, sample.read_bytes(), slot=sample.stem, kind="sample")


# ---------------------------------------------------------------------------
# Convert and split
# ---------------------------------------------------------------------------


def test_text_goes_in_as_it_is_a_csv_as_a_table_and_html_through_markitdown() -> None:
    assert convert("faq.md", (SAMPLES / "faq.md").read_bytes()).startswith("# Questions we are asked")
    table = convert("catalog.csv", (SAMPLES / "catalog.csv").read_bytes()).splitlines()
    assert table[:3] == [
        "| title | author | price_gbp | in_stock |",
        "| --- | --- | --- | --- |",
        "| The Lighthouse Keeper | Mara Quinn | 9.99 | yes |",
    ]
    html = b"<html><body><h1>Shipping</h1><p>Orders ship in 2 days.</p></body></html>"
    assert convert("shipping.html", html).splitlines()[0] == "# Shipping"
    assert '"price": 12' in convert("prices.json", b'{"price": 12}')


@pytest.mark.parametrize(
    ("name", "data", "sentence"),
    [
        ("slides.key", b"x", "and this is a .key file"),
        ("policies.md", b"\xff\xfe broken", "is not UTF-8 text"),
        ("prices.json", b"{not json", "is not JSON"),
        ("scan.pdf", b"%PDF-1.4 not really", "could not be read"),
    ],
    ids=["unknown-type", "not-utf8", "not-json", "damaged-pdf"],
)
def test_a_document_bailey_cannot_read_is_refused_with_a_sentence(name: str, data: bytes, sentence: str) -> None:
    with pytest.raises(DocumentRefused, match=sentence):
        convert(name, data)


def test_a_document_too_large_to_keep_is_refused_with_its_size(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge, "MAX_DOCUMENT_TOKENS", 100)
    long = ("# Long\n\n" + " ".join(f"word{n}" for n in range(400))).encode()
    with pytest.raises(DocumentRefused, match=r"^long\.md is about [\d,]+ tokens; Bailey keeps documents up to 100\.$"):
        add_document(store, "a1", "long.md", long)
    assert store.documents("a1") == [] and _rows(store, "passages") == 0, "nothing of it is kept"
    monkeypatch.setattr(knowledge, "MAX_DOCUMENT_BYTES", 1024 * 1024)
    with pytest.raises(DocumentRefused, match=r"^big\.md is over 1 MB\.$"):
        convert("big.md", b"x" * (1024 * 1024 + 1))


def test_a_document_splits_by_heading_into_passages_that_keep_their_heading_and_open_with_the_title() -> None:
    title, passages = split("policies.md", convert("policies.md", (SAMPLES / "policies.md").read_bytes()))
    assert title == "Harbour Lane Books: shop policies"
    assert [p.heading for p in passages][:4] == [
        "Harbour Lane Books: shop policies",
        "Shipping",
        "Returns",
        "Damaged or wrong items",
    ]
    returns = next(p for p in passages if p.heading == "Returns")
    assert returns.text(title).startswith("Harbour Lane Books: shop policies › Returns\n")


def test_a_short_section_keeps_its_lines_as_written() -> None:
    _, passages = split("hours.md", "# Hours\n\n- Monday to Friday: 9 to 5\n- Saturday: 10 to 4\n- Sunday: closed\n")
    assert [p.body for p in passages] == ["- Monday to Friday: 9 to 5\n- Saturday: 10 to 4\n- Sunday: closed"]


def test_a_long_section_splits_into_overlapping_passages_and_a_table_keeps_its_header() -> None:
    words = [f"word{n}" for n in range(400)]
    _, passages = split("long.md", "# Long\n\n" + " ".join(words))
    assert all(len(p.body.split()) <= WORDS for p in passages)
    assert passages[1].body.split()[:OVERLAP] == passages[0].body.split()[-OVERLAP:], "the next one repeats the overlap"
    rows = "\n".join(f"| item {n} | {' '.join(['detail'] * 20)} |" for n in range(30))
    _, table = split("t.md", "# Table\n\n| item | details |\n| --- | --- |\n" + rows)
    assert len(table) > 1 and all(p.body.startswith("| item | details |\n| --- | --- |") for p in table)


def test_a_pdf_splits_by_page_so_a_citation_can_name_it() -> None:
    _, passages = split("warranty.pdf", "Warranty\fCover lasts two years.\fClaims go by email.")
    assert [(p.heading, p.page) for p in passages] == [("page 1", 1), ("page 2", 2), ("page 3", 3)]


def test_whole_text_reads_each_section_once_with_its_passages_numbered(store: Store) -> None:
    words = " ".join(f"word{n}" for n in range(400))
    add_document(store, "a1", "long.md", f"# Long\n\n{words}".encode())
    passages = store.passages("a1")
    text = whole_text(passages)
    assert text.count("#### Long") == 1
    assert all(f"[P{p['id']}]" in text for p in passages)
    assert [w for w in text.split() if w.startswith("word")] == words.split(), "the overlap is read once"


# ---------------------------------------------------------------------------
# The collection
# ---------------------------------------------------------------------------


def test_a_slot_takes_one_document_and_deleting_one_deletes_its_passages(store: Store) -> None:
    _fill(store)
    first = store.documents("a1")
    assert [(d["slot"], d["kind"]) for d in first] == [("catalog", "sample"), ("faq", "sample"), ("policies", "sample")]
    mine = add_document(
        store, "a1", "my-policies.md", b"# Our policies\n\n## Returns\n\nWithin 14 days.", slot="policies", kind="slot"
    )
    after = store.documents("a1")
    assert [d["name"] for d in after if d["slot"] == "policies"] == ["my-policies.md"], "the slot's sample was replaced"
    assert store.keyword_search("a1", '"bristol"', 10) == [], (
        "and its passages went with it, from the keyword index too"
    )
    assert store.delete_document("a1", mine["id"]) is True
    assert all(p["document_id"] != mine["id"] for p in store.passages("a1"))
    assert store.keyword_search("a1", '"within"', 10) == []
    kept = len(store.passages("a1"))
    assert _rows(store, "passages") == _rows(store, "passages_fts") == kept, "no text of a deleted document stays"
    assert store.delete_document("a1", mine["id"]) is False


def test_deleting_an_assistant_deletes_its_documents_and_leaves_anothers(store: Store) -> None:
    _fill(store, "a1")
    _fill(store, "a2")
    store.put_assistant("a1", {"format": "bailey-assistant/1"})
    store.delete_assistant("a1")
    assert store.documents("a1") == [] and store.passages("a1") == []
    assert store.keyword_search("a1", '"returns"', 10) == []
    assert store.documents("a2") and store.keyword_search("a2", '"returns"', 10)
    kept = len(store.passages("a2"))
    assert _rows(store, "passages") == _rows(store, "passages_fts") == kept, "no text of the deleted assistant's stays"


def test_the_documents_go_whole_on_a_provider_that_caches_and_are_searched_above_the_limit_or_locally() -> None:
    assert mode(0, "anthropic") == "none"
    assert mode(WHOLE_LIMIT, "anthropic") == "whole" and mode(WHOLE_LIMIT, "openai") == "whole"
    assert mode(WHOLE_LIMIT + 1, "anthropic") == "search"
    assert mode(LOCAL_WHOLE_LIMIT, "openai-compatible") == "whole", "a small one fits even a small window"
    assert mode(LOCAL_WHOLE_LIMIT + 1, "openai-compatible") == "search", (
        "a local server's window is not Bailey's to know"
    )


def test_the_fixture_samples_are_small_enough_to_go_whole(store: Store) -> None:
    _fill(store)
    assert 0 < collection_tokens(store, "a1") < WHOLE_LIMIT


# ---------------------------------------------------------------------------
# Index and search
# ---------------------------------------------------------------------------


def test_a_search_finds_the_passage_that_answers_first(store: Store) -> None:
    _fill(store)
    embedder = FakeEmbedder()
    assert index(store, "a1", embedder) == len(store.passages("a1"))
    hits = search(store, "a1", "return a book I did not like refund", embedder)
    assert hits[0]["heading"] == "Returns" and hits[0]["document"] == "policies.md"
    assert index(store, "a1", embedder) == 0, "a passage with this model's vector is not embedded again"


class Synonyms(FakeEmbedder):
    """The stand-in, told that some words mean others, as the real model knows without being told."""

    def __init__(self, meaning: dict[str, str]) -> None:
        super().__init__()
        self.meaning = meaning

    def embed(self, texts: Any) -> Any:
        return super().embed([" ".join(self.meaning.get(word, word) for word in text.split()) for text in texts])


def test_a_passage_no_keyword_reaches_is_found_by_what_it_means(store: Store) -> None:
    care = b"# Care\n\n## Washing up\n\nKeep the mugs out of the dishwasher.\n\n## Hours\n\nWe open at nine."
    add_document(store, "a1", "care.md", care)
    embedder = Synonyms({"cups": "mugs", "cleaning": "dishwasher"})
    index(store, "a1", embedder)
    assert store.keyword_search("a1", match_query("cups cleaning"), 10) == [], "no word of the question is in it"
    assert [hit["heading"] for hit in search(store, "a1", "cups cleaning", embedder)] == ["Washing up"]


def test_a_passage_only_the_vectors_found_under_the_floor_is_left_out(store: Store) -> None:
    _fill(store)
    embedder = FakeEmbedder(floor=0.99)
    index(store, "a1", embedder)
    assert search(store, "a1", "zzz qqq", embedder) == [], "nothing the question says matched anything"
    keyword = search(store, "a1", "tracking link", embedder)
    assert keyword and all("tracking" in hit["text"].lower() for hit in keyword), "a keyword match is kept"


def test_one_assistants_search_never_reads_anothers_passages(store: Store) -> None:
    _fill(store, "a1")
    add_document(store, "a2", "theirs.md", b"# Returns\n\nOur zebra rule: return a book within a year.")
    embedder = FakeEmbedder()
    index(store, "a1", embedder)
    index(store, "a2", embedder)
    theirs = {passage["id"] for passage in store.passages("a2")}
    assert theirs and store.keyword_search("a2", '"zebra"', 10)
    assert store.keyword_search("a1", '"zebra"', 10) == [], "the keyword index answers for one assistant"
    assert not theirs & {passage_id for passage_id, _ in store.vectors("a1", embedder.model.name)}
    hits = search(store, "a1", "zebra rule return a book within a year", embedder, top=50)
    assert hits and not theirs & {hit["id"] for hit in hits}


def test_the_model_loads_with_nothing_fetched_until_the_person_asks_for_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fastembed

    asked: list[dict[str, Any]] = []

    class Recorded:
        def __init__(self, name: str, **options: Any) -> None:
            asked.append(options)
            if options.get("local_files_only"):
                raise ValueError("the model is not on this computer")

    monkeypatch.setattr(fastembed, "TextEmbedding", Recorded)
    monkeypatch.delenv("HF_HUB_DISABLE_TELEMETRY", raising=False)
    embedder = LocalEmbedder(tmp_path / "models")
    assert embedder.ready() is False
    assert asked == [{"cache_dir": str(tmp_path / "models"), "local_files_only": True}], "looked for, not fetched"
    assert os.environ.get("HF_HUB_DISABLE_TELEMETRY") == "1", "Hugging Face's telemetry is off before it loads"
    embedder.download()
    assert asked[-1] == {"cache_dir": str(tmp_path / "models"), "local_files_only": False}
    assert embedder.ready() is True and len(asked) == 2, "fetched once, and loaded from then on"


def test_a_different_model_indexes_again_rather_than_mixing_two(store: Store) -> None:
    _fill(store)
    first, second = FakeEmbedder("fake/one"), FakeEmbedder("fake/two")
    count = index(store, "a1", first)
    assert index(store, "a1", second) == count, "every passage again, under the new model's name"
    assert store.vectors("a1", "fake/one") == [] and len(store.vectors("a1", "fake/two")) == count


def test_an_answer_cites_passages_by_number() -> None:
    assert cited("Within 30 days [P12], prepaid [P3]; see [P12] again.") == [12, 3]
    assert cited("No passage covers that.") == []


def _real_model_folder() -> Path | None:
    named = os.environ.get("BAILEY_SEARCH_MODEL")
    return Path(named) if named else None


@pytest.mark.skipif(
    not os.environ.get("BAILEY_SEARCH_MODEL") and os.environ.get("BAILEY_TEST_REAL_EMBEDDINGS") != "1",
    reason="needs the search model on this machine (BAILEY_SEARCH_MODEL) or leave to fetch it (BAILEY_TEST_REAL_EMBEDDINGS=1)",
)
def test_the_real_model_finds_the_passage_a_model_would_search_for(store: Store, tmp_path: Path) -> None:
    embedder = LocalEmbedder(tmp_path / "models", folder=_real_model_folder())
    if not embedder.ready():
        embedder.download()
    assert embedder.model == MINILM
    _fill(store)
    index(store, "a1", embedder)
    assert search(store, "a1", "return a book refund policy", embedder)[0]["heading"] == "Returns"
    assert search(store, "a1", "how long does UK delivery take", embedder)[0]["heading"] == "Shipping"
    assert search(store, "a1", "collect an order from the shop", embedder)[0]["document"] == "faq.md"
