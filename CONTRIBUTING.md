<div align="center">

<img src="docs/assets/logo.svg" width="72" alt="">

# 🤝 Contributing

**Thanks for helping! This page takes you from a fresh clone to a pull request that's easy to merge.**

[🏠 Home](README.md) · [🔌 Serving](docs/serving.md) · [📬 Google](docs/google.md) · [🍎 Mac](docs/mac.md) · [🧩 Writing a tool set](docs/toolsets.md)

</div>

---

> [!IMPORTANT]
> You need **Python 3.11+** and **[uv](https://docs.astral.sh/uv/)**. The tests run anywhere: nothing in them touches a real Google account or the real Mac.

## 🚀 Your first pull request

**1. Set up**: clone the repo and install every extra plus the test tools.

```sh
git clone https://github.com/anilkumarmeena/ai-armory
cd ai-armory
uv sync --extra dev
```

**2. Find your way around**: the code layout and how a tool set is built are in **[🧩 Writing a tool set](docs/toolsets.md)**.

**3. Make one change, with a test**: follow the [code style](#-code-style) below and add a test for the behaviour.

**4. Run the checks and open the PR**: the [checks](#-checks) must pass, then describe it as in [pull requests](#-pull-requests).

## ✅ Checks

| | Command | Must |
|:-:|---|---|
| 🧪 | `uv run pytest -q` | Pass, with no network and no real accounts |

## 🎨 Code style

- **Comments are short and explain why**, not what: the reason, the constraint or the trap. No history ("used to", "now") in comments; that belongs in the commit message.
- **Docstrings are one line**, saying what the function or module is for.
- **Tool descriptions are product text for the model.** Write them for the LLM that reads them: what the tool does, when to use it, what it returns, and what to treat as data rather than instructions.
- **Sends and deletes never happen directly.** They go through a drafting tool, then a confirming tool marked `needs_confirmation=True` that refuses any draft the host hasn't passed to `approve()` (see [`drafts.py`](src/ai_armory/toolsets/google/drafts.py) and [📬 Google › Draft, then confirm](docs/google.md#-sends-and-deletes-draft-then-confirm)).
- **Each tool set keeps its dependencies in its own extra** in [`pyproject.toml`](pyproject.toml), imported inside its own module, so one missing dependency never breaks the others.
- **Tests fake every external program and API.** Google clients are stood in for by [`tests/google_fakes.py`](tests/google_fakes.py) and the Mac's programs by a fake runner, so no test signs in, sends, opens or runs anything for real.
- **No personal data** in code, tests or docs: use placeholder names and `example.com`.

## 🧩 Adding a tool set

The steps (module, registry entry, extra, tests) and the entry-point route for tool sets that live in another package are in **[🧩 Writing a tool set](docs/toolsets.md)**.

## 🔀 Pull requests

| | What | How |
|:-:|---|---|
| 🎯 | **One change** | With a title that says what it does |
| 📝 | **A clear description** | What changed and why, and how you tested it |
| 📚 | **Docs updated** | The pages that describe the behaviour you changed, including tool tables |
| 🧹 | **Focused commits** | Rebased on `master` before asking for review |

## 🐛 Reporting bugs

Open an [issue](https://github.com/anilkumarmeena/ai-armory/issues) with **what you did**, **what you expected** and **what happened**, with any personal details removed.

---

<div align="center">

By contributing, you agree that your contributions are licensed under the **[MIT License](LICENSE)**.

[🏠 Home](README.md) &nbsp;·&nbsp; [🧩 Writing a tool set →](docs/toolsets.md)

</div>
