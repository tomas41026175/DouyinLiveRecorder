"""
widget.py - Windows tray icon for DouyinLiveRecorder

  - System tray icon shows live recording count as a badge
  - Tray menu:
      Show floating widget     (spawn widget_window.py subprocess)
      Open full dashboard      (browser)
      Stop recorder            (taskkill DouyinLiveRecorder.exe)
      Exit
  - Tooltip updates every 5 seconds: "錄製中 N | 今日 Xh Ym"

Requires: pystray, Pillow, requests

Run:
    python widget.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

import requests
from pystray import Icon, Menu, MenuItem
from PIL import Image, ImageDraw, ImageFont

SELF_DIR = Path(__file__).resolve().parent
WIDGET_WINDOW_SCRIPT = SELF_DIR / "widget_window.py"


def _resolve_webui_url():
    """Read the port web_ui actually bound to (it may have fallen back from
    8765 if that was busy). Defaults to 8765 if the file is missing."""
    port = 8765
    for cand in (SELF_DIR / "config" / "webui_port.txt",
                 SELF_DIR.parent / "config" / "webui_port.txt"):
        try:
            if cand.exists():
                port = int(cand.read_text(encoding="utf-8").strip())
                break
        except Exception:
            pass
    return f"http://127.0.0.1:{port}"


WEBUI_URL = _resolve_webui_url()


def _font():
    """Try to find a font that supports Chinese; fall back gracefully."""
    for name in ("msyhbd.ttc", "msyh.ttc", "simhei.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, 36)
        except Exception:
            pass
    return ImageFont.load_default()


def make_icon_image(count=0, offline=False, disk_full=False):
    """64x64 icon. Orange "!" if disk full, green if recording, cyan if idle,
    gray "X" if offline."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if disk_full:
        bg = (249, 115, 22, 255)    # orange - disk full warning
    elif offline:
        bg = (148, 163, 184, 255)   # gray
    elif count > 0:
        bg = (34, 197, 94, 255)     # green
    else:
        bg = (56, 189, 248, 255)    # cyan
    d.ellipse((2, 2, 62, 62), fill=bg)
    if disk_full:
        text = "!"
    elif offline:
        text = "X"
    else:
        text = str(min(count, 99))
    f = _font()
    try:
        bbox = d.textbbox((0, 0), text, font=f)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        w, h = d.textsize(text, font=f)
    d.text(((64 - w) / 2 - 2, (64 - h) / 2 - 6),
           text, font=f, fill=(255, 255, 255, 255))
    return img


# ---------------------------------------------------------------------------
# Tray actions
# ---------------------------------------------------------------------------
def open_dashboard(icon, item):
    webbrowser.open(WEBUI_URL + "/")


# Track the currently-running floating window so we don't spawn duplicates
_window_proc = None
_window_lock = threading.Lock()


def open_floating(icon, item):
    """Launch widget_window.py as a detached subprocess.
    If a window is already running, do nothing (single-instance behaviour).
    """
    global _window_proc
    with _window_lock:
        # Already alive? skip
        if _window_proc is not None and _window_proc.poll() is None:
            return
        if not WIDGET_WINDOW_SCRIPT.exists():
            webbrowser.open(WEBUI_URL + "/widget")
            return
        try:
            flags = 0
            if os.name == "nt":
                flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            _window_proc = subprocess.Popen(
                [sys.executable, str(WIDGET_WINDOW_SCRIPT)],
                creationflags=flags, close_fds=True)
        except Exception:
            _window_proc = None
            webbrowser.open(WEBUI_URL + "/widget")


def stop_recorder(icon, item):
    if os.name == "nt":
        subprocess.run(["taskkill", "/f", "/t", "/im", "DouyinLiveRecorder.exe"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        subprocess.run(["pkill", "-f", "DouyinLiveRecorder"],
                       capture_output=True)


def quit_app(icon, item):
    icon.stop()


# ---------------------------------------------------------------------------
# Background updater
# ---------------------------------------------------------------------------
def _fmt_gb(b):
    try:
        return f"{b / (1024**3):.1f}GB"
    except Exception:
        return "?"


def updater_loop(icon):
    last_count = -1
    last_offline = None
    last_disk_full = None
    while True:
        try:
            r = requests.get(WEBUI_URL + "/api/status", timeout=2).json()
            disk_full = bool(r.get("disk_full"))
            n = r["recording_count"]
            mon = r["monitoring"]
            today = r["today_total_sec"]
            h = today // 3600
            m = (today % 3600) // 60
            if disk_full:
                free = r.get("disk_free_bytes")
                free_str = f" (剩餘 {_fmt_gb(free)})" if free is not None else ""
                icon.title = (f"⚠ 磁碟空間不足{free_str}！錄製/統計可能失敗，請清理空間。"
                              f"  錄製中 {n}/{mon}")
            else:
                icon.title = (f"DouyinLiveRecorder  錄製中 {n}/{mon}  "
                              f"今日 {h}h{m:02d}m")
            # Redraw the icon when count, offline, or disk-full state changes.
            if n != last_count or last_offline or disk_full != last_disk_full:
                icon.icon = make_icon_image(n, offline=False, disk_full=disk_full)
                last_count = n
                last_offline = False
                last_disk_full = disk_full
        except Exception:
            if last_offline is not True:
                icon.title = "DouyinLiveRecorder (web UI 未啟動)"
                icon.icon = make_icon_image(0, offline=True)
                last_offline = True
                last_count = -1
                last_disk_full = None
        time.sleep(5)


# ---------------------------------------------------------------------------
def _wait_for_webui_and_open(delay=3):
    """Wait briefly for web UI to be reachable, then auto-open floating window."""
    end = time.time() + 30
    while time.time() < end:
        try:
            requests.get(WEBUI_URL + "/api/status", timeout=1)
            break
        except Exception:
            time.sleep(1)
    time.sleep(delay)
    open_floating(None, None)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-window", action="store_true",
                    help="Only show tray icon; do NOT auto-open the floating window.")
    args = ap.parse_args()

    menu = Menu(
        MenuItem("顯示浮動視窗", open_floating, default=True),
        MenuItem("開啟完整控制台", open_dashboard),
        Menu.SEPARATOR,
        MenuItem("停止錄製器", stop_recorder),
        MenuItem("結束 widget", quit_app),
    )
    icon = Icon("DLR", make_icon_image(0, offline=True),
                "DouyinLiveRecorder", menu)
    threading.Thread(target=updater_loop, args=(icon,), daemon=True).start()
    if not args.no_window:
        threading.Thread(target=_wait_for_webui_and_open,
                         daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
