# Antigravity Computer Use (Windows Native CUA + MCP)

High-speed, zero-bloat **Windows Computer Use** skill & native **MCP server** for Google Antigravity (inspired by ChatGPT Operator / Claude Computer Use).

Control the live Windows desktop with **Per-Monitor DPI v2** precision (1080p / 1440p / 4K), **Set-of-Mark (SoM)** Windows UI Automation element tagging, **layout-independent Unicode/Cyrillic typing**, **OpenCV template matching**, and **turbo multi-step batching**.

---

## Features

- **Native MCP Server (`scripts/mcp_server.py`)** — Zero-dependency JSON-RPC 2.0 (Stdio + Streamable HTTP) exposing 15 native tools (`cu_shot`, `cu_som`, `cu_click`, `cu_input_text`, `cu_key`, `cu_window`, `cu_batch`, etc.) with **inline base64 JPEG screenshots** directly in tool responses.
- **Set-of-Mark UI Automation (`scripts/uia_scan.cs`)** — Tiny compiled C# .NET scanner (~50 ms) that extracts interactive controls (buttons, inputs, tabs, links, menus) and draws numbered `[1]`, `[2]`, ... badges on screen so the agent clicks by `id` instead of guessing pixels.
- **4K & High-DPI Native** — Calls `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` so coordinates map 1:1 on 125% / 150% / 200% scaling. Uses normalized `0..1000` coordinates + cyan precision grid + live cursor crosshair + zoomed `--crop` mode.
- **100% Cyrillic / Unicode / Multi-Layout Safe** — `type` uses Win32 `SendInput` + `KEYEVENTF_UNICODE` (works identically in EN and RU keyboard layouts), and `paste` writes UTF-16LE directly to the Win32 clipboard + hardware `Ctrl+V`.
- **Turbo Action Batching (`cu_batch`)** — Execute entire multi-step UI sequences (`focus` → `hotkey` → `paste` → `enter` → `wait` → `shot`) in a single tool call.
- **Window & Vision Tools** — List/focus/maximize/minimize/move/close windows, launch apps, OpenCV template matching (`cu_find_img`), and smart visual change waiting (`cu_wait_change`).

---

## Requirements

- **OS**: Windows 10 / 11 (x64)
- **Python**: 3.10+ with `pillow` (optional: `opencv-python-headless`, `numpy` for `find-img` and `wait-change`)
- **.NET Framework 4.x** (built into Windows — used to auto-compile `uia_scan.exe` via `csc.exe`)

```powershell
pip install pillow opencv-python-headless numpy
```

---

## Quick Install (Antigravity)

Run the one-click installer from PowerShell:

```powershell
.\install.ps1
```

This automatically:
1. Installs the skill into `~/.gemini/config/skills/computer-use/`
2. Compiles native `uia_scan.exe` using Windows' built-in `csc.exe`
3. Registers the `computer-use` MCP server in `~/.gemini/config/mcp_config.json` (preserving any existing MCP servers)

Restart Antigravity (or open a new chat) and all 15 `cu_*` tools will be active globally!

---

## CLI Usage (`scripts/cu.py`)

You can also run the engine directly from PowerShell:

```powershell
# Desktop status (~1 ms)
python scripts/cu.py state

# Screenshot with 0..1000 precision grid + cursor
python scripts/cu.py shot --grid

# Set-of-Mark UI Automation scan
python scripts/cu.py som

# Turbo multi-step batch
python scripts/cu.py batch "focus:Chrome" "wait:0.2" "hotkey:ctrl+t" "paste:hello world" "key:enter" "wait:1.2" "shot:grid"
```

See [references/commands.md](references/commands.md) for the complete CLI and JSON batch reference.

---

## Project Structure

```text
├── SKILL.md                  # Antigravity skill instructions & guardrails
├── install.ps1               # One-click global installer for Antigravity
├── references/
│   └── commands.md           # Full CLI, keymap & batch reference
└── scripts/
    ├── cu.py                 # Core Win32 + Pillow + OpenCV engine
    ├── mcp_server.py         # Zero-dependency MCP server (Stdio & HTTP)
    └── uia_scan.cs           # Native C# Windows UI Automation SoM scanner
```

## License

MIT — see [LICENSE](LICENSE).
