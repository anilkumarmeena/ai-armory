<div align="center">

<img src="assets/logo.svg" width="72" alt="">

# 🍎 Mac

**Control of the Mac the tools run on, through macOS's own programs: nothing to install or configure.**

[🏠 Home](../README.md) · [🔌 Serving](serving.md) · [📬 Google](google.md) · **🍎 Mac** · [🧩 Writing a tool set](toolsets.md)

</div>

---

The `mac` tool set **works only on macOS**. Elsewhere it still loads, but each call says it needs macOS.

```sh
ai-armory serve --toolsets mac
```

## 🧰 Tools

| Tool | 👀 Read-only | What it does | What it runs |
|---|:-:|---|---|
| `mac_notify` | | Shows a notification | `osascript`, `display notification` |
| `mac_open` | | Opens an app by name, or an `http(s)://` link | `open -a` / `open` |
| `mac_run_shortcut` | | Runs an Apple Shortcut by exact name, with optional text input | `shortcuts run` |
| `mac_list_shortcuts` | ✅ | Lists the Apple Shortcuts | `shortcuts list` |
| `mac_set_volume` | | Sets the output volume | `osascript`, `set volume` |
| `mac_copy` | | Puts text on the clipboard, replacing what's there | `pbcopy` |
| `mac_read_clipboard` | ✅ | Reads the clipboard's text | `pbpaste` |

- `mac_run_shortcut` lets a shortcut run for **up to 2 minutes**.
- `mac_set_volume` keeps the level **between 0 and 100**.
- `mac_copy` passes the text on stdin as UTF-8.
- `mac_read_clipboard` returns **up to 20,000 characters**; an image or files read as no text.

## 🔐 Safety

> [!IMPORTANT]
> **Arguments go to programs as argv, never through a shell**, and a notification's text reaches AppleScript as arguments, not as script.

- What `mac_read_clipboard` returns **came from anywhere**: a host should treat it as data, like an email's text, **never as instructions**.
- **Nothing here needs confirmation.** A host that wants a yes before opening apps or running shortcuts can gate them itself.
- The first time, macOS may ask whether the program running the server (your terminal, or the MCP client) may **control the Mac** or **show notifications**.

## 🔔 Notifications from the host

A host can also show a notification of its own, outside any tool call:

```python
from ai_armory.toolsets.mac import notify

await notify("Build", "Finished.")
```

---

<div align="center">

[← 📬 Google](google.md) &nbsp;·&nbsp; [🏠 Home](../README.md) &nbsp;·&nbsp; [🧩 Writing a tool set →](toolsets.md)

</div>
