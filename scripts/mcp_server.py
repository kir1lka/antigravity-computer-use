#!/usr/bin/env python3
"""
mcp_server.py — Zero-dependency MCP Server for Windows Computer Use (cu.py).

Supports:
  - Stdio transport (default, launched directly by Antigravity Language Server in the user's desktop session)
  - Streamable HTTP transport (--http --host 127.0.0.1 --port 8765) for manual/external hosting
  - Native inline MCP image content blocks (base64 JPEG) so the agent sees screenshots immediately
    with zero extra read_file roundtrips!
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure scripts dir is on sys.path and NEVER pollute stdout
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cu  # noqa: E402

SERVER_NAME = "computer-use"
SERVER_VERSION = "1.0.0"

# =============================================================================
# TOOL SCHEMAS
# =============================================================================

TOOLS: List[Dict[str, Any]] = [
    {
        "name": "cu_state",
        "description": "Fast desktop status (~1ms): screen size, cursor position (0..1000 & px), active window title/exe/hwnd, and clipboard preview.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "cu_windows",
        "description": "List all visible top-level Windows desktop windows with HWND, exe, title, state, active flag, and normalized rects.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optional case-insensitive substring filter on title or exe name."}
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_shot",
        "description": "Capture a screenshot of the live Windows desktop and return it directly as an inline image. Supports 0..1000 precision grid + live cursor crosshair, zoomed crop regions, or active window capture.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "grid": {"type": "boolean", "default": True, "description": "Draw 0..1000 normalized coordinate grid + red cursor crosshair."},
                "cursor": {"type": "boolean", "default": False, "description": "Draw cursor crosshair without grid."},
                "window": {"type": "boolean", "default": False, "description": "Capture only the active foreground window."},
                "hwnd": {"type": "integer", "description": "Capture a specific window by HWND."},
                "crop": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": "Zoom crop [x, y, w, h] in 0..1000 normalized coords (or px if px=true). Grid labels stay mapped to global 0..1000 coords.",
                },
                "px": {"type": "boolean", "default": False, "description": "Interpret crop in physical pixels."},
                "width": {"type": "integer", "default": 1280, "description": "Resize max width (default 1280, 0 for native)."},
                "quality": {"type": "integer", "default": 82, "description": "JPEG quality (default 82)."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_som",
        "description": "Set-of-Mark UI Automation scan: detects interactive controls (buttons, inputs, tabs, links, menu items) via native uia_scan.exe, returns an annotated screenshot with numbered badges [1], [2]... and a compact element list so you can click/type by element id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {"type": "integer", "description": "Optional window HWND (defaults to foreground window)."},
                "desktop": {"type": "boolean", "default": False, "description": "Scan full desktop / taskbar instead of foreground window."},
                "max": {"type": "integer", "default": 80, "description": "Maximum elements to collect."},
                "depth": {"type": "integer", "default": 10, "description": "Maximum UIA tree depth."},
                "text": {"type": "boolean", "default": False, "description": "Also include static Text labels."},
                "no_image": {"type": "boolean", "default": False, "description": "Return element list only without screenshot."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_click",
        "description": "Click mouse at normalized (x, y) 0..1000 coordinates OR Set-of-Mark element id. Optionally captures updated screen right after.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "number", "description": "X coordinate (0..1000 unless px=true)."},
                "y": {"type": "number", "description": "Y coordinate (0..1000 unless px=true)."},
                "id": {"type": "integer", "description": "Set-of-Mark element ID from last cu_som scan."},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "clicks": {"type": "integer", "default": 1, "description": "1=click, 2=double-click, 3=triple-click."},
                "smooth": {"type": "boolean", "default": False},
                "px": {"type": "boolean", "default": False},
                "shot": {"type": "boolean", "default": True, "description": "Return updated grid screenshot after click."},
                "grid": {"type": "boolean", "default": True},
                "after_wait": {"type": "number", "default": 0.2},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_move",
        "description": "Move mouse cursor (hover) to normalized (x, y) or Set-of-Mark id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "id": {"type": "integer"},
                "smooth": {"type": "boolean", "default": True},
                "duration": {"type": "number", "default": 0.14},
                "px": {"type": "boolean", "default": False},
                "shot": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_drag",
        "description": "Smooth mouse drag from (x1, y1) to (x2, y2) in 0..1000 coordinates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x1": {"type": "number"},
                "y1": {"type": "number"},
                "x2": {"type": "number"},
                "y2": {"type": "number"},
                "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
                "duration": {"type": "number", "default": 0.25},
                "px": {"type": "boolean", "default": False},
                "shot": {"type": "boolean", "default": True},
            },
            "required": ["x1", "y1", "x2", "y2"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_scroll",
        "description": "Scroll mouse wheel (+up/-down, or +right/-left with horiz=true), optionally moving to (x, y) or id first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "amount": {"type": "integer", "description": "Wheel notches (e.g. -5 down, +5 up)."},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "id": {"type": "integer"},
                "horiz": {"type": "boolean", "default": False},
                "px": {"type": "boolean", "default": False},
                "shot": {"type": "boolean", "default": True},
            },
            "required": ["amount"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_input_text",
        "description": "Enter Unicode text (Russian/Cyrillic, English, symbols, emoji) regardless of Windows keyboard layout. Mode 'paste' uses instant Win32 clipboard + Ctrl+V (best for long text/URLs); mode 'type' uses SendInput KEYEVENTF_UNICODE.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to enter."},
                "mode": {"type": "string", "enum": ["paste", "type"], "default": "paste"},
                "id": {"type": "integer", "description": "Optional SoM element ID to click before typing."},
                "x": {"type": "number", "description": "Optional X coord (0..1000) to click before typing."},
                "y": {"type": "number", "description": "Optional Y coord (0..1000) to click before typing."},
                "clear": {"type": "boolean", "default": False, "description": "Select all (Ctrl+A) and clear before typing."},
                "enter": {"type": "boolean", "default": False, "description": "Press Enter after text."},
                "restore_clipboard": {"type": "boolean", "default": True, "description": "Restore previous clipboard when mode='paste'."},
                "px": {"type": "boolean", "default": False},
                "shot": {"type": "boolean", "default": True},
                "after_wait": {"type": "number", "default": 0.25},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_key",
        "description": "Press a single key (enter, esc, tab, backspace, delete, up, down, left, right, f1..f12, win, space, etc.) or a hotkey combo (e.g. 'ctrl+l', 'ctrl+shift+t', 'alt+tab', 'win+r').",
        "inputSchema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Single key name OR plus-separated combo like 'ctrl+l'."},
                "count": {"type": "integer", "default": 1, "description": "Repeat count for single key."},
                "shot": {"type": "boolean", "default": True},
                "after_wait": {"type": "number", "default": 0.2},
            },
            "required": ["key"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_window",
        "description": "Focus, launch, maximize, minimize, restore, move/resize, or close a Windows app/window.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["focus", "launch", "max", "min", "restore", "close", "move"]},
                "target": {"type": "string", "description": "Window title/exe substring or HWND (or exe/URL/path for 'launch')."},
                "args": {"type": "string", "description": "Optional CLI arguments for 'launch'."},
                "rect": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                    "description": "For action='move': [x, y, w, h] in 0..1000 coords (or px if px=true).",
                },
                "px": {"type": "boolean", "default": False},
                "shot": {"type": "boolean", "default": True},
                "after_wait": {"type": "number", "default": 0.4},
            },
            "required": ["action", "target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_clipboard",
        "description": "Read or write the Windows clipboard (UTF-8/Cyrillic safe).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["get", "set"]},
                "text": {"type": "string", "default": ""},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_wait_change",
        "description": "Wait until the screen (or a 0..1000 region) visually changes instead of blind sleeping. Returns updated screenshot when changed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout": {"type": "number", "default": 10.0},
                "interval": {"type": "number", "default": 0.15},
                "threshold": {"type": "number", "default": 2.5},
                "region": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                "shot": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_find_img",
        "description": "Locate a visual template image on screen using OpenCV template matching, optionally clicking it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "template": {"type": "string", "description": "Absolute path to template PNG/JPG."},
                "threshold": {"type": "number", "default": 0.85},
                "click_mode": {"type": "string", "enum": ["none", "click", "dclick", "rclick"], "default": "none"},
            },
            "required": ["template"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cu_batch",
        "description": "Turbo multi-step action pipeline executed in ONE call. Each step can be a shorthand string (e.g. 'focus:Chrome', 'hotkey:ctrl+l', 'paste:https://google.com', 'key:enter', 'wait:1.2', 'shot:grid') or a JSON object ({op, ...}). Automatically returns the last captured screenshot inline!",
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": "Ordered list of step strings or step objects.",
                    "items": {},
                },
                "px": {"type": "boolean", "default": False},
            },
            "required": ["steps"],
            "additionalProperties": False,
        },
    },
]

# =============================================================================
# TOOL EXECUTION & IMAGE ATTACHMENT
# =============================================================================

def _attach_image_if_present(path_str: Optional[str], content: List[Dict[str, Any]]) -> None:
    if not path_str:
        return
    p = Path(path_str)
    if not p.exists():
        return
    raw = p.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    content.append({"type": "image", "data": b64, "mimeType": mime})

def _extract_last_image_path(res: Any) -> Optional[str]:
    if isinstance(res, dict):
        if "path" in res and isinstance(res["path"], str):
            return res["path"]
        if isinstance(res.get("image"), dict) and isinstance(res["image"].get("path"), str):
            return res["image"]["path"]
        if isinstance(res.get("shot"), dict):
            return res["shot"].get("path")
        if isinstance(res.get("results"), list):
            for item in reversed(res["results"]):
                found = _extract_last_image_path(item)
                if found:
                    return found
    return None

def execute_tool(name: str, args: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], bool]:
    args = args or {}
    t0 = time.perf_counter()
    try:
        if name == "cu_state":
            m = cu.get_screen_metrics()
            cpx, cpy = cu.get_cursor_pos()
            fg = cu.get_window_info(int(cu.user32.GetForegroundWindow() or 0))
            cb = cu.clipboard_get()
            res: Dict[str, Any] = {
                "ok": True,
                "screen": [m["w"], m["h"]],
                "virtual_screen": [m["vx"], m["vy"], m["vw"], m["vh"]],
                "cursor_norm": list(cu.px_to_norm(cpx, cpy)),
                "cursor_px": [cpx, cpy],
                "active_window": fg,
                "clipboard_preview": cb[:200] + ("..." if len(cb) > 200 else ""),
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            }

        elif name == "cu_windows":
            wins = cu.list_windows(args.get("filter"))
            res = {"ok": True, "count": len(wins), "windows": wins}

        elif name == "cu_shot":
            res = cu.do_shot(
                grid=bool(args.get("grid", True)),
                cursor=bool(args.get("cursor", False)),
                window=bool(args.get("window", False)),
                hwnd=args.get("hwnd"),
                crop=args.get("crop"),
                use_px=bool(args.get("px", False)),
                width=int(args.get("width", 1280)),
                quality=int(args.get("quality", 82)),
            )

        elif name == "cu_som":
            res = cu.do_som(
                hwnd=args.get("hwnd"),
                desktop=bool(args.get("desktop", False)),
                max_el=int(args.get("max", 80)),
                depth=int(args.get("depth", 10)),
                include_text=bool(args.get("text", False)),
                no_image=bool(args.get("no_image", False)),
            )

        elif name == "cu_click":
            px, py, el = cu.resolve_coords(args.get("x"), args.get("y"), args.get("id"), bool(args.get("px", False)))
            cu.click_px(px, py, button=str(args.get("button", "left")), clicks=int(args.get("clicks", 1)), smooth=bool(args.get("smooth", False)))
            res = {"ok": True, "px": [px, py], "norm": list(cu.px_to_norm(px, py)), "element": el["name"] if el else None}
            if args.get("shot", True):
                time.sleep(float(args.get("after_wait", 0.2)))
                res["shot"] = cu.do_shot(grid=bool(args.get("grid", True)))

        elif name == "cu_move":
            px, py, _ = cu.resolve_coords(args.get("x"), args.get("y"), args.get("id"), bool(args.get("px", False)))
            cu.move_mouse_px(px, py, smooth=bool(args.get("smooth", True)), duration=float(args.get("duration", 0.14)))
            res = {"ok": True, "px": [px, py], "norm": list(cu.px_to_norm(px, py))}
            if args.get("shot", False):
                time.sleep(0.1)
                res["shot"] = cu.do_shot(grid=True)

        elif name == "cu_drag":
            use_px = bool(args.get("px", False))
            x1, y1, _ = cu.resolve_coords(args["x1"], args["y1"], None, use_px)
            x2, y2, _ = cu.resolve_coords(args["x2"], args["y2"], None, use_px)
            cu.drag_px(x1, y1, x2, y2, button=str(args.get("button", "left")), duration=float(args.get("duration", 0.25)))
            res = {"ok": True, "from_px": [x1, y1], "to_px": [x2, y2]}
            if args.get("shot", True):
                time.sleep(0.2)
                res["shot"] = cu.do_shot(grid=True)

        elif name == "cu_scroll":
            px = py = None
            if args.get("id") is not None or (args.get("x") is not None and args.get("y") is not None):
                px, py, _ = cu.resolve_coords(args.get("x"), args.get("y"), args.get("id"), bool(args.get("px", False)))
            cu.scroll_wheel(int(args["amount"]), px, py, horiz=bool(args.get("horiz", False)))
            res = {"ok": True, "amount": int(args["amount"]), "horiz": bool(args.get("horiz", False))}
            if args.get("shot", True):
                time.sleep(0.2)
                res["shot"] = cu.do_shot(grid=True)

        elif name == "cu_input_text":
            if args.get("id") is not None or (args.get("x") is not None and args.get("y") is not None):
                px, py, _ = cu.resolve_coords(args.get("x"), args.get("y"), args.get("id"), bool(args.get("px", False)))
                cu.click_px(px, py)
                time.sleep(0.06)
            if args.get("clear", False):
                cu.press_hotkey("ctrl+a")
                time.sleep(0.03)
                cu.press_key("backspace")
                time.sleep(0.03)
            txt = str(args["text"])
            mode = str(args.get("mode", "paste"))
            if mode == "type":
                cu.type_unicode(txt)
            else:
                cu.paste_text(txt, restore=bool(args.get("restore_clipboard", True)))
            if args.get("enter", False):
                time.sleep(0.04)
                cu.press_key("enter")
            res = {"ok": True, "mode": mode, "chars": len(txt), "enter": bool(args.get("enter", False))}
            if args.get("shot", True):
                time.sleep(float(args.get("after_wait", 0.25)))
                res["shot"] = cu.do_shot(grid=True)

        elif name == "cu_key":
            k = str(args["key"]).strip()
            if "+" in k and len(k) > 1:
                cu.press_hotkey(k)
            else:
                cu.press_key(k, count=int(args.get("count", 1)))
            res = {"ok": True, "key": k}
            if args.get("shot", True):
                time.sleep(float(args.get("after_wait", 0.2)))
                res["shot"] = cu.do_shot(grid=True)

        elif name == "cu_window":
            act = str(args["action"]).lower()
            target = str(args["target"])
            if act == "focus":
                w = cu.focus_window(target)
                res = {"ok": True, "action": act, "window": w}
            elif act == "launch":
                if args.get("args"):
                    import subprocess
                    subprocess.Popen(f'"{target}" {args["args"]}', shell=True, creationflags=0x00000008)
                else:
                    os.startfile(target)
                res = {"ok": True, "action": act, "launched": target}
            else:
                w = cu.window_op(target, act, args.get("rect"), use_px=bool(args.get("px", False)))
                res = {"ok": True, "action": act, "window": w}
            if args.get("shot", True):
                time.sleep(float(args.get("after_wait", 0.4)))
                res["shot"] = cu.do_shot(grid=True)

        elif name == "cu_clipboard":
            if args["action"] == "get":
                res = {"ok": True, "text": cu.clipboard_get()}
            else:
                txt = str(args.get("text", ""))
                res = {"ok": cu.clipboard_set(txt), "chars": len(txt)}

        elif name == "cu_wait_change":
            res = cu.do_wait_change(
                timeout=float(args.get("timeout", 10.0)),
                interval=float(args.get("interval", 0.15)),
                threshold=float(args.get("threshold", 2.5)),
                region=args.get("region"),
            )
            if args.get("shot", True):
                res["shot"] = cu.do_shot(grid=True)

        elif name == "cu_find_img":
            cm = args.get("click_mode", "none")
            res = cu.do_find_img(str(args["template"]), threshold=float(args.get("threshold", 0.85)), click_mode=None if cm == "none" else cm)

        elif name == "cu_batch":
            steps = args.get("steps", [])
            use_px = bool(args.get("px", False))
            results = [cu.run_batch_step(st, use_px=use_px) for st in steps]
            res = {"ok": True, "steps": len(results), "results": results, "elapsed_ms": int((time.perf_counter() - t0) * 1000)}

        else:
            return [{"type": "text", "text": json.dumps({"ok": False, "error": f"Unknown tool: {name}"}, ensure_ascii=False)}], True

        content: List[Dict[str, Any]] = [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}]
        img_path = _extract_last_image_path(res)
        _attach_image_if_present(img_path, content)
        return content, False

    except Exception as exc:
        err = {"ok": False, "error": str(exc)}
        return [{"type": "text", "text": json.dumps(err, ensure_ascii=False)}], True

# =============================================================================
# JSON-RPC 2.0 MCP PROTOCOL HANDLER
# =============================================================================

def handle_rpc(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    # Notifications have no id -> no response
    if msg_id is None:
        return None

    if method == "initialize":
        req_ver = params.get("protocolVersion", "2025-03-26")
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": req_ver,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Native Windows Computer Use server. All coordinates default to 0..1000 normalized "
                    "(DPI-aware across 1080p/1440p/4K). Use cu_som for Windows/Electron UI controls by ID, "
                    "cu_shot for visual grid, and cu_batch to chain multiple actions fast."
                ),
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        t_name = params.get("name", "")
        t_args = params.get("arguments") or {}
        content, is_error = execute_tool(t_name, t_args)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": content, "isError": is_error},
        }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }

# =============================================================================
# TRANSPORTS: STDIO & STREAMABLE HTTP
# =============================================================================

def run_stdio() -> None:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    while True:
        raw_line = stdin.readline()
        if not raw_line:
            break
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except Exception as exc:
            err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}
            stdout.write((json.dumps(err, ensure_ascii=False) + "\n").encode("utf-8"))
            stdout.flush()
            continue

        try:
            resp = handle_rpc(msg)
        except Exception as exc:
            sys.stderr.write(f"[computer-use mcp] error: {exc}\n{traceback.format_exc()}\n")
            sys.stderr.flush()
            resp = {
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "error": {"code": -32603, "message": str(exc)},
            } if msg.get("id") is not None else None

        if resp is not None:
            out = (json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8")
            stdout.write(out)
            stdout.flush()

def run_http(host: str, port: int) -> None:
    class MCPHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stderr.write(f"[cu-mcp-http] {fmt % args}\n")

        def do_POST(self) -> None:
            if self.path not in ("/mcp", "/"):
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                msg = json.loads(body.decode("utf-8"))
            except Exception as exc:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))
                return

            resp = handle_rpc(msg)
            if resp is None:
                self.send_response(202)
                self.end_headers()
                return
            payload = json.dumps(resp, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            info = json.dumps({"ok": True, "server": SERVER_NAME, "version": SERVER_VERSION, "endpoint": "/mcp"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(info)))
            self.end_headers()
            self.wfile.write(info)

    srv = ThreadingHTTPServer((host, port), MCPHandler)
    sys.stderr.write(f"[{SERVER_NAME}] Streamable HTTP MCP server listening on http://{host}:{port}/mcp\n")
    sys.stderr.flush()
    srv.serve_forever()

def main() -> None:
    ap = argparse.ArgumentParser(description="Computer Use MCP Server")
    ap.add_argument("--http", action="store_true", help="Run as Streamable HTTP MCP server instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    if args.http:
        run_http(args.host, args.port)
    else:
        run_stdio()

if __name__ == "__main__":
    main()
