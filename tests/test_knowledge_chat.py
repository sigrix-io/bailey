"""Documents through the app: a solution's slots filled with its samples, a covered question answered with a citation, and every way a collection changes.

The routes are the page's whole surface, so these drive them as it does, with the provider
emulator and a stand-in for the search model (``fakes.FakeEmbedder``). Story S8's *done
when* runs end to end on the fixture solution's samples: a covered
question is answered citing the passage and its file, an uncovered one by saying the
documents do not cover it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import emulated_providers as emulator
import pytest
from fakes import FakeEmbedder, FakeKeychain
from starlette.testclient import TestClient

from bailey.keys import KeychainKeys
from bailey.knowledge import LOCAL_WHOLE_LIMIT, WHOLE_LIMIT, LocalEmbedder
from bailey.server import AppState, _embedder, build_app
from bailey.solutions import Skill, read_solution, zip_solution
from bailey.store import Store

TOKEN = "test-session-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEY = "sk-ant-api03-0123456789abcdefghij-WXYZ"
FIXTURE = Path(__file__).parent / "fixtures" / "shop-replies"
COVERED = "Can I return a book I did not like?"
UNCOVERED = "Do you offer gift wrapping?"


class FetchedOnRequest(FakeEmbedder):
    """The search model as a new machine has it: not here until the person asks for it."""

    def __init__(self) -> None:
        super().__init__()
        self.fetched = False

    def ready(self) -> bool:
        return self.fetched

    def download(self) -> None:
        self.fetched = True


@pytest.fixture(scope="module")
def provider_base() -> str:
    return emulator.serve_in_background()


@pytest.fixture(autouse=True)
def _fresh_requests() -> Iterator[None]:
    emulator.REQUESTS.clear()
    yield
    emulator.REQUESTS.clear()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    opened = Store(tmp_path / "bailey.db")
    yield opened
    opened.close()


@pytest.fixture
def embedder() -> FetchedOnRequest:
    return FetchedOnRequest()


@pytest.fixture
def client(store: Store, tmp_path: Path, provider_base: str, embedder: FetchedOnRequest) -> Iterator[TestClient]:
    state = AppState(
        token=TOKEN,
        store=store,
        keys=KeychainKeys(FakeKeychain()),  # type: ignore[arg-type]
        data_folder=tmp_path,
        base_urls={"anthropic": provider_base + "/anthropic", "openai-compatible": provider_base + "/local/v1"},
        embedder=embedder,
    )
    with TestClient(build_app(state), base_url="http://127.0.0.1:8123") as made:
        yield made


def _fixture_zip(**changes: Any) -> bytes:
    files = {path.relative_to(FIXTURE).as_posix(): path.read_bytes() for path in FIXTURE.rglob("*") if path.is_file()}
    made = read_solution(files)
    for key, value in changes.items():
        made = made.__class__(**{**made.__dict__, key: value})
    return zip_solution(made)


def _ready(client: TestClient, provider: str = "anthropic", content: bytes | None = None) -> str:
    settings: dict[str, Any] = {"provider": provider}
    if provider == "openai-compatible":
        settings |= {"address": "http://localhost:1/v1", "model": "qwen3:8b"}
    assert client.put("/api/settings", headers=AUTH, json=settings).status_code == 200
    if provider == "anthropic":
        assert client.put("/api/keys/anthropic", headers=AUTH, json={"key": KEY}).status_code == 200
    imported = client.post("/api/assistants/import", headers=AUTH, content=content or _fixture_zip())
    assert imported.status_code == 200, imported.text
    assistant_id = str(imported.json()["id"])
    assert client.post(f"/api/assistants/{assistant_id}/use", headers=AUTH).status_code == 200
    return assistant_id


def _samples(client: TestClient, assistant_id: str) -> dict[str, Any]:
    view: dict[str, Any] = {}
    for slot in ("policies", "faq", "catalog"):
        response = client.post(f"/api/assistants/{assistant_id}/knowledge/samples/{slot}", headers=AUTH)
        assert response.status_code == 200, response.text
        view = response.json()
    return view


def _ask(client: TestClient, assistant_id: str, text: str) -> tuple[str, list[dict[str, Any]]]:
    conversation = client.post("/api/conversations", headers=AUTH, json={"assistant": assistant_id}).json()["id"]
    response = client.post(f"/api/conversations/{conversation}/messages", headers=AUTH, json={"text": text})
    assert response.status_code == 200, response.text
    return conversation, [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def _text(events: list[dict[str, Any]]) -> str:
    return "".join(event["text"] for event in events if event["type"] == "text")


def test_an_imported_solution_asks_for_its_documents_and_offers_each_sample(client: TestClient) -> None:
    imported = client.post("/api/assistants/import", headers=AUTH, content=_fixture_zip())
    assert imported.json()["asks_for_documents"] is True
    panel = client.get(f"/api/assistants/{imported.json()['id']}/knowledge", headers=AUTH).json()
    assert [(slot["name"], slot["required"], slot["sample"], slot["document"]) for slot in panel["slots"]] == [
        ("policies", True, True, None),
        ("faq", False, True, None),
        ("catalog", False, True, None),
    ]
    assert (panel["mode"], panel["tokens"], panel["documents"]) == ("none", 0, [])


def test_on_the_samples_a_covered_question_cites_its_passage_and_an_uncovered_one_says_so(client: TestClient) -> None:
    assistant_id = _ready(client)
    panel = _samples(client, assistant_id)
    assert panel["mode"] == "whole" and 0 < panel["tokens"] < WHOLE_LIMIT, "the samples are small: sent whole"
    assert [slot["document"]["kind"] for slot in panel["slots"]] == ["sample", "sample", "sample"]

    conversation, events = _ask(client, assistant_id, COVERED)
    done = events[-1]
    assert done["type"] == "done"
    assert [(c["document"], c["heading"]) for c in done["citations"]] == [("policies.md", "Returns")]
    assert "return a book in the condition it arrived" in done["citations"][0]["text"]
    shown = client.get(f"/api/conversations/{conversation}", headers=AUTH).json()["messages"][-1]
    assert shown["citations"] == done["citations"], "the citation is stored with the answer"

    _, events = _ask(client, assistant_id, UNCOVERED)
    assert _text(events) == emulator.NOT_COVERED and events[-1]["citations"] == []


def test_a_slot_takes_the_persons_own_file_in_a_type_it_accepts(client: TestClient) -> None:
    assistant_id = _ready(client)
    _samples(client, assistant_id)
    url = f"/api/assistants/{assistant_id}/knowledge"
    mine = client.post(
        f"{url}?slot=policies&name=ours.md", headers=AUTH, content=b"# Ours\n\n## Returns\n\nWithin 14 days."
    )
    policies = mine.json()["slots"][0]["document"]
    assert (policies["name"], policies["kind"]) == ("ours.md", "slot"), "the sample was replaced"
    wrong = client.post(f"{url}?slot=catalog&name=books.md", headers=AUTH, content=b"# Books")
    assert wrong.status_code == 400 and wrong.json()["error"].endswith("Bailey takes it as csv.")
    assert client.post(f"{url}?slot=prices&name=p.csv", headers=AUTH, content=b"a,b").status_code == 404
    unreadable = client.post(f"{url}?name=scan.pdf", headers=AUTH, content=b"%PDF-1.4 not really")
    assert unreadable.status_code == 400 and "could not be read" in unreadable.json()["error"]


def test_a_document_of_ones_own_is_kept_and_deleting_it_removes_it(client: TestClient, store: Store) -> None:
    assistant_id = _ready(client)
    url = f"/api/assistants/{assistant_id}/knowledge"
    added = client.post(f"{url}?name=hours.md", headers=AUTH, content=b"# Opening hours\n\nWe open at nine.").json()
    (document,) = [d for d in added["documents"] if d["kind"] == "own"]
    assert document["name"] == "hours.md" and added["mode"] == "whole"
    removed = client.delete(f"{url}/{document['id']}", headers=AUTH)
    assert removed.status_code == 200 and all(d["kind"] != "own" for d in removed.json()["documents"])
    assert all(p["document_id"] != document["id"] for p in store.passages(assistant_id))
    assert client.delete(f"{url}/{document['id']}", headers=AUTH).status_code == 404


def test_a_collection_too_long_to_send_whole_waits_for_the_model_and_then_is_searched(
    client: TestClient, embedder: FetchedOnRequest
) -> None:
    assistant_id = _ready(client)
    _samples(client, assistant_id)
    filler = " ".join(f"note{n}" for n in range(WHOLE_LIMIT * 4))
    client.post(
        f"/api/assistants/{assistant_id}/knowledge?name=archive.md",
        headers=AUTH,
        content=f"# Archive\n\n{filler}".encode(),
    )
    panel = client.get(f"/api/assistants/{assistant_id}/knowledge", headers=AUTH).json()
    assert panel["mode"] == "search" and panel["model"]["ready"] is False
    assert (panel["model"]["label"], panel["model"]["source"]) == ("all-MiniLM-L6-v2", "Hugging Face")
    conversation = client.post("/api/conversations", headers=AUTH, json={"assistant": assistant_id}).json()["id"]
    refused = client.post(f"/api/conversations/{conversation}/messages", headers=AUTH, json={"text": COVERED})
    assert refused.status_code == 409 and "downloads once from Hugging Face" in refused.json()["error"]
    assert "about 90 MB" in refused.json()["error"], "the download is announced with its size before it starts"
    assert emulator.REQUESTS == [] and embedder.embedded == 0, "nothing was sent or embedded"

    assert client.post("/api/knowledge/model", headers=AUTH).json()["ready"] is True
    conversation, events = _ask(client, assistant_id, COVERED)
    (searched,) = [event["query"] for event in events if event["type"] == "search"]
    assert [(c["document"], c["heading"]) for c in events[-1]["citations"]] == [("policies.md", "Returns")]
    shown = client.get(f"/api/conversations/{conversation}", headers=AUTH).json()["messages"][-1]
    assert shown["searches"] == [searched], "the search is stored with the answer and drawn after a reload"


class FailingDownload(FetchedOnRequest):
    def download(self) -> None:
        raise OSError("Network is unreachable")


def test_a_download_that_fails_says_so_and_names_the_way_round_it(store: Store, tmp_path: Path) -> None:
    state = AppState(
        token=TOKEN,
        store=store,
        keys=KeychainKeys(FakeKeychain()),  # type: ignore[arg-type]
        data_folder=tmp_path,
        embedder=FailingDownload(),
    )
    with TestClient(build_app(state), base_url="http://127.0.0.1:8123") as client:
        failed = client.post("/api/knowledge/model", headers=AUTH)
    assert failed.status_code == 502
    assert failed.json()["error"].startswith("Bailey could not download all-MiniLM-L6-v2 from Hugging Face: OSError.")
    assert "BAILEY_SEARCH_MODEL" in failed.json()["error"]


def test_the_search_model_is_read_from_the_folder_bailey_search_model_names(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def made(folder: str | None) -> LocalEmbedder:
        if folder is None:
            monkeypatch.delenv("BAILEY_SEARCH_MODEL", raising=False)
        else:
            monkeypatch.setenv("BAILEY_SEARCH_MODEL", folder)
        state = AppState(
            token=TOKEN,
            store=store,
            keys=KeychainKeys(FakeKeychain()),  # type: ignore[arg-type]
            data_folder=tmp_path,
        )
        embedder = _embedder(state)
        assert isinstance(embedder, LocalEmbedder)
        return embedder

    named = made(str(tmp_path / "minilm"))
    assert named.folder == tmp_path / "minilm"
    fetched = made(None)
    assert fetched.folder is None and fetched.cache_dir == tmp_path / "models", "fetched into the data folder"


def test_a_local_server_takes_a_small_collection_whole_and_searches_a_larger_one(client: TestClient) -> None:
    assistant_id = _ready(client, provider="openai-compatible")
    panel = _samples(client, assistant_id)
    assert panel["tokens"] <= LOCAL_WHOLE_LIMIT and panel["mode"] == "whole", "no download for the samples"
    filler = " ".join(f"note{n}" for n in range(LOCAL_WHOLE_LIMIT * 4))
    client.post(
        f"/api/assistants/{assistant_id}/knowledge?name=archive.md",
        headers=AUTH,
        content=f"# Archive\n\n{filler}".encode(),
    )
    assert client.get(f"/api/assistants/{assistant_id}/knowledge", headers=AUTH).json()["mode"] == "search"


def test_answering_only_from_the_documents_is_set_in_the_form_and_reaches_the_prompt(client: TestClient) -> None:
    assistant_id = _ready(client)
    _samples(client, assistant_id)
    form = client.get(f"/api/assistants/{assistant_id}", headers=AUTH).json()
    assert form["grounded_only"] is False
    form["grounded_only"] = True
    assert client.put(f"/api/assistants/{assistant_id}", headers=AUTH, json=form).json()["grounded_only"] is True
    _ask(client, assistant_id, UNCOVERED)
    assert "Answer only from the documents" in json.dumps(emulator.REQUESTS[-1]["body"]["system"])


def test_the_sellers_reference_files_are_kept_for_search_and_not_sent_whole_twice(client: TestClient) -> None:
    files = {path.relative_to(FIXTURE).as_posix(): path.read_bytes() for path in FIXTURE.rglob("*") if path.is_file()}
    made = read_solution(files)
    guide = Skill(
        "tone", "Load it to set the tone.", "Be kind.", (("phrases.md", "# Phrases\n\nThank you for writing."),)
    )
    assistant_id = _ready(client, content=zip_solution(made.__class__(**{**made.__dict__, "skills": (guide,)})))
    panel = client.get(f"/api/assistants/{assistant_id}/knowledge", headers=AUTH).json()
    assert [(d["name"], d["kind"]) for d in panel["documents"]] == [("tone/references/phrases.md", "reference")]
    assert panel["mode"] == "none", "a reference alone sends nothing whole: it loads with its skill"
    _samples(client, assistant_id)
    _ask(client, assistant_id, COVERED)
    system = json.dumps(emulator.REQUESTS[-1]["body"]["system"])
    assert "Return" in system and "Thank you for writing" not in system


def test_deleting_an_assistant_deletes_its_documents(client: TestClient, store: Store) -> None:
    assistant_id = _ready(client)
    _samples(client, assistant_id)
    assert store.documents(assistant_id)
    assert client.delete(f"/api/assistants/{assistant_id}", headers=AUTH).status_code == 200
    assert store.documents(assistant_id) == [] and store.passages(assistant_id) == []
