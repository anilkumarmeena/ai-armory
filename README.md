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
```

Each tool set has its own extra holding only what it needs, so you install
the dependencies of the tool sets you use and nothing else.

## Use it standalone (stdio)

```sh
ai-armory list                        # see the tool sets
ai-armory serve --toolsets clock      # serve chosen tool sets over stdio
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
  cli.py         the `ai-armory` command
```

## Development

```sh
pip install -e ".[dev]"
pytest
```

## Licence

MIT, see [LICENSE](LICENSE).
