"""Drives the spike's page in Chromium and prints one JSON report.

    python browser_check.py      # SPIKE_CHROMIUM=/path/to/chrome to use a browser already installed

On each emulated provider, in a real browser:

- `streaming`: the answer grows on the page in several steps before the run ends, and the markup
  in it is shown as text: no element is built from it;
- `no`: the write tool asks, the page answers "No", the tool does not run, and the answer shows
  the denial reached the model;
- `once`: the page answers "Allow once" and the tool runs exactly once.

Then once, on the first provider: "Always for this assistant" runs the tool, and the next call
runs it without asking. And from outside the page: the API refuses a request without the session
token, and the page refuses a `Host` that is not a loopback name.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import Page, async_playwright

sys.path.insert(0, str(Path(__file__).parent))

import web  # noqa: E402

RESULTS = Path(__file__).parent.parent / "results"
REFUND = "Draft a reply to the latest refund request."
COUNT_MUTATIONS = """() => {
  window.__steps = 0;
  new MutationObserver((records) => { window.__steps += records.length; })
    .observe(document.getElementById('transcript'), {subtree: true, childList: true, characterData: true});
}"""


def drafts_written(drafts: Path) -> int:
    return len(drafts.read_text(encoding="utf-8").splitlines()) if drafts.exists() else 0


async def send(page: Page, provider: str, message: str) -> None:
    await page.select_option("#provider", provider)
    await page.fill("#message", message)
    await page.click("#send")


class RunFailed(Exception):
    """The page reported an error instead of finishing."""


async def finished(page: Page) -> str:
    await page.wait_for_selector('#transcript[data-state="done"], #transcript[data-state="error"]', timeout=30_000)
    text = await page.inner_text("#transcript")
    if await page.get_attribute("#transcript", "data-state") == "error":
        raise RunFailed(await page.locator("#transcript .error").last.inner_text())
    return text


async def answer_approval(page: Page, answer: str, screenshot: Path | None = None) -> str:
    card = page.locator(".approval:not([data-answered])").last
    await card.wait_for(timeout=30_000)
    tool = await card.get_attribute("data-tool") or ""
    if screenshot:
        await page.screenshot(path=str(screenshot), full_page=True)
    await card.locator(f'button[data-answer="{answer}"]').click()
    return tool


async def provider_runs(page: Page, provider: str, drafts: Path, screenshot: Path | None) -> dict[str, Any]:
    results: dict[str, Any] = {}

    await page.evaluate(COUNT_MUTATIONS)
    await send(page, provider, "Say hello.")
    text = await finished(page)
    steps = await page.evaluate("window.__steps")
    markup_elements = await page.locator("#transcript b").count()
    results["streaming"] = {
        "ok": steps >= 5 and "<b>Markup</b>" in text and markup_elements == 0,
        "page_updates": steps,
        "markup_shown_as_text": "<b>Markup</b>" in text,
        "elements_built_from_markup": markup_elements,
    }

    before = drafts_written(drafts)
    await send(page, provider, REFUND)
    asked = await answer_approval(page, "no", screenshot)
    text = await finished(page)
    results["no"] = {
        "ok": asked == "create_draft" and drafts_written(drafts) == before and web.DENIAL in text,
        "asked_for": asked,
    }

    before = drafts_written(drafts)
    await send(page, provider, REFUND)
    asked = await answer_approval(page, "once")
    text = await finished(page)
    results["once"] = {
        "ok": asked == "create_draft" and drafts_written(drafts) == before + 1 and "Draft created in t-1042" in text
    }
    return results


async def main() -> int:
    drafts = Path(tempfile.mkdtemp()) / "drafts.jsonl"
    session = web.emulated_session(drafts)
    url = web.serve_in_background(session)
    report: dict[str, Any] = {"providers": {}}

    no_token = httpx.post(url + "/api/runs", json={"provider": "anthropic", "message": "hi"})
    foreign = httpx.get(url + "/", headers={"Host": "attacker.example"})
    served = httpx.get(url + "/")
    report["refusals"] = {
        "ok": no_token.status_code == 401
        and foreign.status_code == 401
        and "script-src 'self'" in served.headers.get("content-security-policy", ""),
        "without_token": no_token.status_code,
        "foreign_host": foreign.status_code,
        "csp": served.headers.get("content-security-policy"),
    }

    async with async_playwright() as playwright:
        chromium = os.environ.get("SPIKE_CHROMIUM")
        browser = (
            await playwright.chromium.launch(executable_path=chromium)
            if chromium
            else await playwright.chromium.launch()
        )
        errors: list[str] = []
        for index, provider in enumerate(session.providers):
            page = await browser.new_page()
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            await page.goto(url + "/")
            shot = RESULTS / "approval.png" if index == 0 else None
            report["providers"][provider] = await provider_runs(page, provider, drafts, shot)
            await page.close()

        page = await browser.new_page()
        await page.goto(url + "/")
        before = drafts_written(drafts)
        await send(page, "anthropic", REFUND)
        await answer_approval(page, "always")
        await finished(page)
        await send(page, "anthropic", REFUND)
        text = await finished(page)
        cards = await page.locator(".approval").count()
        report["always"] = {
            "ok": drafts_written(drafts) == before + 2 and cards == 1 and "allowed: you chose always" in text,
            "approval_cards_shown": cards,
            "drafts_written": drafts_written(drafts) - before,
        }
        report["console_errors"] = errors
        await browser.close()

    checks = [
        report["refusals"],
        report["always"],
        *(c for runs in report["providers"].values() for c in runs.values()),
    ]
    report["all_ok"] = all(check["ok"] for check in checks) and not report["console_errors"]
    print(json.dumps(report, indent=2))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
