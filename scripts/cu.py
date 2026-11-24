#!/usr/bin/env python3
"""
cu.py — High-Speed Native Windows Computer Use Engine (Win32 + Pillow + UIA SoM + OpenCV)

Features:
  - Per-Monitor DPI v2 aware (exact 1:1 coordinate mapping on 100%-300% scaling & 4K displays)
  - Normalized 0..1000 coordinates by default (--px for raw pixels, --id N for Set-of-Mark UIA)
  - Layout-independent Unicode typing (Russian/Cyrillic, English, Emoji via KEYEVENTF_UNICODE)
  - Instant Win32 clipboard paste (CF_UNICODETEXT + VK Ctrl+V, works in any keyboard layout)
  - Native compiled C# UI Automation Set-of-Mark scanner (uia_scan.exe) with numbered visual overlay
  - Precision grid + live cursor crosshair + zoomed crop screenshots
  - Window manager (list, reliable focus, maximize/minimize/restore/move/close, launch)
  - OpenCV template matching (find-img) & smart visual change wait (wait-change)
  - Ultra-fast multi-step action batching (batch) to slash LLM roundtrips
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure UTF-8 stdout/stderr on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# =============================================================================
# 1. DPI AWARENESS & WIN32 SETUP
# =============================================================================

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
psapi = ctypes.windll.psapi

# Fix 64-bit pointer argtypes/restypes!
user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
user32.SetProcessDpiAwarenessContext.restype = wt.BOOL

def init_dpi_awareness() -> None:
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (HANDLE)-4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass

init_dpi_awareness()

CACHE_DIR = Path(tempfile.gettempdir()) / "cu_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
SOM_JSON_PATH = CACHE_DIR / "last_som.json"
DEFAULT_SHOT_PATH = CACHE_DIR / "screen.jpg"
DEFAULT_SOM_PATH = CACHE_DIR / "som.jpg"
SCRIPTS_DIR = Path(__file__).resolve().parent
UIA_EXE_PATH = SCRIPTS_DIR / "uia_scan.exe"
UIA_CS_PATH = SCRIPTS_DIR / "uia_scan.cs"

# Win32 Constants
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
SW_SHOW = 5
SW_MINIMIZE = 6
SW_RESTORE = 9

WM_CLOSE = 0x0010
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0
PW_RENDERFULLCONTENT = 2

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Win32 Structs for SendInput
ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wt.LONG),
        ("dy", wt.LONG),
        ("mouseData", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wt.DWORD),
        ("wParamL", wt.WORD),
        ("wParamH", wt.WORD),
    ]

class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUT_UNION)]

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD),
        ("biWidth", wt.LONG),
        ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD),
        ("biBitCount", wt.WORD),
        ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD),
        ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG),
        ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]

user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wt.UINT
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
user32.GetWindowRect.restype = wt.BOOL
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.IsWindowVisible.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.IsIconic.restype = wt.BOOL
user32.IsZoomed.argtypes = [wt.HWND]
user32.IsZoomed.restype = wt.BOOL
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.MoveWindow.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.BOOL]
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.OpenClipboard.argtypes = [wt.HWND]
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.SetClipboardData.restype = wt.HANDLE
user32.GetDC.argtypes = [wt.HWND]
user32.GetDC.restype = wt.HDC
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.PrintWindow.argtypes = [wt.HWND, wt.HDC, wt.UINT]
user32.PrintWindow.restype = wt.BOOL

kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.argtypes = [wt.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
kernel32.OpenProcess.restype = wt.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.GetCurrentThreadId.restype = wt.DWORD

gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.BitBlt.restype = wt.BOOL
gdi32.GetDIBits.argtypes = [wt.HDC, wt.HBITMAP, wt.UINT, wt.UINT, ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wt.UINT]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]

# =============================================================================
# 2. VIRTUAL KEY TABLE
# =============================================================================

VK_MAP: Dict[str, int] = {
    "backspace": 0x08, "bs": 0x08, "back": 0x08,
    "tab": 0x09,
    "enter": 0x0D, "return": 0x0D, "ret": 0x0D,
    "shift": 0x10, "lshift": 0xA0, "rshift": 0xA1,
    "ctrl": 0x11, "control": 0x11, "lctrl": 0xA2, "rctrl": 0xA3,
    "alt": 0x12, "menu": 0x12, "lalt": 0xA4, "ralt": 0xA5,
    "pause": 0x13, "capslock": 0x14, "caps": 0x14,
    "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "spacebar": 0x20,
    "pgup": 0x21, "pageup": 0x21, "prior": 0x21,
    "pgdn": 0x22, "pagedown": 0x22, "next": 0x22,
    "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "prtsc": 0x2C, "snapshot": 0x2C,
    "insert": 0x2D, "ins": 0x2D,
    "delete": 0x2E, "del": 0x2E,
    "win": 0x5B, "lwin": 0x5B, "super": 0x5B, "meta": 0x5B, "cmd": 0x5B,
    "rwin": 0x5C,
    "apps": 0x5D, "context": 0x5D,
    "numlock": 0x90, "scrolllock": 0x91,
    "mute": 0xAD, "voldown": 0xAE, "volup": 0xAF,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2, "media_play": 0xB3,
    ";": 0xBA, "semicolon": 0xBA,
    "=": 0xBB, "equal": 0xBB, "plus": 0xBB,
    ",": 0xBC, "comma": 0xBC,
    "-": 0xBD, "minus": 0xBD,
    ".": 0xBE, "period": 0xBE,
    "/": 0xBF, "slash": 0xBF,
    "`": 0xC0, "grave": 0xC0, "tilde": 0xC0,
    "[": 0xDB, "lbracket": 0xDB,
    "\\": 0xDC, "backslash": 0xDC,
    "]": 0xDD, "rbracket": 0xDD,
    "'": 0xDE, "quote": 0xDE,
}
for _i in range(1, 25):
    VK_MAP[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK_MAP[_c] = ord(_c.upper())
for _d in "0123456789":
    VK_MAP[_d] = ord(_d)

EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
    0x2C, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D, 0xA3, 0xA5,
}

# =============================================================================
# 3. SCREEN & COORDINATE HELPERS
# =============================================================================

def get_screen_metrics() -> Dict[str, int]:
    return {
        "w": max(1, int(user32.GetSystemMetrics(SM_CXSCREEN))),
        "h": max(1, int(user32.GetSystemMetrics(SM_CYSCREEN))),
        "vx": int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN)),
        "vy": int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN)),
        "vw": max(1, int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))),
        "vh": max(1, int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))),
    }

def get_cursor_pos() -> Tuple[int, int]:
    pt = wt.POINT()
    if user32.GetCursorPos(ctypes.byref(pt)):
        return int(pt.x), int(pt.y)
    return 0, 0

def norm_to_px(nx: float, ny: float) -> Tuple[int, int]:
    m = get_screen_metrics()
    px = int(round(nx * (m["w"] - 1) / 1000.0))
    py = int(round(ny * (m["h"] - 1) / 1000.0))
    return max(m["vx"], min(m["vx"] + m["vw"] - 1, px)), max(m["vy"], min(m["vy"] + m["vh"] - 1, py))

def px_to_norm(px: int, py: int) -> Tuple[int, int]:
    m = get_screen_metrics()
    nx = int(round(px * 1000.0 / max(1, m["w"])))
    ny = int(round(py * 1000.0 / max(1, m["h"])))
    return nx, ny

def load_som_element(el_id: int) -> Dict[str, Any]:
    if not SOM_JSON_PATH.exists():
        raise RuntimeError(f"No cached Set-of-Mark data found ({SOM_JSON_PATH}). Run 'cu.py som' first.")
    data = json.loads(SOM_JSON_PATH.read_text(encoding="utf-8"))
    for el in data.get("elements", []):
        if el.get("id") == el_id:
            return el
    raise ValueError(f"Element ID {el_id} not found in last SoM scan (max id: {data.get('count', 0)}).")

def resolve_coords(x: Optional[float], y: Optional[float], el_id: Optional[int], use_px: bool) -> Tuple[int, int, Optional[Dict[str, Any]]]:
    if el_id is not None:
        el = load_som_element(el_id)
        cx, cy = el["center_px"]
        return int(cx), int(cy), el
    if x is None or y is None:
        cx, cy = get_cursor_pos()
        return cx, cy, None
    if use_px:
        return int(round(x)), int(round(y)), None
    px, py = norm_to_px(x, y)
    return px, py, None

# =============================================================================
# 4. LOW-LEVEL INPUT ENGINE (SendInput)
# =============================================================================

def _send_inputs(inputs: List[INPUT]) -> int:
    if not inputs:
        return 0
    arr = (INPUT * len(inputs))(*inputs)
    return int(user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)))

def _mouse_abs_coords(px: int, py: int) -> Tuple[int, int]:
    m = get_screen_metrics()
    dx = int(round((px - m["vx"]) * 65535.0 / max(1, m["vw"] - 1)))
    dy = int(round((py - m["vy"]) * 65535.0 / max(1, m["vh"] - 1)))
    return max(0, min(65535, dx)), max(0, min(65535, dy))

def move_mouse_px(px: int, py: int, smooth: bool = False, duration: float = 0.14) -> None:
    if smooth and duration > 0:
        sx, sy = get_cursor_pos()
        steps = max(8, int(duration * 120))
        for i in range(1, steps + 1):
            t = i / steps
            # Smoothstep ease-in-out
            e = t * t * (3.0 - 2.0 * t)
            cx = int(round(sx + (px - sx) * e))
            cy = int(round(sy + (py - sy) * e))
            dx, dy = _mouse_abs_coords(cx, cy)
            inp = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(
                dx=dx, dy=dy, mouseData=0,
                dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
                time=0, dwExtraInfo=0
            ))
            _send_inputs([inp])
            time.sleep(duration / steps)
    else:
        dx, dy = _mouse_abs_coords(px, py)
        inp = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(
            dx=dx, dy=dy, mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
            time=0, dwExtraInfo=0
        ))
        _send_inputs([inp])

def mouse_button(button: str, down: bool) -> None:
    b = button.lower()
    flags = {
        ("left", True): MOUSEEVENTF_LEFTDOWN,
        ("left", False): MOUSEEVENTF_LEFTUP,
        ("right", True): MOUSEEVENTF_RIGHTDOWN,
        ("right", False): MOUSEEVENTF_RIGHTUP,
        ("middle", True): MOUSEEVENTF_MIDDLEDOWN,
        ("middle", False): MOUSEEVENTF_MIDDLEUP,
    }
    flag = flags.get((b, down))
    if flag is None:
        raise ValueError(f"Unknown mouse button: {button}")
    inp = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=flag, time=0, dwExtraInfo=0))
    _send_inputs([inp])

def click_px(px: int, py: int, button: str = "left", clicks: int = 1, smooth: bool = False) -> None:
    move_mouse_px(px, py, smooth=smooth)
    time.sleep(0.015)
    for i in range(clicks):
        mouse_button(button, True)
        time.sleep(0.015)
        mouse_button(button, False)
        if i + 1 < clicks:
            time.sleep(0.045)

def drag_px(x1: int, y1: int, x2: int, y2: int, button: str = "left", duration: float = 0.25) -> None:
    move_mouse_px(x1, y1, smooth=False)
    time.sleep(0.03)
    mouse_button(button, True)
    time.sleep(0.04)
    move_mouse_px(x2, y2, smooth=True, duration=max(0.05, duration))
    time.sleep(0.04)
    mouse_button(button, False)

def scroll_wheel(amount: int, px: Optional[int] = None, py: Optional[int] = None, horiz: bool = False) -> None:
    if px is not None and py is not None:
        move_mouse_px(px, py, smooth=False)
        time.sleep(0.015)
    # 120 = 1 notch (WHEEL_DELTA). Pass notches directly (e.g. -5 = scroll down 5 clicks)
    delta = int(amount) * 120
    flag = MOUSEEVENTF_HWHEEL if horiz else MOUSEEVENTF_WHEEL
    # Unsigned 32-bit representation for negative values
    mdata = delta & 0xFFFFFFFF
    inp = INPUT(type=INPUT_MOUSE, mi=MOUSEINPUT(dx=0, dy=0, mouseData=mdata, dwFlags=flag, time=0, dwExtraInfo=0))
    _send_inputs([inp])

def _vk_input(vk: int, keyup: bool = False) -> INPUT:
    scan = user32.MapVirtualKeyW(vk, 0) & 0xFF
    flags = 0
    if vk in EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    if keyup:
        flags |= KEYEVENTF_KEYUP
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0))

def parse_vk(name: str) -> int:
    k = name.strip().lower()
    if k in VK_MAP:
        return VK_MAP[k]
    raise ValueError(f"Unknown key name: '{name}'")

def press_key(name: str, count: int = 1, delay: float = 0.03) -> None:
    vk = parse_vk(name)
    for i in range(max(1, count)):
        _send_inputs([_vk_input(vk, False), _vk_input(vk, True)])
        if i + 1 < count:
            time.sleep(delay)

def press_hotkey(combo: str) -> None:
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ValueError("Empty hotkey")
    vks = [parse_vk(p) for p in parts]
    downs = [_vk_input(vk, False) for vk in vks]
    ups = [_vk_input(vk, True) for vk in reversed(vks)]
    _send_inputs(downs)
    time.sleep(0.025)
    _send_inputs(ups)

def type_unicode(text: str, delay: float = 0.004) -> None:
    """Layout-independent Unicode typing via Win32 KEYEVENTF_UNICODE (works with RU/EN/Symbols/Emoji)."""
    # Normalize CRLF -> LF
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for ch in text:
        if ch == "\n":
            press_key("enter")
        elif ch == "\t":
            press_key("tab")
        else:
            # Encode as UTF-16LE code units so emojis (surrogate pairs) work too!
            raw = ch.encode("utf-16-le")
            units = [int.from_bytes(raw[i:i+2], "little") for i in range(0, len(raw), 2)]
            evts: List[INPUT] = []
            for u in units:
                evts.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0, wScan=u, dwFlags=KEYEVENTF_UNICODE, time=0, dwExtraInfo=0)))
            for u in reversed(units):
                evts.append(INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=0, wScan=u, dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, time=0, dwExtraInfo=0)))
            _send_inputs(evts)
        if delay > 0:
            time.sleep(delay)

# =============================================================================
# 5. WIN32 CLIPBOARD
# =============================================================================

def clipboard_get() -> str:
    for _ in range(5):
        if user32.OpenClipboard(None):
            try:
                h = user32.GetClipboardData(CF_UNICODETEXT)
                if not h:
                    return ""
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr)
                finally:
                    kernel32.GlobalUnlock(h)
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return ""

def clipboard_set(text: str) -> bool:
    data = (text + "\x00").encode("utf-16-le")
    for _ in range(5):
        if user32.OpenClipboard(None):
            try:
                user32.EmptyClipboard()
                h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not h:
                    return False
                ptr = kernel32.GlobalLock(h)
                if not ptr:
                    return False
                try:
                    ctypes.memmove(ptr, data, len(data))
                finally:
                    kernel32.GlobalUnlock(h)
                user32.SetClipboardData(CF_UNICODETEXT, h)
                return True
            finally:
                user32.CloseClipboard()
        time.sleep(0.02)
    return False

def paste_text(text: str, restore: bool = False) -> None:
    prev = clipboard_get() if restore else None
    if not clipboard_set(text):
        raise RuntimeError("Failed to write to Windows clipboard.")
    time.sleep(0.025)
    press_hotkey("ctrl+v")
    if restore and prev is not None:
        time.sleep(0.12)
        clipboard_set(prev)

# =============================================================================
# 6. WINDOW MANAGEMENT
# =============================================================================

def _get_exe_name(pid: int) -> str:
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        sz = wt.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(h)

def get_window_info(hwnd: int) -> Optional[Dict[str, Any]]:
    if not hwnd:
        return None
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(hwnd, buf, length + 1)
    title = buf.value.strip()
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    m = get_screen_metrics()
    nx, ny = px_to_norm(rect.left, rect.top)
    nw = int(round((rect.right - rect.left) * 1000.0 / max(1, m["w"])))
    nh = int(round((rect.bottom - rect.top) * 1000.0 / max(1, m["h"])))
    state = "minimized" if user32.IsIconic(hwnd) else ("maximized" if user32.IsZoomed(hwnd) else "normal")
    return {
        "hwnd": int(hwnd),
        "pid": int(pid.value),
        "exe": _get_exe_name(int(pid.value)),
        "title": title,
        "state": state,
        "active": int(hwnd) == int(user32.GetForegroundWindow() or 0),
        "px": [rect.left, rect.top, rect.right, rect.bottom],
        "norm": [nx, ny, nw, nh],
    }

def list_windows(query: Optional[str] = None) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    q = query.lower().strip() if query else None

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindowTextLengthW(hwnd) <= 0:
            return True
        info = get_window_info(hwnd)
        if not info or not info["title"]:
            return True
        w = info["px"][2] - info["px"][0]
        h = info["px"][3] - info["px"][1]
        if info["state"] != "minimized" and (w < 20 or h < 20):
            return True
        if q:
            hay = f"{info['title']} {info['exe']} {info['hwnd']}".lower()
            if q not in hay:
                return True
        result.append(info)
        return True

    user32.EnumWindows(_cb, 0)
    return result

def find_window(query_or_hwnd: str) -> Dict[str, Any]:
    s = str(query_or_hwnd).strip()
    if s.isdigit():
        info = get_window_info(int(s))
        if info:
            return info
    matches = list_windows(s)
    if not matches:
        raise ValueError(f"No visible window matching '{query_or_hwnd}'")
    # Prefer exact title/exe match or non-minimized
    s_low = s.lower()
    matches.sort(key=lambda w: (
        0 if w["title"].lower() == s_low or w["exe"].lower() == s_low else 1,
        0 if w["state"] != "minimized" else 1,
        len(w["title"]),
    ))
    return matches[0]

def focus_window(query_or_hwnd: str) -> Dict[str, Any]:
    win = find_window(query_or_hwnd)
    hwnd = win["hwnd"]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.05)

    fg = user32.GetForegroundWindow()
    cur_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)

    # Reliably unlock SetForegroundWindow
    if fg_tid and fg_tid != cur_tid:
        user32.AttachThreadInput(cur_tid, fg_tid, True)
    if tgt_tid and tgt_tid != cur_tid:
        user32.AttachThreadInput(cur_tid, tgt_tid, True)
    try:
        # Alt key pulse bypasses Windows foreground lock cleanly
        _send_inputs([_vk_input(0x12, False), _vk_input(0x12, True)])
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if fg_tid and fg_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
        if tgt_tid and tgt_tid != cur_tid:
            user32.AttachThreadInput(cur_tid, tgt_tid, False)

    time.sleep(0.05)
    return get_window_info(hwnd) or win

def window_op(query_or_hwnd: str, op: str, rect_args: Optional[List[float]] = None, use_px: bool = False) -> Dict[str, Any]:
    win = find_window(query_or_hwnd)
    hwnd = win["hwnd"]
    op_l = op.lower()
    if op_l in ("max", "maximize"):
        user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)
    elif op_l in ("min", "minimize"):
        user32.ShowWindow(hwnd, SW_MINIMIZE)
    elif op_l in ("restore", "normal"):
        user32.ShowWindow(hwnd, SW_RESTORE)
    elif op_l == "close":
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    elif op_l == "move":
        if not rect_args or len(rect_args) != 4:
            raise ValueError("winop move requires 4 numbers: x y w h")
        x, y, w, h = rect_args
        if not use_px:
            m = get_screen_metrics()
            x = int(round(x * m["w"] / 1000.0))
            y = int(round(y * m["h"] / 1000.0))
            w = int(round(w * m["w"] / 1000.0))
            h = int(round(h * m["h"] / 1000.0))
        else:
            x, y, w, h = int(x), int(y), int(w), int(h)
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.MoveWindow(hwnd, x, y, w, h, True)
    else:
        raise ValueError(f"Unknown window op: {op}")
    time.sleep(0.05)
    return get_window_info(hwnd) or win

# =============================================================================
# 7. SCREEN CAPTURE + VISUAL OVERLAYS (GRID, CURSOR, CROP, SOM)
# =============================================================================

def _bitblt_capture(bbox: Optional[Tuple[int, int, int, int]] = None, hwnd: Optional[int] = None):
    from PIL import Image
    m = get_screen_metrics()
    if hwnd:
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        left, top, right, bottom = r.left, r.top, r.right, r.bottom
    elif bbox:
        left, top, right, bottom = bbox
    else:
        left, top, right, bottom = 0, 0, m["w"], m["h"]

    w = max(1, right - left)
    h = max(1, bottom - top)

    hdc_src = user32.GetDC(hwnd if hwnd else 0)
    if not hdc_src:
        raise RuntimeError("Win32 GetDC failed (desktop may be locked or non-interactive session).")
    hdc_mem = gdi32.CreateCompatibleDC(hdc_src)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_src, w, h)
    old = gdi32.SelectObject(hdc_mem, hbmp)
    try:
        ok = False
        if hwnd:
            ok = bool(user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT))
        if not ok:
            ok = bool(gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_src, 0 if hwnd else left, 0 if hwnd else top, SRCCOPY | CAPTUREBLT))
        if not ok:
            raise RuntimeError("Screen capture failed (desktop may be locked, UAC active, or running in a non-interactive/sandboxed desktop).")

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB

        buf = ctypes.create_string_buffer(w * h * 4)
        lines = gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
        if lines == 0:
            raise RuntimeError("GetDIBits returned 0 scanlines.")
        return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).copy()
    finally:
        gdi32.SelectObject(hdc_mem, old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(hwnd if hwnd else 0, hdc_src)

def capture_screen(bbox: Optional[Tuple[int, int, int, int]] = None, all_screens: bool = False, hwnd: Optional[int] = None):
    from PIL import ImageGrab
    if hwnd:
        return _bitblt_capture(hwnd=hwnd)
    try:
        return ImageGrab.grab(bbox=bbox, include_layered_windows=True, all_screens=all_screens)
    except Exception as e1:
        try:
            return _bitblt_capture(bbox=bbox)
        except Exception as e2:
            raise RuntimeError(
                f"Screen capture failed (ImageGrab: {e1}; BitBlt: {e2}). "
                "Make sure Windows desktop is unlocked and command is running in an interactive desktop session."
            )

def _load_font(size: int):
    from PIL import ImageFont
    for candidate in (
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\consola.ttf",
    ):
        if os.path.exists(candidate):
            try:
                return ImageFont.truetype(candidate, size)
            except Exception:
                pass
    return ImageFont.load_default()

def draw_grid_overlay(img, origin_norm: Tuple[float, float] = (0.0, 0.0), span_norm: Tuple[float, float] = (1000.0, 1000.0), draw_cursor: bool = True):
    from PIL import Image, ImageDraw
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = base.size
    ox, oy = origin_norm
    sw, sh = span_norm
    font = _load_font(max(11, int(min(w, h) * 0.016)))

    # Choose crisp step based on zoom level
    step = 100 if max(sw, sh) >= 400 else (50 if max(sw, sh) >= 180 else 20)
    minor = step // 2

    # Vertical lines
    start_x = int(math.ceil(ox / minor) * minor)
    for gx in range(start_x, int(ox + sw) + 1, minor):
        if gx <= ox or gx >= ox + sw:
            continue
        ix = int(round((gx - ox) / sw * w))
        is_major = (gx % step == 0)
        color = (0, 220, 255, 115) if is_major else (0, 220, 255, 45)
        draw.line([(ix, 0), (ix, h)], fill=color, width=1)
        if is_major:
            lbl = str(gx)
            tb = draw.textbbox((0, 0), lbl, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            # Top & bottom badges
            for by in (2, h - th - 6):
                draw.rectangle([ix - tw // 2 - 3, by, ix + tw // 2 + 3, by + th + 3], fill=(0, 0, 0, 185))
                draw.text((ix - tw // 2, by + 1), lbl, fill=(0, 255, 255, 255), font=font)

    # Horizontal lines
    start_y = int(math.ceil(oy / minor) * minor)
    for gy in range(start_y, int(oy + sh) + 1, minor):
        if gy <= oy or gy >= oy + sh:
            continue
        iy = int(round((gy - oy) / sh * h))
        is_major = (gy % step == 0)
        color = (0, 220, 255, 115) if is_major else (0, 220, 255, 45)
        draw.line([(0, iy), (w, iy)], fill=color, width=1)
        if is_major:
            lbl = str(gy)
            tb = draw.textbbox((0, 0), lbl, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            for bx in (2, w - tw - 8):
                draw.rectangle([bx, iy - th // 2 - 2, bx + tw + 6, iy + th // 2 + 2], fill=(0, 0, 0, 185))
                draw.text((bx + 3, iy - th // 2 - 1), lbl, fill=(0, 255, 255, 255), font=font)

    if draw_cursor:
        cx_px, cy_px = get_cursor_pos()
        cnx, cny = px_to_norm(cx_px, cy_px)
        if ox <= cnx <= ox + sw and oy <= cny <= oy + sh:
            ix = int(round((cnx - ox) / sw * w))
            iy = int(round((cny - oy) / sh * h))
            r = max(10, int(min(w, h) * 0.015))
            draw.ellipse([ix - r, iy - r, ix + r, iy + r], outline=(255, 40, 40, 240), width=2)
            draw.line([(ix - r - 6, iy), (ix + r + 6, iy)], fill=(255, 40, 40, 240), width=2)
            draw.line([(ix, iy - r - 6), (ix, iy + r + 6)], fill=(255, 40, 40, 240), width=2)

    return Image.alpha_composite(base, overlay).convert("RGB")

def save_image(img, out_path: Path, max_width: int = 1280, quality: int = 85) -> Tuple[int, int]:
    from PIL import Image
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if max_width and max_width > 0 and img.width > max_width:
        ratio = max_width / float(img.width)
        new_h = max(1, int(round(img.height * ratio)))
        img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)
    ext = out_path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        img.convert("RGB").save(out_path, format="JPEG", quality=quality, optimize=True)
    else:
        img.save(out_path, format="PNG", optimize=True)
    return img.width, img.height

def do_shot(
    out: Optional[str] = None,
    grid: bool = False,
    cursor: bool = False,
    window: bool = False,
    hwnd: Optional[int] = None,
    crop: Optional[List[float]] = None,
    use_px: bool = False,
    all_screens: bool = False,
    width: int = 1280,
    quality: int = 85,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    m = get_screen_metrics()
    out_path = Path(out) if out else DEFAULT_SHOT_PATH

    origin_norm = (0.0, 0.0)
    span_norm = (1000.0, 1000.0)
    bbox = None
    target_hwnd = hwnd

    if window and not target_hwnd:
        target_hwnd = int(user32.GetForegroundWindow() or 0)
        if not target_hwnd:
            raise RuntimeError("No active foreground window to capture.")

    if crop:
        x, y, cw, ch = crop
        if use_px:
            left, top = int(x), int(y)
            right, bottom = int(x + cw), int(y + ch)
            origin_norm = (left * 1000.0 / m["w"], top * 1000.0 / m["h"])
            span_norm = (cw * 1000.0 / m["w"], ch * 1000.0 / m["h"])
        else:
            left = int(round(x * m["w"] / 1000.0))
            top = int(round(y * m["h"] / 1000.0))
            right = int(round((x + cw) * m["w"] / 1000.0))
            bottom = int(round((y + ch) * m["h"] / 1000.0))
            origin_norm = (float(x), float(y))
            span_norm = (float(cw), float(ch))
        bbox = (max(0, left), max(0, top), min(m["w"], right), min(m["h"], bottom))

    img = capture_screen(bbox=bbox, all_screens=all_screens, hwnd=target_hwnd)

    if target_hwnd:
        r = wt.RECT()
        user32.GetWindowRect(target_hwnd, ctypes.byref(r))
        origin_norm = (r.left * 1000.0 / m["w"], r.top * 1000.0 / m["h"])
        span_norm = ((r.right - r.left) * 1000.0 / m["w"], (r.bottom - r.top) * 1000.0 / m["h"])

    # Resize before drawing grid so text labels stay ultra-crisp!
    from PIL import Image
    if width and width > 0 and img.width > width:
        ratio = width / float(img.width)
        img = img.resize((width, max(1, int(round(img.height * ratio)))), Image.Resampling.LANCZOS)

    if grid or cursor:
        if grid:
            img = draw_grid_overlay(img, origin_norm=origin_norm, span_norm=span_norm, draw_cursor=True)
        else:
            from PIL import ImageDraw
            base = img.convert("RGBA")
            ov = Image.new("RGBA", base.size, (0, 0, 0, 0))
            dr = ImageDraw.Draw(ov)
            cpx, cpy = get_cursor_pos()
            cnx, cny = px_to_norm(cpx, cpy)
            ox, oy = origin_norm
            sw, sh = span_norm
            if ox <= cnx <= ox + sw and oy <= cny <= oy + sh:
                ix = int(round((cnx - ox) / sw * base.width))
                iy = int(round((cny - oy) / sh * base.height))
                dr.ellipse([ix - 12, iy - 12, ix + 12, iy + 12], outline=(255, 40, 40, 240), width=2)
                dr.line([(ix - 18, iy), (ix + 18, iy)], fill=(255, 40, 40, 240), width=2)
                dr.line([(ix, iy - 18), (ix, iy + 18)], fill=(255, 40, 40, 240), width=2)
            img = Image.alpha_composite(base, ov).convert("RGB")

    iw, ih = save_image(img, out_path, max_width=0, quality=quality)
    cpx, cpy = get_cursor_pos()
    cnx, cny = px_to_norm(cpx, cpy)
    fg = get_window_info(int(user32.GetForegroundWindow() or 0))
    return {
        "ok": True,
        "path": str(out_path),
        "screen": [m["w"], m["h"]],
        "image_size": [iw, ih],
        "cursor_norm": [cnx, cny],
        "cursor_px": [cpx, cpy],
        "active_window": {"title": fg["title"], "exe": fg["exe"], "hwnd": fg["hwnd"]} if fg else None,
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
    }

# =============================================================================
# 8. SET-OF-MARK (UIA SCANNER + ANNOTATED SCREENSHOT)
# =============================================================================

def ensure_uia_exe() -> Path:
    if UIA_EXE_PATH.exists():
        return UIA_EXE_PATH
    if not UIA_CS_PATH.exists():
        raise RuntimeError(f"Missing {UIA_CS_PATH}")
    fw = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319")
    csc = fw / "csc.exe"
    if not csc.exists():
        raise RuntimeError("Built-in .NET compiler (csc.exe) not found.")
    cmd = [
        str(csc), "/nologo", "/optimize+", "/target:exe",
        f"/out:{UIA_EXE_PATH}",
        f"/r:{fw / 'WPF' / 'UIAutomationClient.dll'}",
        f"/r:{fw / 'WPF' / 'UIAutomationTypes.dll'}",
        f"/r:{fw / 'WPF' / 'WindowsBase.dll'}",
        str(UIA_CS_PATH),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to compile uia_scan.cs: {res.stdout} {res.stderr}")
    return UIA_EXE_PATH

ROLE_COLORS = {
    "Button": (0, 120, 255),
    "SplitButton": (0, 120, 255),
    "Edit": (0, 185, 90),
    "Document": (0, 185, 90),
    "ComboBox": (0, 185, 90),
    "TabItem": (235, 130, 0),
    "MenuItem": (235, 130, 0),
    "Hyperlink": (175, 60, 235),
    "CheckBox": (0, 180, 190),
    "RadioButton": (0, 180, 190),
    "ListItem": (220, 60, 110),
    "TreeItem": (220, 60, 110),
}

def do_som(
    hwnd: Optional[int] = None,
    desktop: bool = False,
    max_el: int = 80,
    depth: int = 10,
    include_text: bool = False,
    width: int = 1280,
    out: Optional[str] = None,
    no_image: bool = False,
) -> Dict[str, Any]:
    t0 = time.perf_counter()
    exe = ensure_uia_exe()
    cmd = [str(exe), "--max", str(max_el), "--depth", str(depth)]
    if desktop:
        cmd.append("--desktop")
    elif hwnd:
        cmd.extend(["--hwnd", str(hwnd)])
    if include_text:
        cmd.append("--text")

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8)
    if proc.returncode != 0:
        raise RuntimeError(f"uia_scan failed: {proc.stderr or proc.stdout}")
    data = json.loads(proc.stdout.strip() or "{}")
    if "error" in data:
        raise RuntimeError(f"UIA error: {data['error']}")

    SOM_JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    out_path = Path(out) if out else DEFAULT_SOM_PATH
    img_info = None
    if not no_image:
        from PIL import Image, ImageDraw
        img = capture_screen()
        sw, sh = img.width, img.height
        if width and width > 0 and sw > width:
            scale = width / float(sw)
            img = img.resize((width, max(1, int(round(sh * scale)))), Image.Resampling.LANCZOS)
        else:
            scale = 1.0

        base = img.convert("RGBA")
        ov = Image.new("RGBA", base.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(ov)
        font = _load_font(12)
        occupied: List[Tuple[int, int, int, int]] = []

        for el in data.get("elements", []):
            l, t, r, b = el["px"]
            x1, y1, x2, y2 = int(l * scale), int(t * scale), int(r * scale), int(b * scale)
            col = ROLE_COLORS.get(el["role"], (230, 50, 50))
            dr.rectangle([x1, y1, x2, y2], outline=col + (225,), width=2)

            lbl = str(el["id"])
            tb = dr.textbbox((0, 0), lbl, font=font)
            tw, th = tb[2] - tb[0] + 6, tb[3] - tb[1] + 2

            # Try placing badge at top-left, then top-right, then inside top-left
            candidates = [
                (x1, max(0, y1 - th)),
                (max(0, x2 - tw), max(0, y1 - th)),
                (x1 + 1, y1 + 1),
                (max(0, x2 - tw - 1), y1 + 1),
            ]
            bx, by = candidates[0]
            for cx, cy in candidates:
                cx = max(0, min(base.width - tw, cx))
                cy = max(0, min(base.height - th, cy))
                box = (cx, cy, cx + tw, cy + th)
                if not any(not (box[2] <= o[0] or box[0] >= o[2] or box[3] <= o[1] or box[1] >= o[3]) for o in occupied):
                    bx, by = cx, cy
                    break
            occupied.append((bx, by, bx + tw, by + th))
            dr.rectangle([bx, by, bx + tw, by + th], fill=col + (240,), outline=(255, 255, 255, 230), width=1)
            dr.text((bx + 3, by), lbl, fill=(255, 255, 255, 255), font=font)

        annotated = Image.alpha_composite(base, ov).convert("RGB")
        iw, ih = save_image(annotated, out_path, max_width=0, quality=85)
        img_info = {"path": str(out_path), "image_size": [iw, ih]}

    compact_list = []
    for el in data.get("elements", []):
        name = el.get("name") or el.get("aid") or ""
        val = f" val='{el['value']}'" if el.get("value") else ""
        compact_list.append(f"[{el['id']}] {el['role']} \"{name}\"{val} @({el['center'][0]},{el['center'][1]})")

    return {
        "ok": True,
        "window": data.get("window", ""),
        "hwnd": data.get("hwnd", 0),
        "count": data.get("count", 0),
        "uia_ms": data.get("elapsed_ms", 0),
        "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        "image": img_info,
        "elements": compact_list,
    }

# =============================================================================
# 9. OPENCV TEMPLATE MATCHING & VISUAL WAIT
# =============================================================================

def do_find_img(template_path: str, threshold: float = 0.85, click_mode: Optional[str] = None) -> Dict[str, Any]:
    import cv2
    import numpy as np

    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template image not found: {template_path}")
    screen_pil = capture_screen()
    screen_bgr = cv2.cvtColor(np.array(screen_pil), cv2.COLOR_RGB2BGR)
    tpl = cv2.imread(template_path, cv2.IMREAD_COLOR)
    if tpl is None:
        raise RuntimeError(f"Failed to read template image: {template_path}")

    res = cv2.matchTemplate(screen_bgr, tpl, cv2.TM_CCOEFF_NORMED)
    _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(res)
    th, tw = tpl.shape[:2]
    cx_px = max_loc[0] + tw // 2
    cy_px = max_loc[1] + th // 2
    cnx, cny = px_to_norm(cx_px, cy_px)
    found = float(max_val) >= threshold

    if found and click_mode:
        if click_mode == "click":
            click_px(cx_px, cy_px, button="left", clicks=1)
        elif click_mode == "dclick":
            click_px(cx_px, cy_px, button="left", clicks=2)
        elif click_mode == "rclick":
            click_px(cx_px, cy_px, button="right", clicks=1)

    return {
        "ok": True,
        "found": found,
        "confidence": round(float(max_val), 4),
        "center_norm": [cnx, cny],
        "center_px": [cx_px, cy_px],
        "clicked": bool(found and click_mode),
    }

def do_wait_change(timeout: float = 10.0, interval: float = 0.15, threshold: float = 2.5, region: Optional[List[float]] = None, use_px: bool = False) -> Dict[str, Any]:
    import numpy as np
    m = get_screen_metrics()
    bbox = None
    if region:
        x, y, cw, ch = region
        if use_px:
            bbox = (int(x), int(y), int(x + cw), int(y + ch))
        else:
            bbox = (
                int(round(x * m["w"] / 1000.0)),
                int(round(y * m["h"] / 1000.0)),
                int(round((x + cw) * m["w"] / 1000.0)),
                int(round((y + ch) * m["h"] / 1000.0)),
            )

    def _grab_small():
        im = capture_screen(bbox=bbox)
        im = im.resize((min(320, im.width), min(180, im.height)))
        return np.asarray(im, dtype=np.float32)

    base = _grab_small()
    t0 = time.perf_counter()
    diff = 0.0
    changed = False
    while (time.perf_counter() - t0) < timeout:
        time.sleep(interval)
        cur = _grab_small()
        diff = float(np.mean(np.abs(cur - base)))
        if diff >= threshold:
            changed = True
            break
    return {
        "ok": True,
        "changed": changed,
        "diff": round(diff, 3),
        "elapsed_s": round(time.perf_counter() - t0, 3),
    }

# =============================================================================
# 10. BATCH PIPELINE ENGINE
# =============================================================================

def _parse_xy_or_id(arg: str, use_px: bool = False) -> Tuple[int, int]:
    arg = arg.strip()
    if arg.startswith("#") or arg.lower().startswith("id="):
        el_id = int(arg.lstrip("#").replace("id=", "", 1))
        px, py, _ = resolve_coords(None, None, el_id, use_px)
        return px, py
    parts = [p.strip() for p in arg.split(",")]
    px, py, _ = resolve_coords(float(parts[0]), float(parts[1]), None, use_px)
    return px, py

def run_batch_step(step: Any, use_px: bool = False) -> Dict[str, Any]:
    if isinstance(step, str):
        if ":" in step:
            op, val = step.split(":", 1)
        else:
            op, val = step, ""
        op = op.strip().lower()
        val = val.strip()

        if op in ("click", "rclick", "dclick"):
            px, py = _parse_xy_or_id(val, use_px)
            btn = "right" if op == "rclick" else "left"
            cnt = 2 if op == "dclick" else 1
            click_px(px, py, button=btn, clicks=cnt)
            return {"op": op, "px": [px, py]}
        elif op == "move":
            px, py = _parse_xy_or_id(val, use_px)
            move_mouse_px(px, py, smooth=True)
            return {"op": op, "px": [px, py]}
        elif op == "drag":
            pts = [float(p.strip()) for p in val.split(",")]
            x1, y1, _ = resolve_coords(pts[0], pts[1], None, use_px)
            x2, y2, _ = resolve_coords(pts[2], pts[3], None, use_px)
            drag_px(x1, y1, x2, y2)
            return {"op": op, "from": [x1, y1], "to": [x2, y2]}
        elif op in ("scroll", "hscroll"):
            parts = [p.strip() for p in val.split(",")]
            amt = int(parts[0])
            px = py = None
            if len(parts) >= 3:
                px, py, _ = resolve_coords(float(parts[1]), float(parts[2]), None, use_px)
            scroll_wheel(amt, px, py, horiz=(op == "hscroll"))
            return {"op": op, "amount": amt}
        elif op == "type":
            type_unicode(val)
            return {"op": op, "len": len(val)}
        elif op == "paste":
            paste_text(val)
            return {"op": op, "len": len(val)}
        elif op == "key":
            press_key(val)
            return {"op": op, "key": val}
        elif op == "hotkey":
            press_hotkey(val)
            return {"op": op, "combo": val}
        elif op in ("wait", "sleep"):
            sec = float(val or "0.3")
            time.sleep(sec)
            return {"op": "wait", "sec": sec}
        elif op == "wait-change":
            sec = float(val or "8.0")
            return do_wait_change(timeout=sec)
        elif op == "focus":
            w = focus_window(val)
            return {"op": "focus", "title": w["title"], "hwnd": w["hwnd"]}
        elif op == "launch":
            os.startfile(val)
            return {"op": "launch", "target": val}
        elif op == "shot":
            return do_shot(grid=("grid" in val.lower()), window=("window" in val.lower()))
        elif op == "som":
            return do_som(desktop=("desktop" in val.lower()))
        else:
            raise ValueError(f"Unknown batch step op: {op}")

    elif isinstance(step, dict):
        op = str(step.get("op", "")).lower()
        px_flag = bool(step.get("px", use_px))
        if op in ("click", "rclick", "dclick"):
            px, py, _ = resolve_coords(step.get("x"), step.get("y"), step.get("id"), px_flag)
            btn = step.get("button", "right" if op == "rclick" else "left")
            cnt = int(step.get("clicks", 2 if op == "dclick" else 1))
            click_px(px, py, button=btn, clicks=cnt, smooth=bool(step.get("smooth", False)))
            return {"op": op, "px": [px, py]}
        elif op == "move":
            px, py, _ = resolve_coords(step.get("x"), step.get("y"), step.get("id"), px_flag)
            move_mouse_px(px, py, smooth=bool(step.get("smooth", True)))
            return {"op": op, "px": [px, py]}
        elif op == "drag":
            x1, y1, _ = resolve_coords(step["x1"], step["y1"], None, px_flag)
            x2, y2, _ = resolve_coords(step["x2"], step["y2"], None, px_flag)
            drag_px(x1, y1, x2, y2, button=step.get("button", "left"), duration=float(step.get("duration", 0.25)))
            return {"op": op}
        elif op in ("scroll", "hscroll"):
            px = py = None
            if "x" in step and "y" in step or "id" in step:
                px, py, _ = resolve_coords(step.get("x"), step.get("y"), step.get("id"), px_flag)
            scroll_wheel(int(step.get("amount", -5)), px, py, horiz=(op == "hscroll" or bool(step.get("horiz"))))
            return {"op": op, "amount": int(step.get("amount", -5))}
        elif op in ("type", "paste"):
            if "id" in step or ("x" in step and "y" in step):
                px, py, _ = resolve_coords(step.get("x"), step.get("y"), step.get("id"), px_flag)
                click_px(px, py)
                time.sleep(0.05)
            if step.get("clear"):
                press_hotkey("ctrl+a")
                time.sleep(0.03)
                press_key("backspace")
                time.sleep(0.03)
            txt = str(step.get("text", ""))
            if op == "paste":
                paste_text(txt, restore=bool(step.get("restore", False)))
            else:
                type_unicode(txt, delay=float(step.get("delay", 0.004)))
            if step.get("enter"):
                time.sleep(0.03)
                press_key("enter")
            return {"op": op, "len": len(txt)}
        elif op == "key":
            press_key(str(step["key"]), count=int(step.get("count", 1)))
            return {"op": op, "key": step["key"]}
        elif op == "hotkey":
            press_hotkey(str(step["combo"]))
            return {"op": op, "combo": step["combo"]}
        elif op in ("wait", "sleep"):
            sec = float(step.get("sec", 0.3))
            time.sleep(sec)
            return {"op": "wait", "sec": sec}
        elif op == "wait-change":
            return do_wait_change(timeout=float(step.get("timeout", 8.0)), region=step.get("region"))
        elif op == "focus":
            w = focus_window(str(step["target"]))
            return {"op": "focus", "title": w["title"], "hwnd": w["hwnd"]}
        elif op == "winop":
            w = window_op(str(step["target"]), str(step["action"]), step.get("rect"), use_px=px_flag)
            return {"op": "winop", "title": w["title"]}
        elif op == "launch":
            os.startfile(str(step["target"]))
            return {"op": "launch", "target": step["target"]}
        elif op == "shot":
            return do_shot(grid=bool(step.get("grid", False)), window=bool(step.get("window", False)), crop=step.get("crop"))
        elif op == "som":
            return do_som(desktop=bool(step.get("desktop", False)), max_el=int(step.get("max", 80)))
        else:
            raise ValueError(f"Unknown JSON batch op: {op}")
    raise ValueError(f"Invalid batch step: {step}")

# =============================================================================
# 11. CLI PARSER
# =============================================================================

def _maybe_post_capture(args: argparse.Namespace, res: Dict[str, Any]) -> Dict[str, Any]:
    if getattr(args, "shot", False):
        time.sleep(getattr(args, "after_wait", 0.15))
        res["shot"] = do_shot(grid=getattr(args, "grid", False))
    elif getattr(args, "som", False):
        time.sleep(getattr(args, "after_wait", 0.2))
        res["som"] = do_som()
    return res

def add_post_capture_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--shot", action="store_true", help="Take screenshot after action")
    p.add_argument("--grid", action="store_true", help="Include grid when --shot is set")
    p.add_argument("--som", action="store_true", help="Run Set-of-Mark scan after action")
    p.add_argument("--after-wait", type=float, default=0.15, help="Seconds to wait before post-action capture")

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cu.py", description="Native Windows Computer Use Engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    # state
    sub.add_parser("state", help="Fast status: screen, cursor, active window, clipboard")

    # shot
    sp = sub.add_parser("shot", help="Capture screenshot (optionally with 0..1000 grid, cursor, crop, or window)")
    sp.add_argument("-o", "--out", default=None, help="Output file path")
    sp.add_argument("--grid", action="store_true", help="Draw 0..1000 precision coordinate grid + cursor")
    sp.add_argument("--cursor", action="store_true", help="Draw live mouse cursor crosshair")
    sp.add_argument("--window", action="store_true", help="Capture active foreground window only")
    sp.add_argument("--hwnd", type=int, default=None, help="Capture specific window HWND")
    sp.add_argument("--crop", type=float, nargs=4, metavar=("X", "Y", "W", "H"), help="Zoom crop region")
    sp.add_argument("--px", action="store_true", help="Interpret --crop in physical pixels")
    sp.add_argument("--all-screens", action="store_true", help="Capture all monitors")
    sp.add_argument("--width", type=int, default=1280, help="Resize max width (0 = native)")
    sp.add_argument("--quality", type=int, default=85, help="JPEG quality")

    # som
    sp = sub.add_parser("som", help="Set-of-Mark: scan Windows UIA controls + draw numbered badges")
    sp.add_argument("--hwnd", type=int, default=None, help="Target specific window HWND")
    sp.add_argument("--desktop", action="store_true", help="Scan full desktop / taskbar")
    sp.add_argument("--max", type=int, default=80, help="Max elements")
    sp.add_argument("--depth", type=int, default=10, help="Max UIA tree depth")
    sp.add_argument("--text", action="store_true", help="Include static Text elements")
    sp.add_argument("--width", type=int, default=1280, help="Annotated image max width")
    sp.add_argument("-o", "--out", default=None, help="Output annotated image path")
    sp.add_argument("--no-image", action="store_true", help="Return JSON elements only (ultra-fast)")

    # click / rclick / dclick
    for name in ("click", "rclick", "dclick"):
        sp = sub.add_parser(name, help=f"Mouse {name} at (x, y) or --id N")
        sp.add_argument("coords", type=float, nargs="*", help="x y coordinates (0..1000 unless --px)")
        sp.add_argument("--id", type=int, default=None, help="Click Set-of-Mark element ID")
        sp.add_argument("--px", action="store_true", help="Coordinates are raw physical pixels")
        sp.add_argument("--button", default="right" if name == "rclick" else "left", choices=["left", "right", "middle"])
        sp.add_argument("--clicks", type=int, default=2 if name == "dclick" else 1)
        sp.add_argument("--smooth", action="store_true", help="Smooth cursor movement")
        add_post_capture_flags(sp)

    # move
    sp = sub.add_parser("move", help="Move mouse cursor")
    sp.add_argument("coords", type=float, nargs="*", help="x y coordinates")
    sp.add_argument("--id", type=int, default=None, help="Move to SoM element ID")
    sp.add_argument("--px", action="store_true")
    sp.add_argument("--smooth", action="store_true")
    sp.add_argument("--duration", type=float, default=0.14)
    add_post_capture_flags(sp)

    # down / up
    for name in ("down", "up"):
        sp = sub.add_parser(name, help=f"Mouse button {name}")
        sp.add_argument("button", nargs="?", default="left", choices=["left", "right", "middle"])

    # drag
    sp = sub.add_parser("drag", help="Smooth drag from (x1, y1) to (x2, y2)")
    sp.add_argument("x1", type=float)
    sp.add_argument("y1", type=float)
    sp.add_argument("x2", type=float)
    sp.add_argument("y2", type=float)
    sp.add_argument("--px", action="store_true")
    sp.add_argument("--button", default="left", choices=["left", "right", "middle"])
    sp.add_argument("--duration", type=float, default=0.25)
    add_post_capture_flags(sp)

    # scroll
    sp = sub.add_parser("scroll", help="Scroll mouse wheel (+up/-down, or +right/-left with --horiz)")
    sp.add_argument("amount", type=int, help="Wheel notches (e.g., -5 down, +5 up)")
    sp.add_argument("coords", type=float, nargs="*", help="Optional x y target")
    sp.add_argument("--id", type=int, default=None, help="Optional SoM element target")
    sp.add_argument("--px", action="store_true")
    sp.add_argument("--horiz", action="store_true", help="Horizontal scroll")
    add_post_capture_flags(sp)

    # type / paste
    for name in ("type", "paste"):
        sp = sub.add_parser(name, help="Type Unicode text (layout-independent)" if name == "type" else "Instant clipboard paste")
        sp.add_argument("text", help="Text to input (supports Russian/Unicode)")
        sp.add_argument("--id", type=int, default=None, help="Click SoM element first")
        sp.add_argument("--x", type=float, default=None)
        sp.add_argument("--y", type=float, default=None)
        sp.add_argument("--px", action="store_true")
        sp.add_argument("--clear", action="store_true", help="Ctrl+A + Backspace before input")
        sp.add_argument("--enter", action="store_true", help="Press Enter after input")
        if name == "type":
            sp.add_argument("--delay", type=float, default=0.004)
        else:
            sp.add_argument("--restore", action="store_true", help="Restore previous clipboard")
        add_post_capture_flags(sp)

    # key
    sp = sub.add_parser("key", help="Press a single key (enter, esc, tab, f5, win, etc.)")
    sp.add_argument("name", help="Key name")
    sp.add_argument("--count", type=int, default=1)
    sp.add_argument("--delay", type=float, default=0.03)
    add_post_capture_flags(sp)

    # hotkey
    sp = sub.add_parser("hotkey", help="Press key combination (e.g., ctrl+c, ctrl+shift+t, alt+tab, win+r)")
    sp.add_argument("combo", help="Plus-separated keys")
    add_post_capture_flags(sp)

    # clipboard
    sp = sub.add_parser("clipboard", help="Read or write Windows clipboard")
    sp.add_argument("action", choices=["get", "set"])
    sp.add_argument("text", nargs="?", default="")

    # windows
    sp = sub.add_parser("windows", help="List visible top-level windows")
    sp.add_argument("--filter", default=None, help="Substring filter on title/exe")

    # focus
    sp = sub.add_parser("focus", help="Focus and restore window by title/exe substring or HWND")
    sp.add_argument("target", help="Title/exe substring or HWND")
    add_post_capture_flags(sp)

    # winop
    sp = sub.add_parser("winop", help="Window operation: max, min, restore, close, move")
    sp.add_argument("target", help="Title/exe substring or HWND")
    sp.add_argument("action", choices=["max", "maximize", "min", "minimize", "restore", "normal", "close", "move"])
    sp.add_argument("rect", type=float, nargs="*", help="For move: x y w h")
    sp.add_argument("--px", action="store_true")

    # launch
    sp = sub.add_parser("launch", help="Launch app, file, or URL detached")
    sp.add_argument("target", help="Executable, file path, or URL")
    sp.add_argument("--args", default=None, help="Optional CLI arguments")

    # find-img
    sp = sub.add_parser("find-img", help="Find visual icon/template on screen via OpenCV")
    sp.add_argument("template", help="Path to template PNG/JPG")
    sp.add_argument("--threshold", type=float, default=0.85)
    sp.add_argument("--click", action="store_true")
    sp.add_argument("--dclick", action="store_true")
    sp.add_argument("--rclick", action="store_true")

    # wait-change
    sp = sub.add_parser("wait-change", help="Wait until screen (or region) visually changes")
    sp.add_argument("--timeout", type=float, default=10.0)
    sp.add_argument("--interval", type=float, default=0.15)
    sp.add_argument("--threshold", type=float, default=2.5)
    sp.add_argument("--region", type=float, nargs=4, metavar=("X", "Y", "W", "H"))
    sp.add_argument("--px", action="store_true")
    add_post_capture_flags(sp)

    # batch
    sp = sub.add_parser("batch", help="Execute multiple actions in one fast invocation")
    sp.add_argument("steps", nargs="*", help='Step strings like "focus:Chrome" "click:500,300" "paste:hello" "key:enter" "shot:grid"')
    sp.add_argument("--json", dest="json_steps", default=None, help="JSON array of step objects")
    sp.add_argument("--px", action="store_true")

    return p

def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    t0 = time.perf_counter()

    try:
        cmd = args.cmd
        if cmd == "state":
            m = get_screen_metrics()
            cpx, cpy = get_cursor_pos()
            cnx, cny = px_to_norm(cpx, cpy)
            fg = get_window_info(int(user32.GetForegroundWindow() or 0))
            cb = clipboard_get()
            res = {
                "ok": True,
                "screen": [m["w"], m["h"]],
                "virtual_screen": [m["vx"], m["vy"], m["vw"], m["vh"]],
                "cursor_norm": [cnx, cny],
                "cursor_px": [cpx, cpy],
                "active_window": fg,
                "clipboard_preview": cb[:200] + ("..." if len(cb) > 200 else ""),
                "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            }
        elif cmd == "shot":
            res = do_shot(
                out=args.out, grid=args.grid, cursor=args.cursor, window=args.window,
                hwnd=args.hwnd, crop=args.crop, use_px=args.px,
                all_screens=args.all_screens, width=args.width, quality=args.quality,
            )
        elif cmd == "som":
            res = do_som(
                hwnd=args.hwnd, desktop=args.desktop, max_el=args.max, depth=args.depth,
                include_text=args.text, width=args.width, out=args.out, no_image=args.no_image,
            )
        elif cmd in ("click", "rclick", "dclick"):
            x = args.coords[0] if len(args.coords) >= 2 else None
            y = args.coords[1] if len(args.coords) >= 2 else None
            px, py, el = resolve_coords(x, y, args.id, args.px)
            click_px(px, py, button=args.button, clicks=args.clicks, smooth=args.smooth)
            cnx, cny = px_to_norm(px, py)
            res = {"ok": True, "action": cmd, "px": [px, py], "norm": [cnx, cny], "element": el["name"] if el else None}
            res = _maybe_post_capture(args, res)
        elif cmd == "move":
            x = args.coords[0] if len(args.coords) >= 2 else None
            y = args.coords[1] if len(args.coords) >= 2 else None
            px, py, _ = resolve_coords(x, y, args.id, args.px)
            move_mouse_px(px, py, smooth=args.smooth, duration=args.duration)
            res = {"ok": True, "action": "move", "px": [px, py], "norm": list(px_to_norm(px, py))}
            res = _maybe_post_capture(args, res)
        elif cmd in ("down", "up"):
            mouse_button(args.button, down=(cmd == "down"))
            res = {"ok": True, "action": cmd, "button": args.button}
        elif cmd == "drag":
            x1, y1, _ = resolve_coords(args.x1, args.y1, None, args.px)
            x2, y2, _ = resolve_coords(args.x2, args.y2, None, args.px)
            drag_px(x1, y1, x2, y2, button=args.button, duration=args.duration)
            res = {"ok": True, "action": "drag", "from_px": [x1, y1], "to_px": [x2, y2]}
            res = _maybe_post_capture(args, res)
        elif cmd == "scroll":
            px = py = None
            if args.id is not None or len(args.coords) >= 2:
                x = args.coords[0] if len(args.coords) >= 2 else None
                y = args.coords[1] if len(args.coords) >= 2 else None
                px, py, _ = resolve_coords(x, y, args.id, args.px)
            scroll_wheel(args.amount, px, py, horiz=args.horiz)
            res = {"ok": True, "action": "scroll", "amount": args.amount, "horiz": args.horiz}
            res = _maybe_post_capture(args, res)
        elif cmd in ("type", "paste"):
            if args.id is not None or (args.x is not None and args.y is not None):
                px, py, _ = resolve_coords(args.x, args.y, args.id, args.px)
                click_px(px, py)
                time.sleep(0.05)
            if args.clear:
                press_hotkey("ctrl+a")
                time.sleep(0.03)
                press_key("backspace")
                time.sleep(0.03)
            if cmd == "type":
                type_unicode(args.text, delay=args.delay)
            else:
                paste_text(args.text, restore=args.restore)
            if args.enter:
                time.sleep(0.03)
                press_key("enter")
            res = {"ok": True, "action": cmd, "chars": len(args.text)}
            res = _maybe_post_capture(args, res)
        elif cmd == "key":
            press_key(args.name, count=args.count, delay=args.delay)
            res = {"ok": True, "action": "key", "key": args.name, "count": args.count}
            res = _maybe_post_capture(args, res)
        elif cmd == "hotkey":
            press_hotkey(args.combo)
            res = {"ok": True, "action": "hotkey", "combo": args.combo}
            res = _maybe_post_capture(args, res)
        elif cmd == "clipboard":
            if args.action == "get":
                res = {"ok": True, "text": clipboard_get()}
            else:
                ok = clipboard_set(args.text)
                res = {"ok": ok, "chars": len(args.text)}
        elif cmd == "windows":
            wins = list_windows(args.filter)
            res = {"ok": True, "count": len(wins), "windows": wins}
        elif cmd == "focus":
            w = focus_window(args.target)
            res = {"ok": True, "window": w}
            res = _maybe_post_capture(args, res)
        elif cmd == "winop":
            w = window_op(args.target, args.action, args.rect if args.rect else None, use_px=args.px)
            res = {"ok": True, "action": args.action, "window": w}
        elif cmd == "launch":
            if args.args:
                subprocess.Popen(f'"{args.target}" {args.args}', shell=True, creationflags=0x00000008)
            else:
                os.startfile(args.target)
            res = {"ok": True, "launched": args.target}
        elif cmd == "find-img":
            cm = "dclick" if args.dclick else ("rclick" if args.rclick else ("click" if args.click else None))
            res = do_find_img(args.template, threshold=args.threshold, click_mode=cm)
        elif cmd == "wait-change":
            res = do_wait_change(timeout=args.timeout, interval=args.interval, threshold=args.threshold, region=args.region, use_px=args.px)
            res = _maybe_post_capture(args, res)
        elif cmd == "batch":
            items = json.loads(args.json_steps) if args.json_steps else list(args.steps)
            results = []
            for st in items:
                results.append(run_batch_step(st, use_px=args.px))
            res = {"ok": True, "steps": len(results), "results": results, "elapsed_ms": int((time.perf_counter() - t0) * 1000)}
        else:
            raise ValueError(f"Unknown command: {cmd}")

        print(json.dumps(res, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(1)

if __name__ == "__main__":
    main()
