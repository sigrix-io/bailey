# Lessons

Short videos that show a person how to use Bailey, recorded from the app itself. A lesson is a
storyboard written as code: a table of everything it says, and its steps, each a highlight, a
click or a caption. Recording one starts Bailey, drives its page in Chromium, and writes:

| File | What it is |
| --- | --- |
| `<lesson>.mp4` | the video: 1080p, 30 fps, H.264 and AAC, with its chapters as markers desktop players show |
| `<lesson>.jpg` | its poster, which is the title card |
| `<lesson>.vtt` | its captions: every line spoken, including those over a card |
| `<lesson>.chapters.vtt` | its chapters, for a web page's video player |
| `<lesson>.chapters.txt` | its chapters as lines to paste into a YouTube description |

Because a lesson is code, it is recorded again in a few minutes when the app changes, and a step
the app no longer has stops the recording with an error rather than letting a stale video out.

## Recording one

Beside Bailey's own development setup, a lesson needs Playwright's Chromium, the voice, and
ffmpeg on the `PATH`:

```sh
pip install -e ".[dev]" -r lessons/requirements.txt
playwright install chromium
python lessons/first_solution.py
```

It takes about four minutes, and writes into `lessons/out/`, which git ignores. `--out` names
another folder, `--chromium` (or `LESSONS_CHROMIUM`) a Chromium already installed, and
`--keep-work` keeps the frames, the audio and Bailey's data folder for a look afterwards.

The first recording fetches the voice model, about 120 MB, from the
[kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) project's release on GitHub, checks it
against the SHA-256 in `narrate.py`, and keeps it in `~/.cache/bailey-lessons/` with every line
it speaks, so a lesson recorded again speaks nothing it has spoken before.

## What is real, and what is scripted

The app is real: the page, the install screen, the documents, the citations and the costs are
Bailey's own, running from this checkout. The model is not. `scripted_bailey.py` starts Bailey
with its providers pointed at the emulator from its own tests (`tests/emulated_providers.py`),
which speaks each provider's wire protocol, and gives it the answers the lesson scripts. So a
recording needs no key and costs nothing, and comes out the same every time; and wherever a
lesson is published, it says the answers are scripted. The key a lesson types is made up, and
Bailey keeps it in an encrypted file in a data folder of the recording's own (`BAILEY_KEYS=file`),
so a recording never touches the keychain of the computer it runs on: that is why the page says
"Saved in an encrypted file, Bailey/keys.enc."

## Writing a lesson

Copy `first_solution.py`. Its parts:

- `NARRATION`, everything it says. `text` is the caption; `spoken`, when given, is what the voice
  says instead, for a word it would misread (it spells out "ZIP", so the line says "zip"). To
  hear a line and see how it is read, `python lessons/narrate.py "the line"`.
- `REPLIES`, the model's scripted answers, each for the message that contains its `when`
  (`scripted_bailey.py` says how a reply cites a passage).
- `run`, the steps, through the `Studio` (`studio.py`): `say(key)` shows a caption and speaks
  it, holding until the voice ends; `voice(key)` speaks over a card; `spot(locator)` dims the
  page around one part of it; `click`, `type`, `chapter`, `card`, `quiet` and `sleep`.

A chapter starts at each `chapter(title)`. YouTube shows chapters only when the first starts at
0:00 and none is shorter than ten seconds, so the first is moved to 0:00 and a shorter one runs
into the one before it (`timeline.py`, held by `tests/test_lessons.py`).

## The sound

- **The voice** is Kokoro, an open text-to-speech model (the model under Apache 2.0, kokoro-onnx
  under MIT), run on this computer: `af_heart`, a lesson's `voice`, is one of its English voices.
  Another voice, or a person reading the narration table, can take its place.
- **The music** is synthesised by `music.py` from nothing, so it needs no licence. Any track
  whose licence allows it could replace it.
- **The mix** (`mixdown.py`) levels every line to the same loudness, starts it a quarter of a
  second after its caption appears, adds a soft click for each click, and ducks the music under
  the voice. The whole is normalised to -16 LUFS, with its peaks about 2 dB under full scale.

## Publishing one

The captions are drawn into the picture, and they are in `<lesson>.vtt` as well: a player that
turns the `.vtt` on shows each line twice. The `.vtt` is for a transcript, for a translation,
and for a version recorded without them in the picture. The `.mp4` carries a chapter track
beside its picture and sound, which is how its markers reach a desktop player.

None of `lessons/` is part of Bailey's package: it is in neither the wheel nor the sdist.
