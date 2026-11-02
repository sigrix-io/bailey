"""The real CrewAI, in an environment Bailey builds from PyPI, running the fixture's crew app.

Opt in with ``BAILEY_TEST_REAL_CREWAI=1``: it downloads about 250 MB the first time. Every
other app test runs the real runner with a stand-in for CrewAI (``tests/test_apps.py``);
this is what notices when CrewAI's own clients stop reading the key and model Bailey
gives the runner (``bailey.apps.model_variables``), or stop sending each step.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path

import emulated_providers as emulator
import pytest
from fakes import FakeKeychain
from starlette.testclient import TestClient
from test_apps import AUTH, KEY, NOTES, TOKEN, _events, _fixture_files, _zip

from bailey.keys import KeychainKeys
from bailey.server import AppState, build_app
from bailey.store import Store

pytestmark = pytest.mark.skipif(
    os.environ.get("BAILEY_TEST_REAL_CREWAI") != "1",
    reason="downloads CrewAI and its libraries (about 250 MB): BAILEY_TEST_REAL_CREWAI=1",
)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    store = Store(tmp_path / "bailey.db")
    base = emulator.serve_in_background()
    state = AppState(
        token=TOKEN,
        store=store,
        keys=KeychainKeys(FakeKeychain()),  # type: ignore[arg-type]
        data_folder=Path(os.environ.get("BAILEY_TEST_ENVIRONMENTS") or tmp_path),
        base_urls={"openai": base + "/openai/v1"},
    )
    with TestClient(build_app(state), base_url="http://127.0.0.1:8123") as made:
        yield made
    store.close()


def test_the_real_crewai_runs_the_app_on_the_persons_key(client: TestClient) -> None:
    emulator.REQUESTS.clear()
    assert client.put("/api/settings", headers=AUTH, json={"provider": "openai"}).status_code == 200
    assert client.put("/api/keys/openai", headers=AUTH, json={"key": KEY}).status_code == 200
    imported = client.post("/api/assistants/import", headers=AUTH, content=_zip(_fixture_files())).json()
    app = f"/api/assistants/{imported['id']}/apps/digest"
    panel = client.post(f"{app}/environment", headers=AUTH).json()
    deadline = time.monotonic() + 1800
    while panel["apps"][0]["environment"] == "preparing" and time.monotonic() < deadline:
        time.sleep(5)
        panel = client.get(f"/api/assistants/{imported['id']}/apps", headers=AUTH).json()
    assert panel["apps"][0]["environment"] == "ready", panel["apps"][0]["why"]
    streamed = client.post(f"{app}/postern/v0/stream", headers=AUTH, json={"inputs": {"prompt": NOTES}})
    events = _events(streamed.text)
    assert [name for name, _ in events] == ["start", "step", "step", "done"], streamed.text[-2000:]
    assert [payload["name"] for name, payload in events if name == "step"] == ["pick_highlights", "write_newsletter"]
    assert "Newsletter Writer did:" in events[-1][1]["output"]["value"]
    assert emulator.REQUESTS and {request["key"] for request in emulator.REQUESTS} == {KEY}
