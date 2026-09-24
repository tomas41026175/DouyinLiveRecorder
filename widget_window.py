"""
widget_window.py - Floating frameless window for DouyinLiveRecorder

Opens http://127.0.0.1:8765/widget in a small always-on-top window.
Drag the title area to move; close via X button.

Run from the tray icon menu (widget.py) or directly:
    python widget_window.py
"""
from __future__ import annotations

import webbrowser
from pathlib import Path

import webview


def _resolve_webui_url():
    """Match whatever port web_ui chose (falls back from 8765 if it was busy)."""
    here = Path(__file__).resolve().parent
    port = 8765
    for cand in (here / "config" / "webui_port.txt",
                 here.parent / "config" / "webui_port.txt"):
        try:
            if cand.exists():
                port = int(cand.read_text(encoding="utf-8").strip())
                break
        except Exception:
            pass
    return f"http://127.0.0.1:{port}"


WEBUI_URL = _resolve_webui_url()


class Api:
    def open_full(self):
        webbrowser.open(WEBUI_URL + "/")


def main():
    webview.create_window(
        title="DouyinLiveRecorder",
        url=WEBUI_URL + "/widget",
        js_api=Api(),
        width=340, height=420,
        x=None, y=None,
        resizable=True,
        on_top=True,
        frameless=False,
        easy_drag=False,
        background_color="#0f172a",
    )
    webview.start()


if __name__ == "__main__":
    main()
