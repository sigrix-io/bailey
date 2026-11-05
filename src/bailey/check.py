"""``bailey check``: a solution's examples, and a grounding pair per knowledge slot, run headless in Bailey's own core.

Story S11. "Works with Bailey — verified" has to mean the solution
ran in Bailey's own runtime, so the check is not a second agent loop that could drift from
the first. It starts a Bailey of its own in a temporary folder and drives the app's own
routes in-process, over httpx's ASGI transport, with no socket:

- the solution arrives as it arrives in the app, through :mod:`bailey.install`'s checks;
- each knowledge slot gets its sample, as the Documents panel's "Use the sample" gives it;
- each case is a conversation of its own, through the agent core, the skills, the
  documents (whole or searched, as :mod:`bailey.knowledge` decides) and the tool host.

Nothing reaches the person's own assistants or conversations, and the folder goes with the run.

**The cases** (:func:`cases_of`) are each of the solution's examples, and for each knowledge
slot with a sample a grounding pair: a question the sample answers,
made from one of its passages, and one about a made-up name no document holds.

**The verdicts** (:func:`judge`) hold an answer to what Bailey can see in it, never to its
wording: that it answered; that it cites a passage (``[P12]``) or quotes one word for word;
that it says the documents do not cover the question. An example's own reply decides which of
those its answer must show, since a reply is a model of the answer, not its text.

**Write tools are refused, never run.** Each call that would ask the person is answered "no",
as a person's "no" is, and the case records it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import secrets
import sys
import tempfile
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import httpx
import keyring
from keyring.backend import KeyringBackend

from bailey import __version__
from bailey.install import SCRIPTS_SETTING, check_package, files_from_folder
from bailey.keys import (
    FILE_NAME,
    FORCE_FILE_VARIABLE,
    PASSPHRASE_VARIABLE,
    EncryptedFileKeys,
    KeychainKeys,
    WrongPassphrase,
    keychain_backend,
)
from bailey.knowledge import CITATION, Embedder, LocalEmbedder, convert, split
from bailey.paths import data_folder
from bailey.settings import Connection, SettingRefused, clean_address, key_name, provider
from bailey.solutions import (
    MAX_ENTRIES,
    MAX_FILE_BYTES,
    MAX_PACKAGE_BYTES,
    Assistant,
    KnowledgeSlot,
    Sample,
    SolutionRefused,
    unzip_solution,
)
from bailey.store import STORE_FILE, Store, read_settings
from bailey.tools import variable_key

#: The variable each provider's key is read from when Bailey has none saved, as a CI job gives one.
#: None for an OpenAI-compatible server: a key in OPENAI_API_KEY is OpenAI's, and goes to OpenAI alone.
KEY_VARIABLES = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}

#: Names for the question no document answers. The first that no sample holds is used.
MADE_UP_NAMES = ("Zephyrine", "Quillmark", "Orvanta", "Brindlewick", "Tamsworth", "Calvetta", "Yarrowmere")
#: What the made-up name is asked about, one for each slot in turn, so each slot's question is its own.
MADE_UP_THINGS = ("scheme", "club", "card", "programme", "pass", "plan", "fund", "award", "circle", "festival")

#: A quotation that counts as quoting a passage: this many words or more, found in a sample word for word.
QUOTE_WORDS = 4

#: How an answer says the documents do not cover a question, as models and solutions word it.
#: Held to a case table in the tests, as every word added to it should be.
NOT_COVERED = re.compile(
    r"\bnot covered\b"
    r"|\b(?:do|does|did)(?: not|n['’]t) (?:cover|mention|say|include|contain|address|answer|list|state)\b"
    r"|\b(?:is|are)(?: not|n['’]t) (?:covered|mentioned|in (?:the|your|our) documents)\b"
    r"|\bno (?:mention|information)\b"
    r"|\bnothing (?:in|about) (?:the|your|our) documents\b",
    re.IGNORECASE,
)

_QUOTED = re.compile(r'"([^"\n]+)"|“([^”\n]+)”')
_BASE = "http://127.0.0.1"
#: How long one request of the check's may take, a case's whole answer among them: a model on a
#: slow local server takes minutes.
CASE_TIMEOUT = 600.0
#: How many times a run that stops to ask is answered "no" before the case gives up.
MAX_REFUSALS = 8


class CheckFailed(Exception):
    """A check that cannot run, with the sentence that says why."""


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    """One message the check sends, and what its answer is held to."""

    #: ``example``, ``answered`` (a question a sample answers) or ``uncovered`` (one no document does).
    kind: str
    name: str
    message: str
    #: An example's reply, when its writer gave one.
    expected: str = ""
    #: A grounding case's slot.
    slot: str = ""


def cases_of(made: Assistant) -> list[Case]:
    """Each example, then a grounding pair for each slot with a sample."""

    cases = [
        Case("example", f"Example {index}", example.input.strip(), expected=example.output)
        for index, example in enumerate(made.examples, start=1)
    ]
    held = [sample.text.lower() for sample in made.samples]
    absent = next((name for name in MADE_UP_NAMES if all(name.lower() not in text for text in held)), "Zephyrine")
    with_samples = [(slot, sample) for slot in made.knowledge if (sample := made.sample(slot.name)) is not None]
    for index, (slot, sample) in enumerate(with_samples):
        thing = MADE_UP_THINGS[index % len(MADE_UP_THINGS)]
        cases.append(
            Case(
                "answered",
                f"{slot.name}: a question its sample answers",
                answered_question(slot, sample),
                slot=slot.name,
            )
        )
        cases.append(
            Case(
                "uncovered",
                f"{slot.name}: a question no document answers",
                f"Who runs the {absent} {thing}?",
                slot=slot.name,
            )
        )
    return cases


def answered_question(slot: KnowledgeSlot, sample: Sample) -> str:
    """A question the sample answers, made from one of its passages.

    In order: a section's heading, asked about, or as it stands when it is a question (a
    FAQ's); a table's first row, by the cell in a column that names it (:data:`NAMING_COLUMNS`)
    or its first cell that is not a number; the document's own title, when it has one; and
    failing all three, the opening words of its first passage, quoted.
    """

    label = slot.name.replace("_", " ")
    name = f"{slot.name}.{sample.extension}"
    title, passages = split(name, convert(name, sample.text.encode("utf-8")))
    for passage in passages:
        heading = passage.heading.strip()
        if heading and heading != title and not passage.body.lstrip().startswith("|"):
            return heading if heading.endswith("?") else _about(label, heading)
    for passage in passages:
        rows = [_cells(line) for line in passage.body.splitlines() if line.lstrip().startswith("|")]
        if len(rows) >= 3:
            header, first_row = rows[0], rows[2]
            named = [first_row[i] for i, column in enumerate(header) if column.lower() in NAMING_COLUMNS]
            plain = [cell for cell in first_row if not re.fullmatch(r"[\d.,%$£€-]*", cell)]
            subject = next((cell for cell in [*named, *plain] if cell), "")
            if subject:
                return f"What does your {label} document say about {subject}?"
    if title.strip() and title != Path(name).stem and passages:
        return _about(label, title.strip())
    for passage in passages:
        words = re.sub(r"^[-*+>#\s]+", "", passage.body).split()[:10]
        if words:
            opening = " ".join(words).rstrip(".,;:!?")
            return f'What does your {label} document say about "{opening}"?'
    return f"What does your {label} document say?"


def _about(label: str, subject: str) -> str:
    return f"What does your {label} document say about {subject[:1].lower() + subject[1:]}?"


#: The columns that name what a table's row is about, tried before its first cell that is not a number.
NAMING_COLUMNS = frozenset({"name", "title", "product", "item", "product name", "item name"})


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


# ---------------------------------------------------------------------------
# The verdicts
# ---------------------------------------------------------------------------


#: Typography a writer or a model may set differently from the document, read as plain marks.
_PLAIN_MARKS: dict[str, str | int | None] = {"’": "'", "‘": "'", "–": "-", "—": "-", "“": '"', "”": '"'}
_PLAIN = str.maketrans(_PLAIN_MARKS)


def _normal(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_PLAIN)
    return " ".join(text.split()).lower()


def quotes(text: str, sources: Sequence[str]) -> list[str]:
    """What an answer quotes word for word: each quotation of :data:`QUOTE_WORDS` words or more found in a source."""

    held = [_normal(source) for source in sources]
    found = []
    for match in _QUOTED.finditer(text):
        span = (match.group(1) or match.group(2)).strip()
        if len(span.split()) < QUOTE_WORDS:
            continue
        wanted = _normal(span).rstrip(".,;:!?")
        if wanted and any(wanted in source for source in held):
            found.append(span)
    return found


def says_not_covered(text: str) -> bool:
    return NOT_COVERED.search(text) is not None


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"check": self.name, "passed": self.passed, "reason": self.reason}


@dataclass
class Outcome:
    """What one case's run gave back."""

    answer: str = ""
    error: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    #: Each run's estimated cost, one for every answer the case took: a refusal resumes the run.
    estimates: list[dict[str, Any] | None] = field(default_factory=list)

    @property
    def estimate(self) -> dict[str, str] | None:
        return summed(self.estimates)


def summed(estimates: Sequence[dict[str, Any] | None]) -> dict[str, str] | None:
    """Estimates added up, or none when there is none to add or one is missing: a part priced is not a price."""

    if not estimates or any(not estimate or "usd" not in estimate for estimate in estimates):
        return None
    total = sum((Decimal(str(estimate["usd"])) for estimate in estimates if estimate), Decimal(0))
    return {"usd": str(total)}


def judge(case: Case, outcome: Outcome, made: Assistant) -> list[Check]:
    """The checks a case's answer is held to, each failing with a sentence that says why."""

    if outcome.error:
        return [Check("answered", False, f"The run failed: {outcome.error}")]
    if not outcome.answer.strip():
        return [Check("answered", False, "It gave no answer.")]
    checks = [Check("answered", True)]
    samples = {sample.slot: sample for sample in made.samples}
    every = [sample.text for sample in made.samples]
    cited = bool(outcome.citations)
    quoted = bool(quotes(outcome.answer, every))
    if case.kind == "example" and case.expected.strip():
        if every and quotes(case.expected, every):
            checks.append(
                Check(
                    "quotes",
                    quoted,
                    ""
                    if quoted
                    else "Its example quotes the documents word for word, and this answer quotes none of them.",
                )
            )
        elif every and CITATION.search(case.expected):
            checks.append(
                Check(
                    "cites",
                    cited or quoted,
                    "" if cited or quoted else "Its example cites the documents, and this answer cites none of them.",
                )
            )
        if says_not_covered(case.expected):
            covered = says_not_covered(outcome.answer)
            checks.append(
                Check(
                    "not covered",
                    covered,
                    ""
                    if covered
                    else "Its example says the documents do not cover this, and this answer does not say so.",
                )
            )
    elif case.kind == "answered":
        sample = samples[case.slot]
        document = f"{sample.slot}.{sample.extension}"
        relies = any(citation.get("document") == document for citation in outcome.citations) or bool(
            quotes(outcome.answer, [sample.text])
        )
        checks.append(
            Check(
                "cites its sample",
                relies,
                "" if relies else f"It neither cites nor quotes the {case.slot} sample, which answers this.",
            )
        )
    elif case.kind == "uncovered":
        # Held to saying so, and nothing more: a reply that says so may still cite a passage for
        # what it does say, as a holding reply cites how soon the team answers.
        covered = says_not_covered(outcome.answer)
        checks.append(
            Check("says so", covered, "" if covered else "It does not say that the documents do not cover this.")
        )
    return checks


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass
class Result:
    case: Case
    outcome: Outcome
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.case.kind,
            "name": self.case.name,
            "message": self.case.message,
            "slot": self.case.slot or None,
            "passed": self.passed,
            "checks": [check.as_dict() for check in self.checks],
            "answer": self.outcome.answer,
            "error": self.outcome.error or None,
            "citations": self.outcome.citations,
            "skills": self.outcome.skills,
            "searches": self.outcome.searches,
            "tools": self.outcome.tools,
            "refused": self.outcome.refused,
            "usage": self.outcome.usage,
            "estimate": self.outcome.estimate,
        }


@dataclass
class Report:
    title: str
    name: str
    provider: str
    model: str
    documents: str
    results: list[Result]

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def usage(self) -> dict[str, int]:
        """The tokens every case took, added up."""
        total: dict[str, int] = {}
        for result in self.results:
            for key, value in result.outcome.usage.items():
                total[key] = total.get(key, 0) + value
        return total

    @property
    def estimate(self) -> dict[str, str] | None:
        """The run's estimated cost, when every answer it took has one."""
        return summed([estimate for result in self.results for estimate in result.outcome.estimates])

    def as_dict(self) -> dict[str, Any]:
        return {
            "solution": {"name": self.name, "title": self.title},
            "bailey": __version__,
            "provider": self.provider,
            "model": self.model,
            "documents": self.documents,
            "passed": self.passed,
            "usage": self.usage,
            "estimate": self.estimate,
            "cases": [result.as_dict() for result in self.results],
        }


_DOCUMENTS = {"whole": "its documents sent whole", "search": "its documents searched"}
#: How much of an example's message its line shows: the case is named by its number, and the JSON has it whole.
PREVIEW = 72


def opening(title: str, provider: str, model: str, documents: str) -> str:
    """What the check prints before its first case: what it checks, on what, and how its documents go."""
    ways = _DOCUMENTS.get(documents)
    return f"Checking {title} on {provider} {model}" + (f", {ways}." if ways else ".") + "\n\n"


def _shown(case: Case) -> str:
    if case.kind != "example":
        return case.name
    preview = " ".join(case.message.split())
    if len(preview) > PREVIEW:
        preview = preview[: PREVIEW - 1].rstrip() + "…"
    return f"{case.name}: {preview}"


def result_text(result: Result) -> str:
    """A case's line, PASS or FAIL and its name, then why each of its checks that failed did."""
    lines = [("PASS  " if result.passed else "FAIL  ") + _shown(result.case)]
    lines.extend(f"      {check.reason}" for check in result.checks if not check.passed)
    if result.outcome.refused:
        lines.append(f"      Refused {', '.join(result.outcome.refused)}, as a check refuses every write.")
    return "\n".join(lines) + "\n"


def closing(report: Report) -> str:
    """What the check prints after its last case: how many failed, and what the run took."""
    failed = sum(not result.passed for result in report.results)
    total = len(report.results)
    lines = ["", f"All {total} cases passed." if not failed else f"{failed} of {total} cases failed."]
    usage = report.usage
    if usage:
        took = f"It took {usage.get('input_tokens', 0):,} tokens in, {usage.get('output_tokens', 0):,} out"
        estimate = report.estimate
        lines.append(
            took
            + (
                f", about ${Decimal(estimate['usd']):.4f} (an estimate)."
                if estimate
                else ", with no price estimate for this model."
            )
        )
    return "\n".join(lines) + "\n"


def render(report: Report) -> str:
    """The report as a person reads it: one line per case, and why a failing one failed."""
    return (
        opening(report.title, report.provider, report.model, report.documents)
        + "".join(result_text(result) for result in report.results)
        + closing(report)
    )


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Setup:
    """What the check runs on: the provider, the model and its key, and the installing setting."""

    connection: Connection
    scripts_allowed: bool = False


class _HeldKeys:
    """The check's own keys, held in memory for its run and never written.

    A plain class, not a ``KeyringBackend``: keyring registers every subclass of that, and its
    own detection could then pick this one.
    """

    def __init__(self) -> None:
        self._held: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._held.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._held[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._held.pop((service, username), None)


def setup_for(
    folder: Path,
    *,
    provider_id: str | None = None,
    model: str | None = None,
    address: str | None = None,
    environ: Mapping[str, str] = os.environ,
) -> Setup:
    """The person's provider, model and key from Bailey's settings, or as the options name them.

    Read without writing anything: the store only where Bailey made one, and the key from the
    keychain, or from a key file the passphrase in ``BAILEY_PASSPHRASE`` opens, or from the
    provider's own variable (:data:`KEY_VARIABLES`).
    """

    saved = read_settings(folder / STORE_FILE) if (folder / STORE_FILE).exists() else {}
    chosen = provider_id or saved.get("provider")
    if not chosen:
        raise CheckFailed("Choose an AI provider: in Bailey's Settings, or with --provider.")
    try:
        setup = provider(chosen)
        named = clean_address(address) if address else str(saved.get("address") or "")
    except SettingRefused as refusal:
        raise CheckFailed(str(refusal)) from refusal
    models = saved.get("models") if isinstance(saved.get("models"), dict) else {}
    model_name = model or str((models or {}).get(setup.id) or "") or setup.default_model
    if not model_name:
        raise CheckFailed("Name the model: in Bailey's Settings, or with --model.")
    base_url = setup.base_url or named
    if not base_url:
        raise CheckFailed("Give your server's address: in Bailey's Settings, or with --address.")
    variable = KEY_VARIABLES.get(setup.id)
    key = _saved_key(folder, key_name(setup.id), environ) or (environ.get(variable, "") if variable else "")
    if setup.key_required and not key:
        raise CheckFailed(f"Bailey has no {setup.name} key: save one in Bailey's Settings, or set {variable}.")
    connection = Connection(provider=setup.id, model=model_name, api_key=key, base_url=base_url)
    return Setup(connection=connection, scripts_allowed=bool(saved.get(SCRIPTS_SETTING)))


def _saved_key(folder: Path, name: str, environ: Mapping[str, str]) -> str | None:
    if environ.get(FORCE_FILE_VARIABLE, "").strip().lower() != "file":
        chosen = keychain_backend(keyring.get_keyring())
        if chosen is not None:
            return KeychainKeys(chosen).get(name)
    path = folder / FILE_NAME
    passphrase = environ.get(PASSPHRASE_VARIABLE, "")
    if not path.exists() or not passphrase:
        return None
    keys = EncryptedFileKeys(path)
    try:
        keys.unlock(passphrase)
    except WrongPassphrase as refused:
        raise CheckFailed(f"BAILEY_PASSPHRASE does not open {path}.") from refused
    return keys.get(name)


def read_package(path: Path) -> dict[str, bytes]:
    """A solution as ``bailey check`` is given it: a folder, held to a package's limits, or a ZIP."""

    if path.is_dir():
        entries: list[dict[str, str]] = []
        for root, folders, names in os.walk(path, followlinks=False):
            here = Path(root)
            folders[:] = [name for name in sorted(folders) if name != ".git"]
            for name in [*folders, *sorted(names)]:
                if (here / name).is_symlink():
                    raise CheckFailed(
                        f"{(here / name).relative_to(path).as_posix()} is a link, which a package may not carry."
                    )
            for name in sorted(names):
                if name == ".DS_Store":
                    continue
                if len(entries) >= MAX_ENTRIES:
                    raise CheckFailed(f"{path} holds more than {MAX_ENTRIES} files.")
                item = here / name
                if item.stat().st_size > MAX_FILE_BYTES:
                    raise CheckFailed(
                        f"{item.relative_to(path).as_posix()} is larger than {MAX_FILE_BYTES // 1024} KB."
                    )
                relative = item.relative_to(path).as_posix()
                entries.append(
                    {"path": f"{path.name}/{relative}", "data": base64.b64encode(item.read_bytes()).decode()}
                )
        return files_from_folder(entries)
    if path.is_file():
        if path.stat().st_size > MAX_PACKAGE_BYTES:
            raise CheckFailed(f"{path} is over {MAX_PACKAGE_BYTES // (1024 * 1024)} MB.")
        return unzip_solution(path.read_bytes())
    raise CheckFailed(f"{path} is neither a folder nor a ZIP file.")


def _events(body: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]


def _refusal(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error") or response.text)
    except ValueError:
        return response.text


class _Bailey:
    """The check's own Bailey, driven through the app's routes."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def call(self, method: str, path: str, **sent: Any) -> Any:
        response = await self.client.request(method, path, **sent)
        if response.status_code != 200:
            raise CheckFailed(_refusal(response))
        return response.json()

    async def run(self, assistant_id: str, case: Case) -> Outcome:
        outcome = Outcome()
        conversation = await self.call("POST", "/api/conversations", json={"assistant": assistant_id})
        path = f"/api/conversations/{conversation['id']}"
        response = await self.client.post(f"{path}/messages", json={"text": case.message})
        for _ in range(MAX_REFUSALS):
            if response.status_code != 200:
                outcome.error = _refusal(response)
                return outcome
            asked: dict[str, str] = {}
            for event in _events(response.text):
                kind = event.get("type")
                if kind == "text":
                    outcome.answer += str(event.get("text") or "")
                elif kind == "skill":
                    outcome.skills.append(str(event.get("name") or ""))
                elif kind == "search":
                    outcome.searches.append(str(event.get("query") or ""))
                elif kind == "tool":
                    outcome.tools.append(str(event.get("tool") or ""))
                elif kind == "approval":
                    for call in event.get("calls") or []:
                        asked[str(call["id"])] = str(call.get("tool") or "")
                elif kind == "done":
                    outcome.citations.extend(event.get("citations") or [])
                    for key, value in (event.get("usage") or {}).items():
                        outcome.usage[key] = outcome.usage.get(key, 0) + int(value)
                    outcome.estimates.append(event.get("estimate"))
                elif kind == "error":
                    if event.get("cause") == "provider":
                        # No case runs on a provider that refuses the key, or that cannot be reached.
                        raise CheckFailed(str(event.get("message") or "The AI provider failed."))
                    outcome.error = str(event.get("message") or "The run failed.")
            if outcome.error or not asked:
                return outcome
            # A write, or a tool nobody declared, never runs in a check: each is refused as a "no".
            outcome.refused.extend(name for name in asked.values() if name not in outcome.refused)
            response = await self.client.post(f"{path}/answers", json={"answers": dict.fromkeys(asked, "no")})
        outcome.error = f"It asked to run a tool {MAX_REFUSALS} times after being refused."
        return outcome


async def run_check(
    files: Mapping[str, bytes],
    setup: Setup,
    *,
    embedder: Embedder | None = None,
    environ: Mapping[str, str] = os.environ,
    shown: Callable[[str], None] | None = None,
) -> Report:
    """Install the solution in a Bailey of the check's own, run every case, and judge each answer.

    ``shown`` is handed the report's text as it is ready: its opening once the documents are
    in, then each case as it is judged, so a person watching a slow model sees it move.
    """

    # Imported here: the app pulls in the agent core, which `bailey check --help` needs none of.
    from bailey.server import AppState, build_app

    checked = check_package(files, scripts_allowed=setup.scripts_allowed, provider=setup.connection.provider)
    made = checked.made
    cases = cases_of(made)
    if not cases:
        raise CheckFailed(f"{made.title} has no examples and no samples, so there is nothing to check.")
    missing = [
        f"{name} ({server.name})"
        for server in made.servers
        if not server.remote
        for name in server.env
        if not environ.get(name)
    ]
    if missing:
        raise CheckFailed(
            f"Its tool servers need {', '.join(missing)}: set each in the environment `bailey check` runs in."
        )
    # A file a tool server still holds open on Windows would otherwise turn a finished check into a traceback.
    with tempfile.TemporaryDirectory(prefix="bailey-check-", ignore_cleanup_errors=True) as temporary:
        folder = Path(temporary)
        store = Store(folder / STORE_FILE)
        held = _HeldKeys()
        token = secrets.token_urlsafe(32)
        connection = setup.connection
        state = AppState(
            token=token,
            store=store,
            keys=KeychainKeys(cast(KeyringBackend, held)),
            data_folder=folder,
            base_urls={connection.provider: connection.base_url},
            embedder=embedder,
        )
        try:
            transport = httpx.ASGITransport(app=build_app(state))
            headers = {"Authorization": f"Bearer {token}"}
            async with httpx.AsyncClient(
                transport=transport, base_url=_BASE, headers=headers, timeout=CASE_TIMEOUT
            ) as client:
                bailey = _Bailey(client)
                settings: dict[str, Any] = {"provider": connection.provider, "model": connection.model}
                if provider(connection.provider).needs_address:
                    settings["address"] = connection.base_url
                await bailey.call("PUT", "/api/settings", json={**settings, SCRIPTS_SETTING: setup.scripts_allowed})
                if connection.api_key:
                    await bailey.call("PUT", f"/api/keys/{connection.provider}", json={"key": connection.api_key})
                entries = [{"path": path, "data": base64.b64encode(data).decode()} for path, data in files.items()]
                screen = await bailey.call("POST", "/api/installs", json={"files": entries})
                assistant_id = (await bailey.call("POST", f"/api/installs/{screen['id']}"))["id"]
                for server in made.servers:
                    for name in server.env:
                        if not server.remote:
                            state.keys.set(variable_key(assistant_id, server.name, name), environ[name])
                documents = "none"
                for sample in made.samples:
                    view = await bailey.call("POST", f"/api/assistants/{assistant_id}/knowledge/samples/{sample.slot}")
                    documents = str(view.get("mode") or documents)
                if documents == "search" and (embedder is None or not embedder.ready()):
                    raise CheckFailed(
                        f"{made.title}'s samples are searched, which takes the search model: download it from "
                        "an assistant's Documents in Bailey, or set BAILEY_SEARCH_MODEL to a folder holding it."
                    )
                if shown is not None:
                    shown(opening(made.title, connection.provider, connection.model, documents))
                results = []
                for case in cases:
                    outcome = await bailey.run(assistant_id, case)
                    results.append(Result(case, outcome, judge(case, outcome, made)))
                    if shown is not None:
                        shown(result_text(results[-1]))
        finally:
            await state.tools.close()
            store.close()
    return Report(
        title=made.title,
        name=made.name,
        provider=connection.provider,
        model=connection.model,
        documents=documents,
        results=results,
    )


def main(solution: str, *, as_json: bool, provider_id: str | None, model: str | None, address: str | None) -> int:
    """``bailey check``: 0 when every case passes, 1 when one fails, 2 when the check cannot run."""

    folder = data_folder()
    try:
        files = read_package(Path(solution).expanduser())
        setup = setup_for(folder, provider_id=provider_id, model=model, address=address)
        named = os.environ.get("BAILEY_SEARCH_MODEL")
        # The person's search model, where they have one: asking a folder that is not there
        # whether it holds the model makes it, and the check makes nothing of the person's.
        models = folder / "models"
        embedder = LocalEmbedder(models, folder=Path(named) if named else None) if named or models.is_dir() else None
        report = asyncio.run(run_check(files, setup, embedder=embedder, shown=None if as_json else _show))
    except (CheckFailed, SolutionRefused) as failure:
        sys.stderr.write(f"bailey check: {failure}\n")
        return 2
    # JSON in ASCII, as `bailey doctor --json` writes it: a script reads it whatever the pipe's encoding.
    _show(json.dumps(report.as_dict(), indent=2) + "\n" if as_json else closing(report))
    return 0 if report.passed else 1


def _show(text: str) -> None:
    """Write as the output can take it: a Windows pipe in cp1252 gets a ? for a character it has none for."""
    try:
        sys.stdout.write(text)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "ascii"
        sys.stdout.write(text.encode(encoding, errors="replace").decode(encoding))
    sys.stdout.flush()
