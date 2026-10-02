# Schemas

## `assistant.json`, the solution document

A solution is an [Agent Plugins v1.0.0](https://agent-plugins.org) plugin. Its
skills sit in `skills/<name>/SKILL.md` and its MCP servers in `mcp.json`, so the
parts load in any Agent Plugins client. One file says how the parts fit together
as an assistant: `assistant.json`, which `plugin.json` names in its
`extensions["org.sigrix"]` namespace, under `assistant` (Postern
[§8](https://github.com/sigrix-io/postern/blob/main/SPEC.md#8-sigrix-profile)).
Bailey reads it to install and run the solution, and any builder may write it.

```
shop-support/
├── plugin.json        extensions["org.sigrix"]: { "assistant": "assistant.json", … }
├── assistant.json
├── skills/
│   ├── shop-support/SKILL.md     the assistant's own instructions
│   └── brand-voice/SKILL.md      a skill it loads when needed
└── mcp.json
```

[`0.1/assistant.schema.json`](0.1/assistant.schema.json) is format 0.1. Each
member's `description` there says what it holds; in short:

| Member | Required | Holds |
| --- | --- | --- |
| `format_version` | yes | `"0.1"` |
| `name` | yes | the plugin's name, equal to `plugin.json`'s |
| `title` | yes | the name a person sees |
| `instructions` | yes | the skill holding the assistant's own instructions, kept in context from the first message |
| `description` | | what job the solution does |
| `persona` | | a Markdown file with the voice it answers in, kept in context with the instructions |
| `starters` | | messages a client may offer to start a conversation |
| `skills` | | the other skills the plugin carries, loaded when needed, each as `name` and `path` |
| `skills_sold_separately` | | skills it uses that the plugin does not carry |
| `mcp` | | the MCP configuration, `mcp.json` |
| `tools` | | per MCP server, the declared `tools` and which of them are `write_tools` |
| `examples` | | inputs it is meant to handle, each with an answer when the writer has one |
| `knowledge` | | the documents it asks the person for, its knowledge slots |

### What the schema cannot say

A writer also keeps these, which JSON Schema cannot express:

- Every path names a file the plugin carries.
- `instructions` is not also listed in `skills`.
- Each entry in `skills` has the path `skills/<name>/SKILL.md` for its own
  `name`, and that file's frontmatter `name` is the same. No two entries share a
  name.
- Each key of `tools` is a server in the configuration `mcp` names, and `tools`
  is empty or absent when `mcp` is.
- Each server's `write_tools` are also in its `tools`.
- No two knowledge slots share a name.

A client asks before a write. It treats as a write any tool missing from its
server's declaration, and every tool of a server missing from `tools`, so a
writer that declares nothing gets asked about everything rather than nothing.

### Writers and readers

The schema is the **writer's** contract: a document a writer emits validates
against the schema for the version it names. A **reader** is wider:

- It reads `format_version` as `MAJOR.MINOR` and refuses a major it does not
  know.
- It ignores a member it does not know. A minor version only adds optional
  members, so a reader that knows 0.1 reads a 0.2 document as the 0.1 document
  inside it. A change a reader cannot ignore moves the major.
- It treats an absent list or map as empty, and an absent string as empty.

So do not generate a reader's types from the schema as it stands: its closed
objects refuse the members a later minor version adds.

### Identifiers and hosting

Each version is a directory here, and a new version is a new directory beside
the old ones, never an edit in place: `0.1/` is the format a 0.1 document names.
The schema's `$id`, `https://sigrix.io/schemas/bailey/0.1/assistant.schema.json`,
is a permanent name. It is never reused for anything else, and it resolves:
[Sigrix](https://github.com/sigrix-io/sigrix) vendors this directory and serves
the schema at its `$id`, as it serves Postern's. A change here is therefore also
a change there, and the pull request making one says so.

### The case table

[`0.1/assistant.cases.json`](0.1/assistant.cases.json) holds the cases every
implementation agrees on. `valid` lists documents that validate. Each entry in
`invalid` names a valid case as its `base` and one
[RFC 6902](https://www.rfc-editor.org/rfc/rfc6902) `add`, `replace` or `remove`
as its `change`, so it fails for that change and nothing else.

`tests/test_solution_schema.py` runs the table against the schema with
`jsonschema`, and checks the table itself: every member appears in a valid case,
and every required member has a case without it. A repository that vendors the
schema runs the same table with its own validator, so the two cannot quietly
disagree about what the format allows. A change to the schema comes with the
cases that show it.
