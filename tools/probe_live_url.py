"""
probe_live_url.py -- one-off diagnostic for MONITOR_PLAN.md Stage 3.

Resolves a Douyin live room's *current* playable FLV/HLS URLs the exact same
way main.py does (same spider.py/stream.py functions, same cookie config),
WITHOUT touching main.py or requiring an exe rebuild. Purpose: get a real
resolved URL to paste into tools/flv_probe.html, to test the two open risks
in MONITOR_PLAN.md before building the live_status.json + exe-rebuild
machinery:

  1. Does Douyin's CDN allow a browser (flv.js) to fetch the FLV URL
     directly, or does it reject/CORS-block non-ffmpeg clients?
  2. Can the same URL be consumed by ffmpeg (a real recording) AND a
     browser at the same time, or does the CDN kick one of them?

Usage (run from the repo root, with the same Python that runs web_ui.py /
main.py -- needs httpx + execjs + Node, all already project dependencies):

    python tools/probe_live_url.py https://live.douyin.com/123456789

Prints every available quality's FLV and HLS(m3u8) URL. Paste one FLV URL
into tools/flv_probe.html to test risk #1. To test risk #2, start a normal
recording of the same room in the app *while* flv_probe.html is playing the
URL this script printed for that same moment (URLs are per-resolve, not
shared -- see MONITOR_PLAN.md's note that main.py only resolves once per
session, so a fresh probe run while a recording is already in progress is
NOT guaranteed to return the identical URL ffmpeg is using; for a strict
risk #2 test, run this script, then immediately point a *manual* ffmpeg
command or the recorder at that exact same printed URL, or just accept
resolving twice in quick succession as a reasonable proxy for "does the CDN
allow >1 concurrent consumer of this room's stream").
"""
import asyncio
import configparser
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from src import spider, stream  # noqa: E402


def _read_cookie():
    """Mirror main.py's read_config_value(config, 'Cookie', '抖音cookie', '')."""
    cfg_path = os.path.join(_ROOT, "config", "config.ini")
    cp = configparser.RawConfigParser()
    try:
        cp.read(cfg_path, encoding="utf-8-sig")
    except Exception:
        try:
            cp.read(cfg_path, encoding="utf-8")
        except Exception:
            return ""
    try:
        return cp.get("Cookie", "抖音cookie")
    except Exception:
        return ""


async def main(url: str):
    cookie = _read_cookie()
    if not cookie:
        print("[warn] config/config.ini 裡沒讀到 [Cookie] 抖音cookie -- 會用 spider.py 內建的預設"
              "cookie（可能已過期，若下面報錯像是 risk control / 沒有房間資料，先去設定頁補上"
              "真實 cookie 再試一次）。\n")

    json_data = await spider.get_douyin_web_stream_data(url=url, cookies=cookie or None)
    if not json_data.get("anchor_name"):
        print("[error] 解析失敗，看不到主播名稱 -- 通常是 cookie 失效、房間網址錯誤，或這場直播"
              "不是電腦端支援的類型。完整回應：")
        print(json_data)
        return

    print(f"主播: {json_data.get('anchor_name')}")
    print(f"status: {json_data.get('status')} (2 = 直播中)")

    if json_data.get("status") != 2:
        print("[info] 這個房間目前不是直播中，沒有可用的播放網址。")
        return

    try:
        result = await stream.get_douyin_stream_url(json_data, "原画", None)
        print(f"is_live: {result.get('is_live')}")
        print()
        for k, v in result.items():
            if k in ("anchor_name", "is_live"):
                continue
            print(f"[{k}]\n{v}\n")
    except Exception as e:
        print(f"[warn] get_douyin_stream_url() 這層失敗（{type(e).__name__}: {e}），"
              "不影響下面直接印出的原始網址，忽略即可。\n")

    print("--- 全部畫質的原始網址（stream_url.flv_pull_url / hls_pull_url_map）---")
    stream_url = json_data.get("stream_url") or {}
    for quality, u in (stream_url.get("flv_pull_url") or {}).items():
        print(f"FLV  [{quality}] {u}")
    for quality, u in (stream_url.get("hls_pull_url_map") or {}).items():
        print(f"HLS  [{quality}] {u}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: python tools/probe_live_url.py <douyin 直播間網址>")
        sys.exit(1)
    asyncio.run(main(sys.argv[1]))
