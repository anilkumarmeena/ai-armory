# AI Armory

A collection of MCP tool sets (Google Workspace, Mac control, GitHub, and more
over time), each a separate, independently loadable module.

Every tool set is written once, in plain Python, with no reference to any MCP
library. AI Armory then exposes whichever tool sets you pick in one of two ways:

1. **In-process**, as a [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python)
   MCP server. This is how JARVIS, a voice assistant built on the SDK, uses it.
2. **Standalone**, as a stdio MCP server built on the official
   [`mcp`](https://github.com/modelcontextprotocol/python-sdk) SDK, for Claude
   Desktop, Claude Code or any other MCP client.

## Install

```sh
pip install -e ".[all]"       # everything
pip install -e ".[sdk,clock]" # the Agent SDK adapter and the clock tool set
pip install -e ".[google]"    # the Google tool sets (gmail, calendar, chat, docs, sheets)
```

Each tool set has its own extra holding only what it needs, so you install
the dependencies of the tool sets you use and nothing else.

## Use it standalone (stdio)

```sh
ai-armory list                        # see the tool sets
ai-armory serve --toolsets clock      # serve chosen tool sets over stdio
ai-armory serve --toolsets google     # a group: gmail, calendar, chat, docs and sheets
ai-armory serve                       # serve every installed tool set
```

Claude Code:

```sh
claude mcp add ai-armory -- ai-armory serve --toolsets clock
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "ai-armory": { "command": "ai-armory", "args": ["serve", "--toolsets", "clock"] }
  }
}
```

## Use it in-process (Claude Agent SDK)

```python
from claude_agent_sdk import ClaudeAgentOptions
from ai_armory import load_many
from ai_armory.adapters.claude_sdk import create_sdk_server, confirmation_required

toolsets = load_many(["clock"])
options = ClaudeAgentOptions(mcp_servers={"ai-armory": create_sdk_server(toolsets)})

# Full tool names (mcp__ai-armory__...) the host should confirm with the user
# before running, e.g. from a can_use_tool callback.
gated = confirmation_required(toolsets)
```

## Confirmation

Tools that send, delete or otherwise act on the world are declared with
`needs_confirmation=True`. AI Armory never prompts; it only carries the flag so
the host can gate the call:

- In-process: `confirmation_required(toolsets)` returns the tool names.
- Over stdio: such tools carry `"_meta": {"ai-armory/needsConfirmation": true}`
  in `tools/list`. Clients that don't know the key ignore it.

`read_only=True` is sent as the standard MCP `readOnlyHint` annotation.

## The Google tool sets

Gmail, Google Calendar, Google Chat, Google Docs and Google Sheets across any
number of Google accounts, each its own tool set so you load only what you
need, or all five as the `google` group:

| Tool set   | Reads (read-only)                                   | Changes                                                                                  | Needs confirmation       |
|------------|-----------------------------------------------------|------------------------------------------------------------------------------------------|--------------------------|
| `gmail`    | `gmail_search`, `gmail_read`                        | `gmail_create_draft`, `gmail_modify` (read/unread, archive, star, labels)                 |                          |
| `calendar` | `calendar_events`, `calendar_draft_invite`          | `calendar_create_event` (nobody invited)                                                 | `calendar_send_invite`   |
| `chat`     | `chat_unread`, `chat_spaces`, `chat_read`, `chat_search`, `chat_draft` | `chat_mark_read` (the user's own read state)                          | `chat_send`              |
| `docs`     | `docs_search`, `docs_read`, `docs_draft_delete`     | `docs_beautify`, `docs_format`, `docs_replace_text`, `docs_insert`, `docs_insert_image`  | `docs_delete`            |
| `sheets`   | `sheets_search`, `sheets_read`, `sheets_draft_delete_tab` | `sheets_write`, `sheets_append`, `sheets_add_tab`, `sheets_format`                 | `sheets_delete_tab`      |

Searches (`gmail_search`, `calendar_events`, `chat_unread`, `chat_search`)
cover every account unless one is named; one account failing is left out with
a note. Everything else takes one account, defaulting to the default account
(for Chat, the first account with Chat). An account can be named by its label
or its email.

Nothing here sends email (drafts wait in Gmail's Drafts), trashes or deletes
mail, deletes a document or spreadsheet, or deletes a row. The tools that
return other people's text (mail, chat, docs) tell the model, in their
descriptions, not to follow instructions in it.

### Sends and deletes: draft, then confirm

Everything that sends something to other people or deletes something is two
tools. The first (`chat_draft`, `calendar_draft_invite`, `docs_draft_delete`,
`sheets_draft_delete_tab`) works out exactly what would happen, changes
nothing, and returns a `draft_id` and a `summary` to put to the user. The
second (`chat_send`, `calendar_send_invite`, `docs_delete`,
`sheets_delete_tab`) takes only the `draft_id`, so what happens is exactly what
was drafted, and is declared `needs_confirmation`. A deletion is pinned to the
document revision it was worked out from, so if the doc changed in between,
nothing is deleted. A draft is used once at most and lapses after
`draft_minutes`.

With `host_approval` on (the default), the confirming tool also refuses any
draft the host hasn't approved, so even a misconfigured gate can't send:

```python
from ai_armory.toolsets.google import approve, pending

async def can_use_tool(name, args, ctx):          # e.g. the SDK's can_use_tool
    if name in gated:                             # confirmation_required(toolsets)
        draft = pending(args["draft_id"])         # None if lapsed or used
        if draft and await ask_user(draft.summary + " Go ahead?"):
            approve(args["draft_id"])
            return PermissionResultAllow()
        return PermissionResultDeny(message="Not confirmed.")
    ...
```

Standalone MCP clients can't call `approve`; if your client asks you before
each tool call it hasn't been told to allow (Claude Desktop and Claude Code
do), set `host_approval = false` and leave the confirming tools off its
always-allow list, so that prompt is the confirmation.

### Configure

1. In a Google Cloud project, create an OAuth client of type *Desktop app*,
   download its JSON, and turn on the APIs you'll use: Gmail, Google Calendar,
   Google Drive, Google Docs, Google Sheets, and for Chat the Google Chat API
   (with a Chat app configured on its Configuration page) and the People API.
2. List the accounts in `~/.config/ai-armory/google.toml`, or any file named by
   `$AI_ARMORY_GOOGLE_CONFIG`:

   ```toml
   default = "work"                  # gets whatever is made without naming an account; default: the first
   timezone = "Europe/London"        # for calendar events and the times shown; default UTC
   token_dir = "google"              # where sign-ins are kept, relative to this file; this is the default
   client_secret = "~/Downloads/client_secret.json"  # used only to sign in
   # user_name = "Sam"               # how results name the user; default "the user"
   # host_approval = true            # see above
   # draft_minutes = 10
   # sign_in_command = "python -m ai_armory.toolsets.google login {label}"  # shown when a sign-in is needed
   # image_folder = "AI Armory doc images"   # Drive folder for images from this machine
   # blocked_paths = ["~/private"]   # never uploaded by docs_insert_image, besides keys, tokens and secrets folders

   [[accounts]]
   label = "work"                    # what the tools take as account: lowercase letters, digits, - and _
   email = "me@example.com"          # optional; lets the model name the account by email
   description = "for work"          # optional; passed to the model in the account field's description
   chat = true                       # a Workspace account with Google Chat

   [[accounts]]
   label = "personal"
   ```

3. Sign each account in. This opens a browser and stores the account's token
   in `<token_dir>/google-<label>.json`, readable only by you:

   ```sh
   python -m ai_armory.toolsets.google login work
   python -m ai_armory.toolsets.google            # list accounts and whether each is signed in
   python -m ai_armory.toolsets.google check-chat # check Google Chat works
   ```

   An account with `chat = true` also asks for Google Chat's narrow scopes
   (read-only, create-only for sending and starting a DM, and the user's own
   read state). If a Workspace admin blocks that, sign-in offers to try again
   without reading messages, then without Chat; `chat_unread` then falls back
   to Chat's notification emails. Sign in again to replace a token, e.g. after
   the tools gain a permission; the tools say so when one is missing.

A host running the tools in-process can configure them in code instead,
before loading them (their schemas list the accounts as they are when
loaded):

```python
from ai_armory.toolsets.google import Account, GoogleSettings, configure

configure(GoogleSettings(
    accounts=[Account("work", "me@example.com", "for work", chat=True), Account("personal")],
    token_dir="~/my-host/secrets",
    sign_in_command="my-host accounts login {label}",
))
toolsets = load_many(["google"])
```

`GoogleSettings.describe()` gives a line on the accounts for the host's own
prompt, and `ai_armory.toolsets.google.chat.news(label)` the unread DMs and
mentions, for the host's own alerts.

## Adding a tool set

1. Create `src/ai_armory/toolsets/<name>.py` with a module-level `toolset`:

   ```python
   from ai_armory import ToolError, ToolSet

   toolset = ToolSet("notes", "Keep short notes.")

   @toolset.tool(
       "add",
       "Save a note.",
       {"properties": {"text": {"type": "string"}}, "required": ["text"]},
       needs_confirmation=True,
   )
   async def add(args):
       if not args["text"].strip():
           raise ToolError("Empty note")  # becomes an error result for the model
       ...
       return "Saved."  # a string, or anything JSON-serialisable
   ```

   Tools are exposed with the tool set name as a prefix (`notes_add`). The
   input schema is a JSON Schema object; `type: object` is filled in for you.

2. Register it in `BUILTIN` in `src/ai_armory/registry.py`.
3. Add an extra for its dependencies in `pyproject.toml` (import them inside
   the tool set module, not anywhere shared).
4. Add tests under `tests/`.

Tool sets can also live in other packages: point an entry point in the
`ai_armory.toolsets` group at a `ToolSet` object and AI Armory will find it.

## Layout

```
src/ai_armory/
  core.py        Tool, ToolSet, ToolError, call(): the neutral definitions
  registry.py    finding and loading tool sets by name
  adapters/
    claude_sdk.py  in-process Claude Agent SDK server
    stdio.py       standalone stdio MCP server
  toolsets/      one module per tool set
    google/      the Google tool sets: settings, shared clients, drafts, sign-in,
                 and gmail, calendar, chat, docs (+ docs_write, docs_images), sheets
  cli.py         the `ai-armory` command
```

## Development

```sh
pip install -e ".[dev]"
pytest
```

## Licence

MIT, see [LICENSE](LICENSE).
