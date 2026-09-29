# Security

## Reporting

Email security@sigrix.io with what you found and how to reproduce it. Do not
open a public issue for a vulnerability. You will get an acknowledgement
within three working days.

## What this release does

`bailey doctor` reads this machine only. It opens an in-memory SQLite
database, binds a free port on 127.0.0.1 and closes it at once, and looks for
`uv` and `npx` on the path. It makes no network call, writes no file, and
prints no environment variable.

## What the assistant will hold

These are the rules the assistant is being built to, so a report that one is
broken is welcome from the first version that carries it:

- AI and tool keys stay on your machine, in the operating system's keychain,
  and are never part of a solution package, which names keys and never holds
  them.
- The local server listens on 127.0.0.1 only, and every request carries a
  token the page received when the app started, because any web page you
  visit can reach a loopback port.
- A tool that changes something asks before it runs.
- Documents you add are indexed on your computer. Only the passages retrieved
  for a question reach your AI provider, inside the prompt.
- What a model returns is shown as text, never as markup.
