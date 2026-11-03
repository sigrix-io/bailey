"""A lesson's voice: Kokoro, an open text-to-speech model (Apache-2.0) that runs on this computer.

    python lessons/narrate.py "Install one from a zip file."   # hear one line, and see how it is said

The model's two files, about 120 MB, are fetched the first time a line has to be spoken,
from the kokoro-onnx project's release on GitHub, checked against the SHA-256 below, and
kept in the cache folder. Each line's clip is kept there too, by its words, voice and speed,
so a lesson recorded again speaks nothing it has spoken before.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import urllib.request
import wave
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
MODEL = "kokoro-v1.0.int8.onnx"
VOICES = "voices-v1.0.bin"
DIGESTS = {
    MODEL: "6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb",
    VOICES: "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}


@dataclass(frozen=True)
class Clip:
    file: Path
    seconds: float


def cache_folder() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "bailey-lessons"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch_model(folder: Path) -> Path:
    """The model's folder, with both files in it, each fetched once and checked."""

    folder.mkdir(parents=True, exist_ok=True)
    for name, digest in DIGESTS.items():
        path = folder / name
        if path.exists() and _sha256(path) == digest:
            continue
        sys.stderr.write(f"Fetching the voice model's {name} from {RELEASE}\n")
        partial = path.with_name(name + ".part")
        # A fixed https address, the project's own release.
        with urllib.request.urlopen(RELEASE + name, timeout=120) as response, partial.open("wb") as out:  # noqa: S310
            shutil.copyfileobj(response, out, 1 << 20)
        if _sha256(partial) != digest:
            partial.unlink()
            raise RuntimeError(f"{name} from {RELEASE} does not match its SHA-256, so it was not kept.")
        partial.replace(path)
    return folder


def _write_wav(path: Path, audio: np.ndarray, rate: int) -> None:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())


class Voice:
    """One voice, loaded the first time it has a line to say."""

    def __init__(self, name: str = "af_heart", speed: float = 1.0, lang: str = "en-us") -> None:
        self.name, self.speed, self.lang = name, speed, lang
        self._kokoro: Any = None

    def kokoro(self) -> Any:
        if self._kokoro is None:
            from kokoro_onnx import Kokoro  # heavy: loaded only when a line has to be spoken

            folder = fetch_model(cache_folder() / "kokoro")
            self._kokoro = Kokoro(str(folder / MODEL), str(folder / VOICES))
        return self._kokoro

    def say(self, words: str, path: Path) -> None:
        audio, rate = self.kokoro().create(words, voice=self.name, speed=self.speed, lang=self.lang)
        # A short fade at each end, so no clip starts or stops with a click.
        ramp = min(len(audio) // 4, int(rate * 0.012))
        if ramp:
            audio[:ramp] *= np.linspace(0, 1, ramp)
            audio[-ramp:] *= np.linspace(1, 0, ramp)
        _write_wav(path, audio, rate)

    def phonemes(self, words: str) -> str:
        return str(self.kokoro().tokenizer.phonemize(words, lang=self.lang))


def speak(lines: Mapping[str, Mapping[str, str]], voice: Voice) -> dict[str, Clip]:
    """A clip for every line: its ``spoken`` words where it has them, else its ``text``."""

    folder = cache_folder() / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    clips = {}
    for key, line in lines.items():
        words = line.get("spoken") or line["text"]
        name = hashlib.sha256(f"{voice.name}|{voice.speed}|{voice.lang}|{words}".encode()).hexdigest()[:20]
        path = folder / f"{name}.wav"
        if not path.exists():
            voice.say(words, path)
        with wave.open(str(path), "rb") as clip:
            clips[key] = Clip(path, clip.getnframes() / clip.getframerate())
    return clips


def main() -> int:
    parser = argparse.ArgumentParser(description="Speak one line, to hear it and see how the voice reads it.")
    parser.add_argument("words")
    parser.add_argument("--voice", default="af_heart")
    parser.add_argument("--out", type=Path, default=Path("line.wav"))
    args = parser.parse_args()
    voice = Voice(args.voice)
    voice.say(args.words, args.out)
    with wave.open(str(args.out), "rb") as clip:
        seconds = clip.getnframes() / clip.getframerate()
    sys.stdout.write(f"{args.out}: {seconds:.1f} s\n{voice.phonemes(args.words)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
