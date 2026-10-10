<div align="center">

<img src="assets/logo.svg" width="72" alt="">

# 🧩 Writing a tool set

**How to add a tool set to AI Armory, or ship one from your own package, and where the code lives.**

[🏠 Home](../README.md) · [🔌 Serving](serving.md) · [📬 Google](google.md) · [🍎 Mac](mac.md) · **🧩 Writing a tool set**

</div>

---

## ➕ Adding a tool set

**1. Create** — `src/ai_armory/toolsets/<name>.py` with a module-level `toolset`:

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

- Tools are exposed with the **tool set name as a prefix** (`notes_add`).
- The input schema is a JSON Schema object; `type: object` is filled in for you.
- Mark tools that only read with `read_only=True`, and ones that act on the world with `needs_confirmation=True` (see [🔌 Serving › Confirmation](serving.md#-confirmation)).

**2. Register** — add it to `BUILTIN` in [`src/ai_armory/registry.py`](../src/ai_armory/registry.py).

**3. Add an extra** — for its dependencies, in [`pyproject.toml`](../pyproject.toml).

> [!WARNING]
> Import a tool set's dependencies **inside its own module**, not anywhere shared. Tool sets are imported only when asked for, so one with missing dependencies never breaks the others.

**4. Add tests** — under [`tests/`](../tests/).

## 🔌 Tool sets from other packages

> [!TIP]
> **Tool sets can also live in other packages:** point an entry point in the **`ai_armory.toolsets`** group at a `ToolSet` object and AI Armory will find it.

```toml
# in your package's pyproject.toml
[project.entry-points."ai_armory.toolsets"]
notes = "my_package.notes:toolset"
```

## 📦 Layout

```text
src/ai_armory/
├── core.py            Tool, ToolSet, ToolError, call(): the neutral definitions
├── registry.py        finding and loading tool sets by name
├── cli.py             the `ai-armory` command
├── adapters/
│   ├── claude_sdk.py  in-process Claude Agent SDK server
│   └── stdio.py       standalone stdio MCP server
└── toolsets/          one module per tool set
    ├── clock.py
    ├── mac.py         notifications, apps and links, Apple Shortcuts, volume, clipboard
    └── google/        settings, shared clients, drafts, sign-in,
                       and gmail, calendar, chat, docs (+ docs_write, docs_images), sheets,
                       slides, drive
```

## 🧪 Development

```sh
pip install -e ".[dev]"
pytest
```

---

<div align="center">

[← 🍎 Mac](mac.md) &nbsp;·&nbsp; [🏠 Home](../README.md)

</div>
