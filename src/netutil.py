"""
netutil.py - tiny networking helpers shared by web_ui and the widget.

Solves the "port 8765 is taken so nothing starts" failure mode by letting the
web UI pick the first free port in a small range and recording it where the
widget can find it.
"""
from __future__ import annotations

import socket
from pathlib import Path
from typing import Optional

DEFAULT_PORT = 8765
PORT_RANGE = range(8765, 8771)  # 8765..8770


def port_is_free(host: str, port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port(host: str = "127.0.0.1", preferred: int = DEFAULT_PORT,
              candidates=PORT_RANGE) -> int:
    """Return `preferred` if free, else the first free port in `candidates`,
    else fall back to `preferred` (Flask will raise a clear error if truly taken)."""
    if port_is_free(host, preferred):
        return preferred
    for p in candidates:
        if p != preferred and port_is_free(host, p):
            return p
    return preferred


def write_port_file(path, port: int) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(port), encoding="utf-8")
    except Exception:
        pass


def read_port_file(path, default: int = DEFAULT_PORT) -> int:
    try:
        return int(Path(path).read_text(encoding="utf-8").strip())
    except Exception:
        return default
