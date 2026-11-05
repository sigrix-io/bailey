# A download from Sigrix

`order-desk.zip` is a download exactly as Sigrix's own builder writes it for an assistant, byte for
byte, from the function its download route packs with. The assistant is made up (a pottery
shop's order desk). It has one MCP server with one key, one skill of its seller's own with a
reference file, and one knowledge slot with its sample.

Since 2026-10-01 a download is a solution: `plugin.json` and `assistant.json` sit beside the
files a download always carried (`SKILL.md`, `README.md`, `AGENTS.md`, the MCP configs and
`setup.sh`). `tests/test_install.py` installs it as it is, and in the shape a download had
before then, which it makes by taking those additions out again.

The script that writes it again calls Sigrix's builder, so it is kept with Sigrix's own code,
beside that builder, rather than here.
