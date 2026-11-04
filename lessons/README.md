# Lessons

Short videos that show a person how to use Bailey, recorded from the app itself. A lesson is a
storyboard written as code: a table of everything it says, and its steps, each a highlight, a
click or a caption. Recording one starts Bailey, drives its page in Chromium, and writes a folder
of the lesson's own, `lessons/out/<lesson>/`:

| File | What it is |
| --- | --- |
| `video.mp4` | the video: 1080p, 30 fps, H.264 and AAC, with its chapters as markers desktop players show |
| `cover.jpg` | its poster, which is the title card |
| `captions.vtt` | its captions: every line spoken, including those over a card |
| `chapters.vtt` | its chapters, for a web page's video player |
| `chapters.txt` | its chapters as lines to paste into a YouTube description |
| `lesson.json` | its page on sigrix.io, from `pages.py` |

That folder is a lesson bundle, which Sigrix's Learn console imports in one step (below).

Because a lesson is code, it is recorded again in a few minutes when the app changes, and a step
the app no longer has stops the recording with an error rather than letting a stale video out.

## The lessons

| Lesson | What a person learns |
| --- | --- |
| `first_solution.py`, 1: your first solution | where things are; choosing an AI and saving its key; installing a solution from a file, and what the install screen says; giving it documents; a cited answer and what it cost |
| `tools_that_ask.py`, 2: tools that ask before they act | a solution's tool server and the key it needs; a tool that reads and runs, and one that changes something and asks; allowing once and always; taking an "always" back in Settings |
| `apps_that_run_a_crew.py`, 3: apps that run a crew | a solution's app on the install screen, and the key it runs on; installing its libraries once; the form it draws, a run of its crew step by step, and what the run used |
| `check_a_solution.py`, 4: check a solution | `bailey doctor` and what it says of a machine; `bailey check` on a solution whose cases all pass, and on a draft whose examples fail, each with its reason; what it costs; its exit status for a script or a CI job |

Each lesson picks up where the one before left off: lessons 2 to 4 choose the provider before
the recording starts (`provider_chosen`, in `studio.py`), rather than showing it again.

## Recording one

Beside Bailey's own development setup, a lesson needs Playwright's Chromium, the voice, and
ffmpeg on the `PATH`:

```sh
pip install -e ".[dev]" -r lessons/requirements.txt
playwright install chromium
python lessons/first_solution.py
```

and the same for any other lesson's file. Each takes three or four minutes, and writes its folder into `lessons/out/`, which git ignores. `--out` names
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
lesson is published, it says the answers are scripted. Lesson 2's tool server is a stand-in too
(`harbour_mail.py`): the recording puts it on Bailey's `PATH` as `harbour-mail`, the command its
solution's `mcp.json` names, and Bailey starts it as it starts any server, with the variables
that file names and no others. Its drafts go nowhere. Lesson 3's app runs in a stand-in
environment: rather than the 250 MB of CrewAI and its libraries, `scripted_bailey.py` makes it in
seconds as `tests/app_environments.py` does, with the tests' stand-in CrewAI and the pinned
runner. The runner and the run page are real, and the crew's answers are scripted like the
model's. Lesson 4 films a terminal: a page the studio draws (`terminal.html`), where each command
is typed and then really run, in a shell of its own, with its output shown as it prints it.
There `bailey` is Bailey's own command line with each provider's address the scripted model's
(`scripted_bailey.py --cli`), so `bailey check`'s cases, verdicts and report are its own, and
only the answers are scripted. The keys a lesson types are made up, and
Bailey keeps them in an encrypted file in a data folder of the recording's own (`BAILEY_KEYS=file`),
so a recording never touches the keychain of the computer it runs on: that is why the page says
"Saved in an encrypted file, Bailey/keys.enc." Bailey names its data folder in full, so the
recording names the key file from the work folder, and lesson 4's `bailey doctor` the data folder
too: the same in every recording, and never the recorder's own temporary folder.

## Writing a lesson

Copy the lesson nearest to the new one. Its parts:

- `page`, its page on sigrix.io, which `pages.py` holds: the folder it records into and the
  address, title and text an import gives it (Publishing on sigrix.io, above).
- `NARRATION`, everything it says. `text` is the caption; `spoken`, when given, is what the voice
  says instead, for a word it would misread (it spells out "ZIP", so the line says "zip"). To
  hear a line and see how it is read, `python lessons/narrate.py "the line"`.
- `REPLIES`, the model's scripted answers, each for the message that contains its `when`: the
  skill it loads, the tool `calls` it makes, which the app runs or asks about, and the reply
  (`scripted_bailey.py` says how a reply cites a passage). A call to a tool the app did not
  offer fails the recording, as a provider refuses it.
- `setup`, steps before the recording starts, on the page itself; `commands`, stand-in
  programs on Bailey's and the terminal's `PATH`, each a name and the script (with its first
  arguments) it runs, such as a solution's tool server or `bailey` itself; and
  `app_task_seconds`, for a lesson that runs an app: its environment is a stand-in, and each of
  its crew's tasks takes that long, so the steps arrive at a pace a viewer can follow.
- For a lesson in the terminal, `terminal(title, where)` leaves Bailey's page for it, and
  `shell(command)` types a command and runs it, returning its exit status; `clear` clears it.
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
  the voice. The whole is normalised to -16 LUFS, its peaks aimed 2 dB under full scale.

## Publishing on sigrix.io

```sh
python lessons/pack.py
```

zips every recorded lesson with its page and the learning path that lists them,
`lessons/out/bailey-lessons.zip`, and stops, naming the file, if a lesson has not been recorded
whole. On sigrix.io, Moderator > Learn > **Import lessons** takes that zip: it checks each lesson
as the Learn editor would, shows what importing them will do, and saves them, as drafts or
published. A lesson already on the site is updated in place, matched by its slug, so a lesson
recorded again, or a summary corrected in `pages.py`, reaches the site with a pack and an import.

A lesson's page is in `pages.py`: its folder, its address (`slug`), title, summary, body,
category and tags, and the order of the learning path. The format is Sigrix's
(`src/services/learn_import.py` there) and `bundle.py` writes it. A new lesson adds its page there,
names it in its file (`Lesson(page=pages.THE_PAGE, ...)`), and joins `pages.PATH`. Every page
carries the tag `bailey`, which Learn lists the lessons by (`/learn/tag/bailey`), and so does
Bailey's own page on sigrix.io, and says what in its lesson is scripted or a stand-in. Once a lesson
is on YouTube, its page's `youtube_url` is the video's address: the next pack and import link the
page to it.

## Publishing on YouTube

```sh
python lessons/youtube.py --link bailey-lesson-1-your-first-solution=https://sigrix.io/go/abc123
```

writes `youtube.txt` into each recorded lesson's folder: the title, the description and the tags
YouTube Studio asks for, from the lesson's page. The description opens with the summary and a link to
the lesson's page on sigrix.io: its address, or the tracked link the Learn console's Content tab
makes for the video's post (`--link`, once per lesson), so the visits YouTube sends are counted as
YouTube's. Then come the chapters from `chapters.txt`, which YouTube turns into the video's chapters,
the line saying its answers are scripted, and where to get Bailey. Upload `video.mp4` with
`cover.jpg` as its thumbnail and `captions.vtt` as its subtitles. What YouTube would refuse (`<` or `>`, a
title over 100 characters, a description over 5,000 bytes) or show without chapters (a first one not
at 0:00, fewer than three, one under ten seconds) stops the command, naming the lesson, before it
writes anything.

## Publishing one elsewhere

The captions are drawn into the picture, and they are in `<lesson>.vtt` as well: a player that
turns the `.vtt` on shows each line twice. The `.vtt` is for a transcript, for a translation,
and for a version recorded without them in the picture. The `.mp4` carries a chapter track
beside its picture and sound, which is how its markers reach a desktop player.

None of `lessons/` is part of Bailey's package: it is in neither the wheel nor the sdist.
