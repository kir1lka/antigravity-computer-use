---
name: computer-use
description: >-
  Full-desktop Windows Computer Use skill + native MCP server (like ChatGPT Operator /
  Claude Computer Use). Use this skill whenever the user asks to control the computer,
  interact with Windows desktop applications, click/drag/scroll/type in native apps
  (Chrome, Telegram, Discord, VS Code, Unity, Blender, Office, Explorer, Settings,
  games, etc.), manage windows, read what is on screen, or perform multi-app GUI
  workflows.
---

# Computer Use (Windows Native CUA + MCP)

Control the live Windows desktop fast and accurately.

## Transport Priority (CRITICAL)

1. **Native MCP Server `computer-use` (PREFERRED — Always use first!)**
   - Registered globally in `~/.gemini/config/mcp_config.json` ([mcp_server.py](./scripts/mcp_server.py)).
   - Runs directly via the Antigravity host Language Server (with native Windows desktop access, unlike `run_command`'s isolated shell) AND returns live screenshots **inline as MCP image blocks** (no extra `read_file` turn needed!).
   - Tools: `cu_state`, `cu_windows`, `cu_shot`, `cu_som`, `cu_click`, `cu_move`, `cu_drag`, `cu_scroll`, `cu_input_text`, `cu_key`, `cu_window`, `cu_clipboard`, `cu_wait_change`, `cu_find_img`, `cu_batch`.
2. **CLI Fallback (`cu.py`)** — Use [cu.py](./scripts/cu.py) via `run_command` ONLY if the `computer-use` MCP server tools are not currently loaded in `<mcp_servers>`. Note: `run_command` on Windows runs in an isolated non-interactive desktop, so if `cu.py state` returns `windows: []` / `active_window: null`, tell the user to refresh MCP Servers (**... > MCP Servers**) or start a fresh chat so the `computer-use` MCP server connects.

## Core Operating Loop (Observe -> Batch Act -> Verify)

1. **Observe (Pick the sharpest mode)**
   - **Native / Office / Electron / Settings / Browser chrome** — call **`cu_som`** first:
     - Scans interactive Windows UI Automation controls via compiled native `uia_scan.exe` (~50ms), draws numbered badges `[1]`, `[2]`, ..., and returns the annotated image inline.
     - Click/type directly using `id` with zero pixel guesswork!
   - **Custom canvases, games, or visual-only UIs** — call **`cu_shot(grid=true)`**:
     - Coordinates are **normalized `0..1000`** on both X and Y axes (Per-Monitor DPI v2 aware across 1080p/1440p/4K).
   - **Tiny icons or dense toolbars** — call **`cu_shot(crop=[x, y, w, h], grid=true)`**:
     - Grid labels inside the cropped zoom still map directly to **global `0..1000`** coordinates!

2. **Act Fast (Batch + Shortcuts First)**
   - **Batch predictable sequences** with **`cu_batch`** in ONE tool call (4–6x faster!):
     ```json
     {
       "steps": [
         "focus:Chrome",
         "wait:0.15",
         "hotkey:ctrl+l",
         "paste:https://google.com",
         "key:enter",
         "wait:1.2",
         "shot:grid"
       ]
     }
     ```
   - **Prefer keyboard & window tools** (`cu_window`, `cu_key` with `ctrl+l` / `ctrl+t` / `ctrl+s` / `alt+tab` / `win+r`) over slow clicking through menus.
   - **Russian / Unicode / Layout Safety**:
     - `cu_input_text(text="привет", mode="paste", enter=true)` writes UTF-16 directly to Win32 clipboard and sends hardware `Ctrl+V` (instant, works in EN and RU keyboard layouts, restores clipboard automatically).
     - `mode="type"` uses Win32 `SendInput` + `KEYEVENTF_UNICODE` (works even in inputs that block paste, layout-independent).
     - Use `clear=true` to wipe existing input (`Ctrl+A` + `Backspace`) before entering text.

3. **Verify & Recover**
   - Most action tools default to `shot=true` and return the updated screenshot inline — inspect it before proceeding.
   - Use `cu_wait_change(timeout=8, shot=true)` instead of guessing long sleeps while apps/pages load.
   - If a click misses or UI didn't respond:
     1. Call `cu_windows` / `cu_state` to verify foreground focus.
     2. Zoom in with `cu_shot(crop=[...], grid=true)` or switch to `cu_som` / keyboard (`tab`, `enter`, `esc`).
     3. For repeatable icons in non-UIA apps, use `cu_find_img(template=..., click_mode="click")`.

## Safety & Guardrails

- **Never bypass security gates**: Do not solve CAPTCHAs, click through UAC elevation prompts, or defeat access controls. Stop and ask the user.
- **Confirm high-impact actions**: Before irreversible or sensitive GUI actions — deleting files, sending messages/emails, making payments, changing passwords/security settings, uninstalling apps, or submitting external forms — pause and ask the user for explicit confirmation.

For full CLI flags, batch syntax, and key names, see [commands.md](./references/commands.md).
