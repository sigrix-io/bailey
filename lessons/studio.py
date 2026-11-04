"""Record a Bailey lesson as code: the real app in a real browser, with a voice, captions and chapters.

A lesson (``first_solution.py``) is a storyboard: a table of what it says, and the steps,
each a highlight, a click, a caption. The studio starts Bailey on a scripted model
(``scripted_bailey.py``), opens it in Chromium at 1280x720 CSS pixels and a device scale
of 1.5, so frames are 1920x1080 and text is sharp, draws the cursor, spotlight, captions
and cards over the page (``overlay.js``), and captures every painted frame over CDP. It
writes, beside each other in the output folder:

- ``<slug>.mp4``: H.264 and AAC at 1080p and 30 fps, with its chapters as markers;
- ``<slug>.jpg``: the poster, which is the title card;
- ``<slug>.vtt``: the captions, every line spoken;
- ``<slug>.chapters.vtt`` and ``<slug>.chapters.txt``: the chapters, for a web page's player
  and for a YouTube description.

Every line is spoken before the recording starts (``narrate.py``), so each caption is held
exactly as long as its voice, and the sound is mixed in at the end (``mixdown.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import math
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal

import mixdown
import narrate
import timeline
from playwright.async_api import Locator, Page, async_playwright

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WIDTH, HEIGHT, SCALE = 1280, 720, 1.5
#: A voice starts a moment after its caption fades in, and the next line waits a breath after it.
VOICE_LEAD = 0.25
VOICE_GAP = 0.45
LAUNCH = re.compile(r"Open it at (http://127\.0\.0\.1:\d+/#token=\S+)")
MODEL = re.compile(r"Scripted model at (\S+)")
#: The recording's own key file, in its own data folder, holds only a made-up key.
PASSPHRASE = "a passphrase for the recording"  # noqa: S105 -- made up, for a key file thrown away


@dataclass(frozen=True)
class Lesson:
    slug: str
    #: The title card, which is also the poster: ``kicker``, ``title`` and ``sub``.
    title: Mapping[str, str]
    #: Every line, by key: ``text`` is the caption, and ``spoken`` what the voice says instead, if anything.
    narration: Mapping[str, Mapping[str, str]]
    run: Callable[[Studio], Awaitable[None]]
    #: The model's scripted answers (``scripted_bailey.py``).
    replies: Sequence[Mapping[str, Any]] = ()
    voice: str = "af_heart"
    color_scheme: Literal["light", "dark"] = "light"
    #: Steps before the recording starts, on the page itself (``studio.page``): what an
    #: earlier lesson showed, such as choosing a provider, so this one starts where it left off.
    setup: Callable[[Studio], Awaitable[None]] | None = None
    #: Stand-in programs on Bailey's PATH and the terminal's, each a name and what it runs with this
    #: Python: a script and its first arguments, such as the tool server a solution's mcp.json
    #: starts (``harbour_mail.py``), or ``bailey`` itself on the scripted model.
    commands: Mapping[str, Sequence[str | Path]] = field(default_factory=dict)
    #: For a lesson that runs an app: its environment is a stand-in made in seconds, and each of
    #: its crew's tasks takes this many seconds (``scripted_bailey.py``).
    app_task_seconds: float | None = None


class Bailey:
    """Bailey on a scripted model, in a data folder of its own, and the address it printed."""

    def __init__(self, work: Path, lesson: Lesson) -> None:
        answers = work / "replies.json"
        answers.write_text(json.dumps(list(lesson.replies)), encoding="utf-8")
        # Nothing it reaches is past this computer, so no proxy applies. The data folder is named
        # relative to the work folder, so the page names the key file as "Bailey/keys.enc".
        env = {key: value for key, value in os.environ.items() if not key.lower().endswith("_proxy")}
        env |= {"BAILEY_HOME": "Bailey", "BAILEY_KEYS": "file", "BAILEY_PASSPHRASE": PASSPHRASE}
        if lesson.app_task_seconds is not None:
            env["LESSON_APP_TASK_SECONDS"] = str(lesson.app_task_seconds)
        if lesson.commands:
            # Each stand-in is a small shell script ahead of everything else on the PATH, which a
            # tool server Bailey starts inherits, as it would a command the person had installed.
            folder = work / "bin"
            folder.mkdir()
            for name, runs in lesson.commands.items():
                launcher = folder / name
                argv = " ".join(shlex.quote(str(part)) for part in (sys.executable, *runs))
                launcher.write_text(f'#!/bin/sh\nexec {argv} "$@"\n', encoding="utf-8")
                launcher.chmod(0o755)
            env["PATH"] = os.pathsep.join([str(folder), env.get("PATH", "")])
        self.env = env
        self.model_url = ""
        self.process = subprocess.Popen(  # noqa: S603 -- this folder's own launcher, a fixed argv
            [sys.executable, str(HERE / "scripted_bailey.py"), str(answers)],
            cwd=work,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._read, args=(lines,), daemon=True).start()
        printed: list[str] = []
        deadline = time.monotonic() + 60
        while True:
            try:
                line = lines.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                raise RuntimeError("Bailey printed no address in 60 s:\n" + "".join(printed)) from None
            if line is None:
                raise RuntimeError("Bailey stopped before printing its address:\n" + "".join(printed))
            printed.append(line)
            if model := MODEL.search(line):
                self.model_url = model.group(1)
            if match := LAUNCH.search(line):
                self.address = match.group(1)
                return

    def _read(self, lines: queue.Queue[str | None]) -> None:
        if self.process.stdout is not None:
            for line in self.process.stdout:
                lines.put(line)
        lines.put(None)

    def stop(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.process.kill()


class _TerminalFiles(BaseHTTPRequestHandler):
    """The terminal a lesson films, and Bailey's mark for the overlay's cards: nothing else."""

    FILES = {
        "/": (HERE / "terminal.html", "text/html; charset=utf-8"),
        "/static/icon.svg": (REPO / "src" / "bailey" / "web" / "icon.svg", "image/svg+xml"),
    }

    def do_GET(self) -> None:
        found = self.FILES.get(self.path.split("?", 1)[0])
        if found is None:
            self.send_error(404)
            return
        body = found[0].read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", found[1])
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@dataclass
class _Caption:
    start: float
    text: str
    end: float | None = None


@dataclass
class Studio:
    """What a lesson's steps call. Times are seconds since the recording started."""

    page: Page
    lesson: Lesson
    clips: Mapping[str, narrate.Clip]
    work: Path
    started: float = 0.0
    captions: list[_Caption] = field(default_factory=list)
    spoken_only: list[timeline.Cue] = field(default_factory=list)
    spoken: list[tuple[float, Path]] = field(default_factory=list)
    clicks: list[float] = field(default_factory=list)
    marks: list[timeline.Mark] = field(default_factory=list)
    voice_until: float = 0.0
    mouse: tuple[float, float] = (WIDTH / 2, HEIGHT / 2)
    #: The terminal page's address, and what a command typed there runs with.
    terminal_url: str = ""
    shell_env: Mapping[str, str] = field(default_factory=dict)
    where: str = "~"

    def clock(self) -> float:
        return time.time() - self.started

    async def sleep(self, ms: float) -> None:
        await asyncio.sleep(ms / 1000)

    async def card(self, spec: Mapping[str, str] | None, hold_ms: float = 0) -> None:
        await self.page.evaluate("spec => window.__demo.card(spec)", spec)
        await self.sleep(hold_ms)

    async def chapter(self, title: str | None) -> None:
        """The label on screen, and a chapter of the video from here on."""

        if title:
            self.marks.append(timeline.Mark(self.clock(), title))
        await self.page.evaluate("title => window.__demo.chapter(title)", title)

    async def _voice_free(self) -> None:
        """Never two voices at once: a line waits for the one before it, and a breath after."""

        busy = self.voice_until - time.time()
        if busy > 0:
            await asyncio.sleep(busy + VOICE_GAP)

    def _speak(self, clip: narrate.Clip) -> float:
        """Start a clip now; how long to wait for it, and the breath after."""

        self.spoken.append((self.clock() + VOICE_LEAD, clip.file))
        self.voice_until = time.time() + VOICE_LEAD + clip.seconds
        return VOICE_LEAD + clip.seconds + VOICE_GAP

    def _close_caption(self) -> None:
        if self.captions and self.captions[-1].end is None:
            self.captions[-1].end = self.clock()

    async def say(self, key_or_text: str, hold_ms: float | None = None) -> None:
        """A caption, by its narration key or as text.

        Spoken, it is held as long as its voice; unspoken, long enough to read, about 60 ms a
        character and never under 2.4 s. A shorter ``hold_ms`` lets the voice run on under
        the steps that follow.
        """

        line = self.lesson.narration.get(key_or_text)
        text = line["text"] if line else key_or_text
        clip = self.clips.get(key_or_text) if line else None
        if clip:
            await self._voice_free()
        self._close_caption()
        self.captions.append(_Caption(self.clock(), text))
        await self.page.evaluate("text => window.__demo.caption(text)", text)
        natural = self._speak(clip) if clip else max(2.4, len(text) * 0.06)
        await asyncio.sleep(natural if hold_ms is None else hold_ms / 1000)

    async def voice(self, key: str, extra_ms: float = 0) -> None:
        """A line spoken over a card, with no caption on screen; the captions file still has it."""

        clip = self.clips.get(key)
        if clip:
            await self._voice_free()
            start = self.clock() + VOICE_LEAD
            self.spoken_only.append(timeline.Cue(start, start + clip.seconds + 0.3, self.lesson.narration[key]["text"]))
            await asyncio.sleep(self._speak(clip))
        await self.sleep(extra_ms)

    async def quiet(self) -> None:
        self._close_caption()
        await self.page.evaluate("() => window.__demo.caption(null)")

    async def spot(self, locator: Locator | None, padding: float = 8) -> None:
        """Dim the page around ``locator``, scrolled into view; ``None`` lifts the spotlight."""

        if locator is None:
            await self.page.evaluate("() => window.__demo.spot(null)")
            return
        await locator.evaluate("el => el.scrollIntoView({ block: 'center', behavior: 'smooth' })")
        await self.sleep(550)
        await locator.evaluate("(el, padding) => window.__demo.spot(el, padding)", padding)
        await self.sleep(450)

    async def glide(self, locator: Locator) -> None:
        """Move the cursor to the middle of ``locator`` as a hand would, not in one jump."""

        await locator.evaluate("el => el.scrollIntoView({ block: 'center', behavior: 'smooth' })")
        await self.sleep(450)
        box = await locator.bounding_box()
        if box is None:
            raise RuntimeError(f"{locator} is not on the page")
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        steps = max(14, round(math.hypot(x - self.mouse[0], y - self.mouse[1]) / 14))
        await self.page.mouse.move(x, y, steps=steps)
        self.mouse = (x, y)
        await self.sleep(220)

    async def click(self, locator: Locator) -> None:
        await self.glide(locator)
        self.clicks.append(self.clock())
        await self.page.mouse.down()
        await self.sleep(70)
        await self.page.mouse.up()
        await self.sleep(350)

    async def terminal(self, title: str = "Terminal", where: str = "~") -> None:
        """Leave Bailey's page for the terminal, where shell() runs commands, and put the pointer away."""

        self.where = where
        await self.page.goto(self.terminal_url)
        await self.page.wait_for_function("() => window.__term && window.__demo && window.__demo.ready()")
        await self.page.evaluate(
            "([title, where]) => { window.__term.title(title); window.__term.prompt(where); window.__demo.pointer(false); }",
            [title, where],
        )

    async def shell(self, command: str, delay_ms: float = 45) -> int:
        """Type a command at the prompt, run it for real in a shell of its own, and show what it prints as it prints it.

        It runs in the work folder, with Bailey's environment and the lesson's stand-in commands on
        the PATH, ``bailey`` among them when the lesson puts it there. Its exit status is returned.
        """

        await self.page.evaluate("([text, delay]) => window.__term.type(text, delay)", [command, delay_ms])
        await self.sleep(400)
        await self.page.evaluate("() => window.__term.enter()")
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=self.work,
            env=dict(self.shell_env),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if process.stdout is not None:
            while line := await process.stdout.readline():
                await self.page.evaluate(
                    "text => window.__term.write(text)", line.decode("utf-8", "replace").rstrip("\n")
                )
        status = await process.wait()
        await self.page.evaluate("where => window.__term.prompt(where)", self.where)
        return status

    async def type(self, locator: Locator, text: str, delay_ms: float = 28) -> None:
        """Type as a person does. A new line is Shift+Enter, since Enter sends."""

        await locator.focus()
        for index, line in enumerate(text.split("\n")):
            if index:
                await self.page.keyboard.press("Shift+Enter")
            await locator.press_sequentially(line, delay=delay_ms)


async def provider_chosen(s: Studio) -> None:
    """A lesson's setup for where lesson 1 left off: Anthropic chosen, a made-up key saved, on Assistants."""

    page = s.page
    await page.locator('.nav-item[data-view="settings"]').click()
    await page.locator("#provider-choices label", has_text="Anthropic").click()
    await page.locator("#provider-form button[type=submit]").click()
    await page.locator("#key-card").wait_for(state="visible")
    await page.locator("#key").fill("a-made-up-key-for-the-recording")
    await page.locator("#key-form button[type=submit]").click()
    await page.wait_for_function("() => document.querySelector('#key-status').textContent.trim().length > 0")
    await page.locator('.nav-item[data-view="assistants"]').click()


async def _capture(
    lesson: Lesson, out: Path, work: Path, chromium: str | None
) -> tuple[Studio, list[tuple[Path, float]], float]:
    """Record the lesson's frames; the studio's log of it, each frame with when it was painted, and when it stopped."""

    clips = narrate.speak(lesson.narration, narrate.Voice(lesson.voice)) if lesson.narration else {}
    frames_folder = work / "frames"
    frames_folder.mkdir()
    frames: list[tuple[Path, float]] = []
    acks: set[asyncio.Future[Any]] = set()
    bailey = Bailey(work, lesson)
    files = ThreadingHTTPServer(("127.0.0.1", 0), _TerminalFiles)
    threading.Thread(target=files.serve_forever, daemon=True).start()
    try:
        async with async_playwright() as playwright:
            # The scale is the browser's own, not an emulated one: only then are screencast
            # frames painted at device pixels (1920x1080) rather than CSS pixels (1280x720).
            browser = await playwright.chromium.launch(
                executable_path=chromium,
                args=[f"--force-device-scale-factor={SCALE}", f"--window-size={WIDTH},{HEIGHT}"],
            )
            context = await browser.new_context(viewport=None, color_scheme=lesson.color_scheme)
            await context.add_init_script(path=HERE / "overlay.js")
            page = await context.new_page()
            studio = Studio(page, lesson, clips, work)
            studio.terminal_url = f"http://127.0.0.1:{files.server_address[1]}/"
            studio.shell_env = {
                **bailey.env,
                "LESSON_MODEL_URL": bailey.model_url,
                "PYTHONUNBUFFERED": "1",
                "TERM": "xterm-256color",
            }
            try:
                await page.goto(bailey.address)
                await page.wait_for_function("() => window.__demo && window.__demo.ready()")
                # The page is ready once it has opened Chat: its menu shows a moment earlier, while it
                # still loads its settings, and a click then is undone when it opens Chat.
                await page.locator('[data-panel="chat"]').wait_for(state="visible")
                if lesson.setup is not None:
                    await lesson.setup(studio)
                    # Setup is not part of the video: nothing it did is on its timeline.
                    studio.captions.clear()
                    studio.clicks.clear()
                    studio.marks.clear()
                    studio.spoken.clear()
                    studio.spoken_only.clear()
                await page.mouse.move(*studio.mouse)
                await studio.card(lesson.title, 700)
                cdp = await context.new_cdp_session(page)
                # The poster is the title card, taken over CDP as the frames are, at 1920x1080.
                poster = await cdp.send("Page.captureScreenshot", {"format": "jpeg", "quality": 92})
                (out / f"{lesson.slug}.jpg").write_bytes(base64.b64decode(poster["data"]))

                def painted(frame: dict[str, Any]) -> None:
                    file = frames_folder / f"f{len(frames):06d}.jpg"
                    file.write_bytes(base64.b64decode(frame["data"]))
                    frames.append((file, time.time()))
                    ack = asyncio.ensure_future(cdp.send("Page.screencastFrameAck", {"sessionId": frame["sessionId"]}))
                    acks.add(ack)
                    ack.add_done_callback(settled)

                def settled(ack: asyncio.Future[Any]) -> None:
                    # An ack still in flight when the page closes fails; that failure is expected.
                    acks.discard(ack)
                    if not ack.cancelled():
                        ack.exception()

                cdp.on("Page.screencastFrame", painted)
                studio.started = time.time()
                await cdp.send(
                    "Page.startScreencast",
                    {"format": "jpeg", "quality": 90, "maxWidth": int(WIDTH * SCALE), "maxHeight": int(HEIGHT * SCALE)},
                )
                await lesson.run(studio)
                await studio.quiet()
                await asyncio.sleep(max(0.4, studio.voice_until - time.time() + 0.4))
                stopped = time.time()
                await cdp.send("Page.stopScreencast")
            except Exception:
                with contextlib.suppress(Exception):
                    await page.screenshot(path=out / "failure.png")
                raise
            finally:
                await context.close()
                await browser.close()
    finally:
        files.shutdown()
        bailey.stop()
    return studio, frames, stopped


async def record(lesson: Lesson, out: Path, *, chromium: str | None = None, keep_work: bool = False) -> dict[str, Any]:
    """Record ``lesson`` into ``out``, and say what was made."""

    out.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"{lesson.slug}-", dir=out))
    studio, frames, stopped = await _capture(lesson, out, work, chromium)
    if not frames:
        raise RuntimeError("The browser painted no frames, so there is nothing to encode.")

    # The video's first instant is its first frame's, so every time recorded moves by that much.
    offset = frames[0][1] - studio.started
    seconds = stopped - frames[0][1]
    # Each frame lasts until the next was painted; ffmpeg turns that into a steady 30 fps.
    concat = []
    for (file, at), (_, after) in zip(frames, [*frames[1:], (None, stopped)], strict=True):
        concat += [f"file '{file}'", f"duration {after - at:.3f}"]
    concat.append(f"file '{frames[-1][0]}'")
    (work / "frames.txt").write_text("\n".join(concat) + "\n", encoding="utf-8")
    mp4 = out / f"{lesson.slug}.mp4"
    picture = work / "picture.mp4" if lesson.narration else mp4
    mixdown.ffmpeg(
        *("-f", "concat", "-safe", "0", "-i", str(work / "frames.txt"), "-fps_mode", "cfr", "-r", "30"),
        *("-c:v", "libx264", "-preset", "slow", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart"),
        str(picture),
    )
    if lesson.narration:
        voice = [(at - offset, file) for at, file in studio.spoken]
        mixdown.mix(voice, [at - offset for at in studio.clicks], seconds, picture, mp4, work)

    captions = [
        timeline.Cue(
            caption.start - offset,
            (stopped - studio.started if caption.end is None else caption.end) - offset,
            caption.text,
        )
        for caption in studio.captions
    ]
    captions += [timeline.Cue(cue.start - offset, cue.end - offset, cue.text) for cue in studio.spoken_only]
    (out / f"{lesson.slug}.vtt").write_text(timeline.webvtt(captions), encoding="utf-8")

    parts = timeline.chapters([timeline.Mark(mark.start - offset, mark.title) for mark in studio.marks], seconds)
    if parts:
        (out / f"{lesson.slug}.chapters.vtt").write_text(timeline.webvtt(parts), encoding="utf-8")
        (out / f"{lesson.slug}.chapters.txt").write_text(timeline.youtube_chapters(parts), encoding="utf-8")
        metadata = work / "chapters.ffmeta"
        metadata.write_text(timeline.ffmetadata(f"{lesson.title['kicker']}: {lesson.title['title']}", parts), "utf-8")
        marked = work / "marked.mp4"
        language = ("-metadata:s:a:0", "language=eng") if lesson.narration else ()
        mixdown.ffmpeg(
            *("-i", str(mp4), "-i", str(metadata), "-map", "0", "-map_metadata", "1", "-map_chapters", "1"),
            *("-c", "copy", *language, "-movflags", "+faststart", str(marked)),
        )
        marked.replace(mp4)
    if not keep_work:
        shutil.rmtree(work)
    return {
        "video": str(mp4),
        "seconds": round(seconds, 1),
        "frames": len(frames),
        "captions": len(captions),
        "spoken": len(studio.spoken),
        "clicks": len(studio.clicks),
        "chapters": [f"{timeline.clock_time(part.start)} {part.text}" for part in parts],
    }


def main(lesson: Lesson) -> int:
    parser = argparse.ArgumentParser(description=f"Record the lesson {lesson.slug}.")
    parser.add_argument(
        "--out", type=Path, default=HERE / "out", help="where the recording goes (default: lessons/out)"
    )
    parser.add_argument(
        "--chromium",
        default=os.environ.get("LESSONS_CHROMIUM"),
        help="a Chromium to drive instead of Playwright's own (or LESSONS_CHROMIUM)",
    )
    parser.add_argument("--keep-work", action="store_true", help="keep the frames, the audio and Bailey's data folder")
    args = parser.parse_args()
    made = asyncio.run(record(lesson, args.out, chromium=args.chromium, keep_work=args.keep_work))
    sys.stdout.write(json.dumps(made, indent=2, ensure_ascii=False) + "\n")
    return 0
