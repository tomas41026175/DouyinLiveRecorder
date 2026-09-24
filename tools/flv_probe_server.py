"""
flv_probe_server.py -- one-off diagnostic server for MONITOR_PLAN.md Stage 3.

Combines what probe_live_url.py did (resolve a Douyin room URL's playable
FLV/HLS URLs, exactly like main.py does) with serving flv_probe.html, so the
whole test is "paste the live-room URL, click 解析, click 播放" in one page
instead of a manual two-step (run script in terminal, copy the printed URL,
paste into a separate HTML file).

Still does NOT touch main.py, web_ui.py, or require an exe rebuild -- this
is a throwaway diagnostic Flask app, separate from the real app entirely.
Uses Flask since it's already a project dependency (web_ui.py).

Usage (from repo root, same Python env as web_ui.py / main.py):

    python tools/flv_probe_server.py

Then open http://127.0.0.1:8901/ in a browser.
"""
import asyncio
import configparser
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402
from src import spider  # noqa: E402

app = Flask(__name__)
PORT = 8901


def _read_cookie():
    """Mirror main.py's read_config_value(config, 'Cookie', '抖音cookie', '')."""
    cfg_path = os.path.join(_ROOT, "config", "config.ini")
    cp = configparser.RawConfigParser()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            cp.read(cfg_path, encoding=enc)
            break
        except Exception:
            continue
    try:
        return cp.get("Cookie", "抖音cookie")
    except Exception:
        return ""


def _normalize_room_url(raw: str) -> str:
    """Accept a few common paste formats and reduce to the live.douyin.com
    form spider.get_douyin_web_stream_data() expects. Does NOT handle
    v.douyin.com short links (those need an extra redirect-resolve step via
    room.py/get_douyin_app_stream_data, same as main.py's branching) --
    if you pasted one of those, this will tell you rather than silently
    failing."""
    raw = raw.strip()
    if "v.douyin.com" in raw:
        raise ValueError(
            "這是抖音 App 分享的短連結（v.douyin.com），這個診斷工具目前只支援網頁版房間網址"
            "（像 https://live.douyin.com/123456789）。到抖音網頁版打開該主播的直播間，複製"
            "網址列的網址再試一次。")
    if "live.douyin.com" not in raw:
        raise ValueError("看起來不是抖音直播間網址，需要包含 live.douyin.com。")
    return raw


@app.get("/")
def index():
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), "flv_probe.html")


@app.get("/resolve")
def resolve():
    raw_url = request.args.get("url", "")
    try:
        room_url = _normalize_room_url(raw_url)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400

    cookie = _read_cookie()
    try:
        json_data = asyncio.run(spider.get_douyin_web_stream_data(url=room_url, cookies=cookie or None))
    except Exception as e:
        return jsonify({"ok": False, "error": f"解析失敗：{type(e).__name__}: {e}"}), 500

    if not json_data.get("anchor_name"):
        hint = "" if cookie else "（config/config.ini 沒設定 [Cookie] 抖音cookie，用的是內建預設值，可能已過期）"
        return jsonify({"ok": False, "error": f"解析失敗，抖音沒回傳房間資料{hint}",
                        "raw": json_data}), 502

    if json_data.get("status") != 2:
        return jsonify({"ok": False, "error": f"{json_data.get('anchor_name')} 目前不是直播中"}), 200

    stream_url = json_data.get("stream_url") or {}
    flv = dict((stream_url.get("flv_pull_url") or {}))
    hls = dict((stream_url.get("hls_pull_url_map") or {}))
    return jsonify({
        "ok": True,
        "anchor_name": json_data.get("anchor_name"),
        "flv": flv,
        "hls": hls,
    })


if __name__ == "__main__":
    print(f"open http://127.0.0.1:{PORT}/ in a browser")
    app.run(host="127.0.0.1", port=PORT, debug=False)
