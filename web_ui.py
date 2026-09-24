"""
web_ui.py - DouyinLiveRecorder Web Console (standalone)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory, send_file, abort

# Ensure the script's directory is on sys.path so `from src import ...` works
# when launched by pyembed (which has a restricted ._pth-based sys.path).
import os as _os
_script_dir = _os.path.dirname(_os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

try:
    from src import update_checker as _updater
    _updater_error = None
except Exception as _e:
    _updater = None
    _updater_error = f"{type(_e).__name__}: {_e}"
    import traceback as _tb
    print("[web_ui] update_checker import failed:")
    print(_tb.format_exc())

try:
    from src import health as _health
    from src import alerts as _alerts
except Exception as _e:
    _health = None
    _alerts = None
    print(f"[web_ui] health/alerts import failed: {type(_e).__name__}: {_e}")

try:
    from src import disk_manager as _diskmgr
except Exception as _e:
    _diskmgr = None
    print(f"[web_ui] disk_manager import failed: {type(_e).__name__}: {_e}")

try:
    from src import netutil as _netutil
except Exception as _e:
    _netutil = None
    print(f"[web_ui] netutil import failed: {type(_e).__name__}: {_e}")

try:
    from src import compressor as _compressor
except Exception as _e:
    _compressor = None
    print(f"[web_ui] compressor import failed: {type(_e).__name__}: {_e}")

try:
    from src import danmaku_store as _danmaku_store
except Exception as _e:
    _danmaku_store = None
    print(f"[web_ui] danmaku_store import failed: {type(_e).__name__}: {_e}")

try:
    from src import danmaku_capture as _danmaku_capture
except Exception as _e:
    _danmaku_capture = None
    print(f"[web_ui] danmaku_capture import failed: {type(_e).__name__}: {_e}")

try:
    from src import danmaku_subtitle as _danmaku_subtitle
except Exception as _e:
    _danmaku_subtitle = None
    print(f"[web_ui] danmaku_subtitle import failed: {type(_e).__name__}: {_e}")

try:
    from src import marks_store as _marks_store
except Exception as _e:
    _marks_store = None
    print(f"[web_ui] marks_store import failed: {type(_e).__name__}: {_e}")

try:
    from src import discord_bot as _discord_bot
except Exception as _e:
    _discord_bot = None
    print(f"[web_ui] discord_bot import failed: {type(_e).__name__}: {_e}")

try:
    from src import library as _library
except Exception as _e:
    _library = None
    print(f"[web_ui] library import failed: {type(_e).__name__}: {_e}")

# Process-lifetime caches for _library.resolve_video()/resolve_all_segments()
# (see PERF_PLAN.md P0-1). These are the expensive calls in the playback page
# (each one is a real glob.glob() disk scan per candidate extension), and
# group_by_date()/build_playlist() call them once per session -- without a
# cache, clicking an anchor with many 分段錄製 sessions re-globs its entire
# history on every single click, and simply opening the playback page
# (anchor unselected = every session) does the same at an even larger scale.
# Both functions only trust a cache hit after re-verifying (via a cheap
# exists() stat, not a glob) that the previously-resolved path is still
# there, so a file that's since been converted/compressed/moved is never
# served stale -- see resolve_video()/resolve_all_segments() docstrings.
# Restarting web_ui clears these (in-memory only), same as the danmaku
# capture toggle -- no persistence, no cross-restart staleness to worry about.
_RESOLVE_VIDEO_CACHE = {}
_RESOLVE_SEGMENTS_CACHE = {}

try:
    from src import rec_notify as _rec_notify
except Exception as _e:
    _rec_notify = None
    print(f"[web_ui] rec_notify import failed: {type(_e).__name__}: {_e}")

try:
    from src import common as _common
except Exception as _e:
    _common = None
    print(f"[web_ui] common import failed: {type(_e).__name__}: {_e}")

# ---------------------------------------------------------------------------
# Windows: this console runs headless (launched via start_webui_silent.bat /
# ToolLauncher with no console window of its own). Any subprocess we spawn
# without CREATE_NO_WINDOW has nothing to attach to and pops a brand-new,
# instantly-closing console instead — the "flash and disappear" symptom.
# Every short-lived helper subprocess (tasklist/taskkill probes) must use
# this. The one exception is the recorder respawn in api_recorder_restart(),
# which intentionally uses CREATE_NEW_CONSOLE — DouyinLiveRecorder.exe is a
# console app and needs a real console to run.
# ---------------------------------------------------------------------------
def _hidden_kw() -> dict:
    if os.name == "nt":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    SELF_DIR = Path(sys.executable).resolve().parent
    BUNDLE = Path(getattr(sys, "_MEIPASS", str(SELF_DIR)))
    STATIC_DIR = BUNDLE / "web_static"
else:
    SELF_DIR = Path(__file__).resolve().parent
    STATIC_DIR = SELF_DIR / "web_static"


def _is_main_dir(p):
    return (p / "config" / "URL_config.ini").exists() and (
        (p / "DouyinLiveRecorder.exe").exists() or (p / "main.py").exists()
    )


def _resolve_root(cli_root):
    if cli_root:
        return Path(cli_root).expanduser().resolve()
    env = os.environ.get("WEB_UI_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    parent = SELF_DIR.parent
    if _is_main_dir(parent):
        return parent
    if _is_main_dir(SELF_DIR):
        return SELF_DIR
    return SELF_DIR


ROOT = _resolve_root(None)
URL_CONFIG = ROOT / "config" / "URL_config.ini"
DB_FILE = ROOT / "config" / "recording_history.db"
LIMITS_FILE = ROOT / "config" / "streamer_limits.json"
APP_CONFIG = ROOT / "config" / "config.ini"
ALERTS_FILE = ROOT / "config" / "alerts.json"
PORT_FILE = ROOT / "config" / "webui_port.txt"
COMPRESS_FILE = ROOT / "config" / "compress.json"
COMPRESS_STATE = ROOT / "config" / "compress_state.json"
DANMAKU_DB = ROOT / "config" / "danmaku.db"
REC_NOTIFY_FILE = ROOT / "config" / "rec_notify.json"
MARKS_DB = ROOT / "config" / "marks.db"
DISCORD_BOT_FILE = ROOT / "config" / "discord_bot.json"


def _apply_root(new_root):
    global ROOT, URL_CONFIG, DB_FILE, LIMITS_FILE, APP_CONFIG, ALERTS_FILE, PORT_FILE
    global COMPRESS_FILE, COMPRESS_STATE, DANMAKU_DB, REC_NOTIFY_FILE, MARKS_DB
    global DISCORD_BOT_FILE
    ROOT = new_root
    URL_CONFIG = ROOT / "config" / "URL_config.ini"
    DB_FILE = ROOT / "config" / "recording_history.db"
    LIMITS_FILE = ROOT / "config" / "streamer_limits.json"
    APP_CONFIG = ROOT / "config" / "config.ini"
    ALERTS_FILE = ROOT / "config" / "alerts.json"
    PORT_FILE = ROOT / "config" / "webui_port.txt"
    COMPRESS_FILE = ROOT / "config" / "compress.json"
    COMPRESS_STATE = ROOT / "config" / "compress_state.json"
    DANMAKU_DB = ROOT / "config" / "danmaku.db"
    REC_NOTIFY_FILE = ROOT / "config" / "rec_notify.json"
    MARKS_DB = ROOT / "config" / "marks.db"
    DISCORD_BOT_FILE = ROOT / "config" / "discord_bot.json"


_PLATFORM_HOSTS = [
    (r"douyin\.com", "抖音"), (r"tiktok\.com", "TikTok"),
    (r"kuaishou\.com", "快手"), (r"huya\.com", "虎牙"),
    (r"douyu\.com", "斗鱼"), (r"yy\.com", "YY"),
    (r"bilibili\.com", "B站"),
    (r"xiaohongshu\.com|xhslink\.com", "小红书"),
    (r"bigo\.tv|bigovideo\.tv", "Bigo"), (r"blued\.cn", "Blued"),
    (r"sooplive\.co\.kr|sooplive\.com", "SOOP"),
    (r"cc\.163\.com", "网易CC"), (r"qiandurebo\.com", "千度热播"),
    (r"pandalive\.co\.kr", "PandaTV"), (r"missevan\.com", "猫耳FM"),
    (r"winktv\.co\.kr", "WinkTV"),
    (r"flextv\.co\.kr|ttinglive\.com", "FlexTV"),
    (r"look\.163\.com", "Look"), (r"popkontv\.com", "PopkonTV"),
    (r"twitcasting\.tv", "TwitCasting"), (r"baidu\.com", "百度"),
    (r"weibo\.com", "微博"), (r"kugou\.com", "酷狗"),
    (r"twitch\.tv", "TwitchTV"), (r"liveme\.com", "LiveMe"),
    (r"huajiao\.com", "花椒"), (r"7u66\.com", "流星"),
    (r"showroom-live\.com", "ShowRoom"), (r"acfun\.cn", "Acfun"),
    (r"chzzk\.naver\.com", "CHZZK"), (r"haixiutv\.com", "嗨秀"),
    (r"vvxqiu\.com", "VV星球"), (r"17\.live", "17Live"),
    (r"lang\.live", "浪Live"), (r"pp\.weimipopo\.com", "漂漂"),
    (r"\.6\.cn", "六间房"), (r"lehaitv\.com", "乐嗨"),
    (r"catshow168\.com", "花猫"),
    (r"live\.shopee|shp\.ee", "Shopee"),
    (r"youtube\.com|youtu\.be", "Youtube"),
    (r"tb\.cn|taobao", "淘宝"), (r"jd\.com", "京东"),
    (r"faceit\.com", "Faceit"), (r"miguvideo\.com", "咪咕"),
    (r"lailianjie\.com", "连接"), (r"imkktv\.com", "来秀"),
    (r"picarto\.tv", "Picarto"),
]


def guess_platform(url):
    for pat, name in _PLATFORM_HOSTS:
        if re.search(pat, url):
            return name
    return "其他"


# ---------------------------------------------------------------------------
# Save path from config.ini (the recorder's "直播保存路径")
# ---------------------------------------------------------------------------
def get_save_path():
    if not APP_CONFIG.exists():
        return ROOT / "downloads"
    try:
        text = APP_CONFIG.read_text(encoding="utf-8-sig")
        m = re.search(r"^直播保存路径\([^)]*\)\s*=\s*(.*?)\s*$",
                      text, re.MULTILINE)
        if m and m.group(1).strip():
            return Path(m.group(1).strip())
    except Exception:
        pass
    return ROOT / "downloads"


def get_folder_rules():
    """Returns dict describing how the recorder organizes folders."""
    out = {"by_author": True, "by_time": False, "by_title": False}
    if not APP_CONFIG.exists():
        return out
    try:
        text = APP_CONFIG.read_text(encoding="utf-8-sig")
        for key, attr, default in [
            (r"保存文件夹是否以作者区分", "by_author", True),
            (r"保存文件夹是否以时间区分", "by_time", False),
            (r"保存文件夹是否以标题区分", "by_title", False),
        ]:
            m = re.search(rf"^{key}\s*=\s*(.*?)\s*$", text, re.MULTILINE)
            if m:
                out[attr] = m.group(1).strip() == "是"
    except Exception:
        pass
    return out


def get_segment_seconds():
    """录制设置 -> 视频分段时间(秒) (main.py's split_time, default 1800/30min
    when unset). Needed to estimate each segment's start_time/duration_sec
    when browsing a 分段錄製 session in 回放 (library.build_playlist splits
    one DB session row into one row per real segment file -- see stage 12)."""
    if APP_CONFIG.exists():
        try:
            text = APP_CONFIG.read_text(encoding="utf-8-sig")
            m = re.search(r"^视频分段时间\(秒\)\s*=\s*(.*?)\s*$", text, re.MULTILINE)
            if m and m.group(1).strip():
                return int(m.group(1).strip())
        except Exception:
            pass
    return 1800


# ---------------------------------------------------------------------------
# config.ini whitelist read/write (recorder settings GUI, P1-1 / /api/config,
# UIUX_SPEC §3.6 + §6). Only lines that ALREADY exist in config.ini are ever
# touched -- for each whitelisted key we regex-substitute just the value after
# "=" on that key's own line, using a function-based replacement (never a
# backreference string, since values like Windows paths contain backslashes
# that would otherwise be misread as regex group refs). Comments, section
# headers, key order/spacing, the BOM and every non-whitelisted key are left
# byte-for-byte untouched; write-back uses the same
# `write_text(..., encoding="utf-8-sig")` (no explicit newline=) convention
# `_write_lines()` already uses for URL_config.ini, so CRLF line endings on
# Windows round-trip the same way they already do there.
# ---------------------------------------------------------------------------
CONFIG_QUALITY_OPTIONS = ["原画", "蓝光", "超清", "高清", "标清", "流畅"]
CONFIG_FORMAT_OPTIONS = ["ts", "mkv", "flv", "mp4", "mp3音频", "m4a音频"]

# (field id exposed over the API, literal ini key text, value kind)
CONFIG_FIELDS = [
    ("quality", "原画|超清|高清|标清|流畅", "enum_quality"),
    ("save_path", "直播保存路径(不填则默认)", "str"),
    ("folder_by_author", "保存文件夹是否以作者区分", "bool"),
    ("folder_by_time", "保存文件夹是否以时间区分", "bool"),
    ("folder_by_title", "保存文件夹是否以标题区分", "bool"),
    ("filename_with_title", "保存文件名是否包含标题", "bool"),
    ("video_format", "视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频", "enum_format"),
    ("loop_seconds", "循环时间(秒)", "int"),
    ("segment_enabled", "分段录制是否开启", "bool"),
    ("segment_seconds", "视频分段时间(秒)", "int"),
]

_CONFIG_COOKIE_SECTION = "[Cookie]"


def _config_read_text():
    """Raw config.ini text (utf-8-sig decoded -> BOM stripped, universal
    newlines), or None if the file doesn't exist. Pure IO, no parsing."""
    if not APP_CONFIG.exists():
        return None
    return APP_CONFIG.read_text(encoding="utf-8-sig")


def _config_line_regex(key):
    return re.compile(r"^(" + re.escape(key) + r"[ \t]*=[ \t]*)([^\r\n]*)$", re.MULTILINE)


def _config_get_raw(text, key):
    """Raw string value (whitespace-trimmed) after '=' on `key`'s line, or
    None if that key's line isn't present in `text`."""
    m = _config_line_regex(key).search(text)
    if not m:
        return None
    return m.group(2).strip()


def _config_set_raw(text, key, new_value):
    """Replace only the value on `key`'s existing line. Returns (new_text,
    changed_bool). No-ops (changed=False) if the key's line isn't present --
    we never invent new lines, so writes stay confined to what's already
    there. Uses a replacement function (not a backref string) so values
    containing backslashes/`\\g<n>`-looking text can't corrupt the regex."""
    pattern = _config_line_regex(key)
    if not pattern.search(text):
        return text, False
    new_text = pattern.sub(lambda m: m.group(1) + new_value, text, count=1)
    return new_text, True


def _config_cookie_keys(text):
    """Cookie key names that literally appear under the `[Cookie]` section, in
    file order. Discovered dynamically (not hardcoded) so every platform's
    cookie field -- including ones the upstream project adds later -- is
    whitelisted automatically; per `_config_set_raw`, we still only ever
    overwrite lines that already exist, so this can't be used to inject new
    keys."""
    keys = []
    in_section = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_section = (s == _CONFIG_COOKIE_SECTION)
            continue
        if not in_section or not s or s.startswith("#") or s.startswith(";"):
            continue
        if "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        if key:
            keys.append(key)
    return keys


def _config_bool_to_cn(v):
    return "是" if v else "否"


def _config_parse_all(text):
    """Whitelisted fields + cookie values out of config.ini text -> dict
    (JSON-shaped for GET /api/config). Pure function, no IO."""
    out = {}
    for fid, key, kind in CONFIG_FIELDS:
        raw = _config_get_raw(text, key)
        if raw is None:
            out[fid] = None
        elif kind == "bool":
            out[fid] = (raw == "是")
        elif kind == "int":
            try:
                out[fid] = int(raw)
            except ValueError:
                out[fid] = raw
        else:
            out[fid] = raw
    out["cookies"] = {ck: (_config_get_raw(text, ck) or "") for ck in _config_cookie_keys(text)}
    return out


def _config_apply_updates(text, payload):
    """Apply whitelisted updates from `payload` (dict, PUT /api/config body)
    onto `text`. Returns (new_text, changed_ids). Unknown field ids and
    cookie keys not already present in the file are silently ignored -- the
    frontend only ever sends whitelisted ids, this is just defense in depth
    so a malformed request can't touch anything outside the whitelist or
    values failing validation (bad enum choice / non-integer)."""
    changed = []
    if not isinstance(payload, dict):
        return text, changed
    for fid, key, kind in CONFIG_FIELDS:
        if fid not in payload:
            continue
        val = payload[fid]
        if kind == "bool":
            raw = _config_bool_to_cn(bool(val))
        elif kind == "int":
            try:
                raw = str(int(val))
            except (TypeError, ValueError):
                continue
        elif kind == "enum_quality":
            if val not in CONFIG_QUALITY_OPTIONS:
                continue
            raw = val
        elif kind == "enum_format":
            if val not in CONFIG_FORMAT_OPTIONS:
                continue
            raw = val
        else:
            raw = "" if val is None else str(val)
        text, ok = _config_set_raw(text, key, raw)
        if ok:
            changed.append(fid)
    cookies_payload = payload.get("cookies")
    if isinstance(cookies_payload, dict):
        cookie_keys = set(_config_cookie_keys(text))
        for ck, val in cookies_payload.items():
            if ck not in cookie_keys:
                continue  # not an existing whitelisted cookie line
            text, ok = _config_set_raw(text, ck, "" if val is None else str(val))
            if ok:
                changed.append(f"cookies.{ck}")
    return text, changed


def _config_response_payload(text, changed=None):
    data = _config_parse_all(text)
    data["exists"] = True
    data["quality_options"] = CONFIG_QUALITY_OPTIONS
    data["video_format_options"] = CONFIG_FORMAT_OPTIONS
    if changed is not None:
        data["changed"] = changed
        data["needs_recorder_restart"] = bool(changed)
        data["message"] = (
            "設定已儲存；需重啟錄製器（DouyinLiveRecorder.exe）才會套用"
            if changed else "沒有變更（送出的值與目前設定相同，或欄位不在白名單內）"
        )
    return data


# ---------------------------------------------------------------------------
# Per-streamer disk usage (recorder saves to <save_path>/<platform>直播/<anchor>/)
# Cached for 60 seconds to keep /api/streamers fast.
# ---------------------------------------------------------------------------
_size_cache = {}      # path -> (size_bytes, computed_at)
_size_ttl = 60        # seconds


def _platform_folder_name(platform):
    """Folder names main.py uses: '抖音' -> '抖音直播' etc."""
    return f"{platform}直播" if platform else "未知平台"


def get_anchor_folder(entry):
    """Best-effort guess at the on-disk folder for this URL_config entry."""
    if not entry.get("anchor_name"):
        return None
    save = get_save_path()
    return save / _platform_folder_name(entry.get("platform")) / entry["anchor_name"]


def _folder_size(path):
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += (Path(root) / f).stat().st_size
                except Exception:
                    pass
    except Exception:
        return 0
    return total


def get_folder_size_cached(path):
    if not path:
        return 0
    key = str(path)
    now = time.time()
    cached = _size_cache.get(key)
    if cached and now - cached[1] < _size_ttl:
        return cached[0]
    if not Path(path).exists():
        _size_cache[key] = (0, now)
        return 0
    size = _folder_size(path)
    _size_cache[key] = (size, now)
    return size


def invalidate_size_cache(path=None):
    if path is None:
        _size_cache.clear()
    else:
        _size_cache.pop(str(path), None)


# ---------------------------------------------------------------------------
# URL_config.ini I/O
# ---------------------------------------------------------------------------
_url_lock = threading.Lock()


def _read_lines():
    if not URL_CONFIG.exists():
        return []
    return URL_CONFIG.read_text(encoding="utf-8-sig").splitlines()


def _write_lines(lines):
    if URL_CONFIG.exists():
        try:
            shutil.copy2(URL_CONFIG, URL_CONFIG.with_suffix(".ini.bak"))
        except Exception:
            pass
    URL_CONFIG.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


_QUALITY_PREFIX = {"原画", "蓝光", "超清", "高清", "标清", "流畅"}


def parse_entry(line):
    raw = line
    enabled = True
    if raw.lstrip().startswith("#"):
        enabled = False
        raw = raw.lstrip().lstrip("#").lstrip()
    raw = raw.strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split(",", 2)]
    quality = None
    url_part = None
    anchor_part = ""
    if parts and parts[0] in _QUALITY_PREFIX:
        quality = parts[0]
        if len(parts) >= 2:
            url_part = parts[1]
        if len(parts) >= 3:
            anchor_part = parts[2]
    else:
        url_part = parts[0] if parts else ""
        if len(parts) >= 2:
            anchor_part = parts[1]
        if len(parts) >= 3:
            anchor_part = parts[1] + "," + parts[2]
    if not url_part or not re.match(r"https?://", url_part):
        return None
    anchor_name = ""
    m = re.search(r"主播[:：]\s*(.+)$", anchor_part)
    if m:
        anchor_name = m.group(1).strip()
    elif anchor_part:
        anchor_name = anchor_part.strip()
    return {
        "enabled": enabled, "quality": quality, "url": url_part,
        "anchor_name": anchor_name, "platform": guess_platform(url_part),
        "raw": line,
    }


def entry_to_line(entry):
    pieces = []
    if entry.get("quality"):
        pieces.append(entry["quality"])
    pieces.append(entry["url"])
    if entry.get("anchor_name"):
        pieces.append(f"主播: {entry['anchor_name']}")
    line = ",".join(pieces)
    if not entry.get("enabled", True):
        line = "#" + line
    return line


def list_entries():
    out = []
    for idx, line in enumerate(_read_lines()):
        e = parse_entry(line)
        if e is None:
            continue
        e["line_no"] = idx
        out.append(e)
    return out


def set_enabled(url, enabled, reason=""):
    with _url_lock:
        lines = _read_lines()
        for i, line in enumerate(lines):
            e = parse_entry(line)
            if e and e["url"] == url:
                if e["enabled"] == enabled:
                    return False
                e["enabled"] = enabled
                lines[i] = entry_to_line(e)
                _write_lines(lines)
                print(f"[limits] {'enable' if enabled else 'DISABLE'} {url}"
                      f"{(' -- ' + reason) if reason else ''}")
                return True
    return False


# ---------------------------------------------------------------------------
# Discord 控制頻道 callbacks -- 跟 api_streamers_create()/api_streamers_delete()
# 共用同一套 _read_lines()/_write_lines()/parse_entry()/entry_to_line()/
# _url_lock 底層，但獨立成純函式（不碰 Flask request/response）方便從
# discord_bot.py 的背景 bot thread 直接呼叫。刻意跟那兩支 Flask handler 保持
# 各自獨立、不合併：Discord 指令只需要 url+anchor_name 兩個欄位，合併會讓兩邊
# 都要遷就對方的參數形狀，得不償失。
# ---------------------------------------------------------------------------
def _discord_add_streamer(url: str, anchor_name: str = ""):
    """Returns (ok, message)."""
    if not url or not re.match(r"https?://", url):
        return False, f"網址格式看起來不對：{url!r}"
    with _url_lock:
        lines = _read_lines()
        for ln in lines:
            p = parse_entry(ln)
            if p and p["url"] == url:
                return False, "這個網址已經在清單裡了"
        lines.append(entry_to_line({"enabled": True, "quality": None,
                                    "url": url, "anchor_name": anchor_name}))
        _write_lines(lines)
    return True, f"已新增：{anchor_name or url}"


def _discord_remove_streamer(url: str):
    """Returns (ok, message)."""
    with _url_lock:
        lines = _read_lines()
        kept, removed_name = [], None
        for ln in lines:
            p = parse_entry(ln)
            if p and p["url"] == url and removed_name is None:
                removed_name = p.get("anchor_name") or url
                continue
            kept.append(ln)
        if removed_name is None:
            return False, "找不到這個網址（可能已經被移除了）"
        _write_lines(kept)
    return True, f"已移除：{removed_name}"


def _discord_list_streamers():
    """Same shape list_entries() already returns, plus is_recording -- what
    discord_bot.format_list()/find_matching_entries() expect."""
    try:
        active_urls = {a["live_url"] for a in active_sessions()}
    except Exception:
        active_urls = set()
    return [{**e, "is_recording": e["url"] in active_urls} for e in list_entries()]


def _start_discord_bot():
    if _discord_bot is None:
        return False
    settings = _discord_bot.load_settings(DISCORD_BOT_FILE)
    return _discord_bot.start(settings, {
        "add": _discord_add_streamer,
        "remove": _discord_remove_streamer,
        "list": _discord_list_streamers,
    })


# ---------------------------------------------------------------------------
# Limits + note storage (config/streamer_limits.json)
# ---------------------------------------------------------------------------
_limits_lock = threading.Lock()
_DEFAULT_COOLDOWN = 0   # 0 = never auto-resume; user must opt in per URL


def load_limits():
    if not LIMITS_FILE.exists():
        return {"_state": {}}
    try:
        return json.loads(LIMITS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"_state": {}}


def save_limits(data):
    LIMITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIMITS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_limit(url):
    data = load_limits()
    return data.get(url) or {}


def set_limit(url, max_session_minutes=None, cooldown_minutes=None, note=None,
              priority=None):
    with _limits_lock:
        data = load_limits()
        entry = data.get(url) or {}
        if max_session_minutes is not None:
            if int(max_session_minutes) <= 0:
                entry.pop("max_session_minutes", None)
            else:
                entry["max_session_minutes"] = int(max_session_minutes)
        if cooldown_minutes is not None:
            # 0 / empty = clear -> revert to default daily-reset behaviour
            if int(cooldown_minutes) <= 0:
                entry.pop("cooldown_minutes", None)
            else:
                entry["cooldown_minutes"] = int(cooldown_minutes)
        if note is not None:
            note = note.strip()
            if note:
                entry["note"] = note
            else:
                entry.pop("note", None)
        if priority is not None:
            # 特別關注：True 才寫入；False 直接移除鍵讓 JSON 保持乾淨。
            if priority:
                entry["priority"] = True
            else:
                entry.pop("priority", None)
        if entry:
            data[url] = entry
        else:
            data.pop(url, None)
        save_limits(data)


def mark_disabled_by_limit(url, reason):
    with _limits_lock:
        data = load_limits()
        state = data.setdefault("_state", {})
        state[url] = {
            "disabled_at": datetime.now().isoformat(timespec="seconds"),
            "reason": reason,
        }
        save_limits(data)


def clear_disabled_state(url):
    with _limits_lock:
        data = load_limits()
        state = data.setdefault("_state", {})
        if url in state:
            del state[url]
            save_limits(data)


# ---------------------------------------------------------------------------
# DB reads
# ---------------------------------------------------------------------------
def _db_query(sql, params=()):
    if not DB_FILE.exists():
        return []
    con = sqlite3.connect(DB_FILE, timeout=5)
    con.execute("PRAGMA journal_mode=WAL;")
    try:
        return list(con.execute(sql, params))
    except sqlite3.OperationalError as e:
        # DB 檔案已存在但 recording_sessions 表還沒建立時會走到這裡
        # （例如剛裝好、web_ui 在錄製器第一次寫入前就先被打開；或
        # config/recording_history.db 是空白 placeholder 檔）。schema
        # 建立的權責在 src/duration_tracker.py（DurationTracker._init_db
        # 的 CREATE TABLE IF NOT EXISTS），web_ui.py 只負責唯讀查詢、不
        # 在這裡搶著建表，因此把「表還不存在」視同「還沒有任何資料」
        # 回傳空結果，而不是讓 sqlite3.OperationalError 一路往上炸成
        # Flask 500（此為統計頁 #stats 顯示「載入失敗」的根因：前端
        # api() 對非 2xx 回應的 HTML 錯誤頁 json 解析失敗後直接 throw）。
        if "no such table" in str(e):
            return []
        raise
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Disk-full detection / graceful degradation
# ---------------------------------------------------------------------------
# When the drive holding the DB / recordings runs out of space, SQLite raises
# "disk I/O error" and file writes raise OSError(errno=28). Instead of letting
# /api/status spam HTTP 500s (which makes the widget look merely "offline"), we
# detect this condition and return a clear `disk_full` flag + message, while
# still reporting filesystem-based disk usage so the user can see what's eating
# the space and clean it up.
DISK_LOW_THRESHOLD = 200 * 1024 * 1024  # < 200 MB free is treated as "full"


def _is_disk_full_error(exc):
    """True if the exception was caused by the disk being full / unwritable."""
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return True
    msg = str(exc).lower()
    return ("disk i/o error" in msg
            or "no space left" in msg
            or "database or disk is full" in msg
            or "disk full" in msg)


def disk_usage_info():
    """Return free/total/used bytes for the drive holding the DB / save path.
    Reads filesystem metadata only, so it works even when the disk is full.
    Never raises."""
    candidates = []
    try:
        candidates.append(DB_FILE.parent)
    except Exception:
        pass
    try:
        candidates.append(get_save_path())
    except Exception:
        pass
    candidates.append(ROOT)
    for target in candidates:
        try:
            if target and Path(target).exists():
                u = shutil.disk_usage(str(target))
                return {"free_bytes": u.free, "total_bytes": u.total,
                        "used_bytes": u.used, "low": u.free < DISK_LOW_THRESHOLD}
        except Exception:
            continue
    return {"free_bytes": None, "total_bytes": None,
            "used_bytes": None, "low": False}


# ---------------------------------------------------------------------------
# Recorder process probe + health snapshot + alert watchdog
# ---------------------------------------------------------------------------
_recorder_state = {"running": None, "checked_at": 0.0}
_recorder_ttl = 8  # seconds; avoid hammering tasklist on every /api/status


def recorder_running():
    """Best-effort check whether DouyinLiveRecorder is alive (cached briefly).
    Returns True/False, or None if it cannot be determined (e.g. non-Windows)."""
    now = time.time()
    if _recorder_state["running"] is not None and \
            now - _recorder_state["checked_at"] < _recorder_ttl:
        return _recorder_state["running"]
    running = None
    try:
        if os.name == "nt":
            import subprocess
            out = subprocess.run(
                ["tasklist", "/fi", "imagename eq DouyinLiveRecorder.exe", "/nh"],
                capture_output=True, text=True, timeout=5, **_hidden_kw()).stdout
            running = "DouyinLiveRecorder.exe" in out
        else:
            running = None  # not meaningful in dev/non-Windows
    except Exception:
        running = None
    _recorder_state.update(running=running, checked_at=now)
    return running


def _alerts_settings():
    if _alerts is None:
        return {"enabled": False}
    return _alerts.load_alerts(ALERTS_FILE)


def build_health_snapshot():
    """Gather a snapshot for the health evaluator. Never raises."""
    disk = disk_usage_info()
    # DB writability probe (cheap): a failed read here means the disk/DB is sick.
    db_ok = True
    try:
        _db_query("SELECT 1")
    except Exception as exc:
        if _is_disk_full_error(exc):
            db_ok = False
        else:
            db_ok = True  # unrelated error; don't cry disk-full
    try:
        enabled = sum(1 for e in list_entries() if e["enabled"])
    except Exception:
        enabled = 0
    rec = recorder_running()
    snap = {
        "disk_free_bytes": disk["free_bytes"],
        "disk_total_bytes": disk["total_bytes"],
        "db_ok": db_ok,
        "recorder_expected": enabled > 0,
    }
    if rec is not None:
        snap["recorder_running"] = rec
    return snap


def recent_finished_sessions(hours=24, limit=40):
    """Recent finished sessions with on-disk file size, for integrity checks.
    Returns list of dicts; never raises."""
    out = []
    try:
        since = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        rows = _db_query(
            "SELECT anchor_name, platform, duration_sec, file_path, finished_reason "
            "FROM recording_sessions WHERE end_time IS NOT NULL AND start_time >= ? "
            "ORDER BY start_time DESC LIMIT ?",
            (since, limit))
        for anchor, platform, dur, fpath, reason in rows:
            size = None
            exists = None
            if fpath:
                try:
                    p = Path(fpath)
                    exists = p.exists()
                    size = p.stat().st_size if exists else 0
                except Exception:
                    exists = None
            out.append({
                "anchor_name": anchor, "platform": platform,
                "duration_sec": dur, "file_size": size,
                "file_exists": exists, "finished_reason": reason,
            })
    except Exception:
        pass
    return out


def _extra_health_issues():
    """Suspect-recording + disk-pause issues to merge into the base health.
    Returns a list of issue dicts (possibly empty). Never raises."""
    issues = []
    if _health is not None:
        try:
            r = _health.evaluate_recordings(recent_finished_sessions())
            if r.get("issue"):
                issues.append(r["issue"])
        except Exception:
            pass
    return issues


def evaluate_current_health():
    """Run the health evaluator against a fresh snapshot, merging in the
    suspect-recording check. Returns 'ok' if the health module is unavailable."""
    if _health is None:
        return {"status": "ok", "issues": [], "checked_keys": []}
    s = _alerts_settings()
    base = _health.evaluate_health(
        build_health_snapshot(),
        disk_warn_gb=float(s.get("disk_warn_gb", 10)),
        disk_critical_gb=float(s.get("disk_critical_gb", 2)),
    )
    extra = _extra_health_issues()
    if extra:
        base["issues"] = list(base.get("issues", [])) + extra
        base["status"] = _health.worst([i["severity"] for i in base["issues"]])
    return base


_WATCHDOG_INTERVAL = 60


def _watchdog_loop():
    # Seed rec_notify so already-in-progress recordings don't all fire as "started".
    if _rec_notify is not None:
        try:
            _rec_notify.prime(active_sessions())
        except Exception:
            pass
    while True:
        try:
            if _health is not None and _alerts is not None:
                cfg = _alerts_settings()
                if cfg.get("enabled"):
                    health = evaluate_current_health()
                    _alerts.process_health(cfg, health)
        except Exception as e:
            print(f"[watchdog] error: {e}")
        # Recording start/stop -> Discord/webhook (independent of health alerts).
        try:
            if _rec_notify is not None:
                rn_cfg = _rec_notify.load_settings(REC_NOTIFY_FILE)
                _rec_notify.process_active(rn_cfg, active_sessions())
        except Exception as e:
            print(f"[rec_notify] error: {e}")
        time.sleep(_WATCHDOG_INTERVAL)


def start_watchdog_thread():
    # Run the loop if EITHER health-alerts or rec-notify is available.
    if (_health is None or _alerts is None) and _rec_notify is None:
        return None
    t = threading.Thread(target=_watchdog_loop, daemon=True, name="health-watchdog")
    t.start()
    return t


def _url_to_config_entry():
    return {e["url"]: e for e in list_entries()}


def _clean_db_anchor(name):
    if not name:
        return ""
    m = re.match(r"^\s*序号\s*\d+\s+(.+)$", name)
    return m.group(1).strip() if m else name


def active_sessions():
    rows = _db_query(
        "SELECT anchor_name, platform, live_url, quality, start_time, file_path "
        "FROM recording_sessions WHERE end_time IS NULL ORDER BY start_time"
    )
    out = []
    now = datetime.now()
    url_map = _url_to_config_entry()
    limits = load_limits()
    for anchor, platform, url, quality, start_str, file_path in rows:
        try:
            start = datetime.fromisoformat(start_str)
            elapsed = int((now - start).total_seconds())
        except Exception:
            elapsed = 0
        cfg = url_map.get(url)
        display_anchor = (cfg and cfg["anchor_name"]) or _clean_db_anchor(anchor)
        display_platform = (cfg and cfg["platform"]) or platform
        display_quality = (cfg and cfg.get("quality")) or quality
        lim = limits.get(url) or {}
        max_min = lim.get("max_session_minutes")
        note = lim.get("note") or ""
        remaining_sec = None
        if max_min:
            remaining_sec = max_min * 60 - elapsed
        out.append({
            "anchor_name": display_anchor,
            "note": note,
            "platform": display_platform,
            "live_url": url,
            "quality": display_quality,
            "start_time": start_str,
            "elapsed_sec": elapsed,
            "in_config": cfg is not None,
            "max_session_minutes": max_min,
            "remaining_sec": remaining_sec,
            "file_path": file_path,
        })
    return out


def totals_today():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = _db_query(
        "SELECT COALESCE(SUM(duration_sec),0) FROM recording_sessions "
        "WHERE start_time >= ? AND end_time IS NOT NULL",
        (today.isoformat(timespec="seconds"),),
    )
    return int(rows[0][0]) if rows else 0


def grand_total():
    rows = _db_query(
        "SELECT COALESCE(SUM(duration_sec),0) FROM recording_sessions "
        "WHERE end_time IS NOT NULL"
    )
    return int(rows[0][0]) if rows else 0


def _per_url_totals():
    rows = _db_query(
        "SELECT live_url, COALESCE(SUM(duration_sec),0), COUNT(*) "
        "FROM recording_sessions WHERE end_time IS NOT NULL "
        "GROUP BY live_url"
    )
    return {r[0]: (int(r[1]), int(r[2])) for r in rows}


# ---------------------------------------------------------------------------
# Stats page (P1-2, #stats, UIUX_SPEC §3.5). Thin wrappers around the same
# recording_sessions queries query_duration.py / duration_tracker.py already
# run (total_by_anchor / sessions_for / total_seconds) -- reimplemented here
# against _db_query instead of importing DurationTracker, because that class
# resolves its own ROOT independently of web_ui's ROOT (source dir vs frozen
# exe can differ; see AGENT.md §3), and every other stat already served by
# this file (totals_today/grand_total/_per_url_totals above) goes through
# _db_query for the same reason. Query shapes match query_duration.py 1:1.
# ---------------------------------------------------------------------------
def _stats_since_for_range(range_key):
    """Map a UI range key to a `since` datetime cutoff. None = all history."""
    now = datetime.now()
    if range_key == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if range_key == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None


def _stats_canonical_anchor(raw_name):
    """Same fix as the playback anchor list (library.canonical_anchor_name):
    main.py prefixes anchor_name with a shifting `序号N ` config-line-number
    for console disambiguation, which used to fragment one real streamer into
    several rows here (排行榜/摘要主播數/明細 all grouped/filtered on the raw
    column). Every stats aggregation below groups by this instead."""
    if _library is not None:
        return _library.canonical_anchor_name(raw_name) or "(未知主播)"
    return raw_name or "(未知主播)"


def _stats_raw_sessions(since=None):
    """Finished sessions (anchor_name/live_url/start_time/duration_sec) in the
    given range, RAW anchor_name -- callers canonicalize as needed. Shared by
    ranking/summary so canonicalization and (expensive-ish) per-session
    danmaku counting only happen once per request, not once per caller."""
    sql = ("SELECT anchor_name, live_url, start_time, duration_sec "
           "FROM recording_sessions WHERE end_time IS NOT NULL ")
    params = []
    if since is not None:
        sql += "AND start_time >= ? "
        params.append(since.isoformat(timespec="seconds"))
    rows = _db_query(sql, params)
    return [{"anchor_name": r[0], "live_url": r[1], "start_time": r[2],
             "duration_sec": r[3]} for r in rows]


def _stats_session_danmaku_count(s):
    """Danmaku message count for one session's time window. Same matching
    rule as /api/library/clip/<id>/danmaku: prefer the room URL (stable even
    if the anchor gets renamed), fall back to the raw anchor_name capture used
    when there's no live_url on record."""
    if _danmaku_store is None:
        return 0
    try:
        start_epoch = int(datetime.fromisoformat(s.get("start_time") or "").timestamp())
    except Exception:
        return 0
    dur = s.get("duration_sec")
    until = start_epoch + int(dur) + 5 if dur else None
    live_url = s.get("live_url") or None
    raw_anchor = s.get("anchor_name") or None
    try:
        store = _danmaku_store.get_store(DANMAKU_DB)
        return store.count(url=live_url, anchor=None if live_url else raw_anchor,
                           since=start_epoch, until=until)
    except Exception:
        return 0


def _stats_ranking(since=None, limit=200):
    merged = {}
    for s in _stats_raw_sessions(since):
        raw_name = s["anchor_name"]
        key = _stats_canonical_anchor(raw_name)
        m = merged.setdefault(key, {"anchor_name": key, "total_sec": 0,
                                    "sessions": 0, "danmaku_count": 0,
                                    "_raw_names": set()})
        m["total_sec"] += int(s["duration_sec"] or 0)
        m["sessions"] += 1
        m["danmaku_count"] += _stats_session_danmaku_count(s)
        m["_raw_names"].add(raw_name)
    out = []
    for m in merged.values():
        # 診斷用：這個 canonical name 底下實際合併了哪些原始 anchor_name（通常
        # 是好幾個不同「序号N」前綴的變體）。如果同一個真人主播還是被拆成兩筆
        # 排行榜列，比對兩筆各自的 raw_anchor_names 就能直接看出是哪裡對不上
        # （例如前綴格式跟 canonical_anchor_name() 的 regex 沒吃到、或名稱本身
        # 有細微差異如結尾表情符號變動）。
        m["raw_anchor_names"] = sorted(m.pop("_raw_names"))
        out.append(m)
    out.sort(key=lambda x: x["total_sec"], reverse=True)
    return out[:limit]


def _stats_total_since(since):
    where = "WHERE end_time IS NOT NULL" + (" AND start_time >= ?" if since else "")
    params = (since.isoformat(timespec="seconds"),) if since else ()
    rows = _db_query(f"SELECT COALESCE(SUM(duration_sec),0) FROM recording_sessions {where}", params)
    return int(rows[0][0]) if rows else 0


def _stats_summary(since=None):
    sessions = _stats_raw_sessions(since)
    total_sec = sum(int(s["duration_sec"] or 0) for s in sessions)
    total_sessions = len(sessions)
    longest_sec = max((int(s["duration_sec"] or 0) for s in sessions), default=0)
    anchors = len({_stats_canonical_anchor(s["anchor_name"]) for s in sessions})
    avg_sec = int(total_sec / total_sessions) if total_sessions else 0
    danmaku_total = sum(_stats_session_danmaku_count(s) for s in sessions)
    return {
        "total_sec": total_sec,
        "total_sessions": total_sessions,
        "avg_session_sec": avg_sec,
        "longest_session_sec": longest_sec,
        "anchors": anchors,
        "today_sec": totals_today(),
        "month_sec": _stats_total_since(_stats_since_for_range("month")),
        "danmaku_count": danmaku_total,
    }


def _stats_anchor_total(name, since=None):
    """`name` is the CANONICAL anchor name (what the ranking table now sends
    to the client) -- match every raw 序号N variant that canonicalizes to it,
    same fix as _stats_ranking."""
    total_sec = 0
    sessions = 0
    for s in _stats_raw_sessions(since):
        if _stats_canonical_anchor(s["anchor_name"]) == name:
            total_sec += int(s["duration_sec"] or 0)
            sessions += 1
    return total_sec, sessions


def _stats_sessions_for(anchor, limit=100):
    rows = _db_query(
        "SELECT id, platform, live_url, quality, start_time, end_time, "
        "duration_sec, file_path, finished_reason, anchor_name "
        "FROM recording_sessions ORDER BY start_time DESC")
    out = []
    for r in rows:
        raw_anchor = r[9]
        if _stats_canonical_anchor(raw_anchor) != anchor:
            continue
        sess = {"anchor_name": raw_anchor, "live_url": r[2],
                "start_time": r[4], "duration_sec": r[6]}
        out.append({
            "id": r[0], "platform": r[1], "live_url": r[2], "quality": r[3],
            "start_time": r[4], "end_time": r[5], "duration_sec": r[6],
            "file_path": r[7], "finished_reason": r[8],
            "danmaku_count": _stats_session_danmaku_count(sess),
        })
        if len(out) >= limit:
            break
    return out


def _stats_parse_range_args(args):
    """Shared `range`/`since` query-arg parsing for the two GET endpoints
    below. `range` in {all,today,month}; a custom `since=YYYY-MM-DD` overrides
    it (unrecognized/blank falls back to 'all' = no cutoff)."""
    range_key = (args.get("range") or "all").strip()
    since_arg = (args.get("since") or "").strip()
    if since_arg:
        try:
            return range_key, datetime.strptime(since_arg, "%Y-%m-%d")
        except ValueError:
            pass
    return range_key, _stats_since_for_range(range_key)


# ---------------------------------------------------------------------------
# Background enforcement
# ---------------------------------------------------------------------------
CHECK_INTERVAL = 15
_last_disk_full_warn = 0.0


def _enforce_limits_once():
    try:
        for sess in active_sessions():
            if not sess["max_session_minutes"]:
                continue
            if sess["elapsed_sec"] >= sess["max_session_minutes"] * 60:
                url = sess["live_url"]
                if set_enabled(url, False, reason="max_session_reached"):
                    mark_disabled_by_limit(url, "max_session_reached")
        data = load_limits()
        state = data.get("_state", {})
        now = datetime.now()
        today = now.date()
        for url, info in list(state.items()):
            try:
                disabled_at = datetime.fromisoformat(info["disabled_at"])
            except Exception:
                clear_disabled_state(url)
                continue
            cooldown_min = (data.get(url) or {}).get("cooldown_minutes")
            if cooldown_min and int(cooldown_min) > 0:
                # Explicit minute-based cooldown overrides daily reset
                if (now - disabled_at).total_seconds() >= int(cooldown_min) * 60:
                    set_enabled(url, True, reason="cooldown_expired")
                    clear_disabled_state(url)
            else:
                # Default: reset on day rollover (new calendar day)
                if disabled_at.date() < today:
                    set_enabled(url, True, reason="daily_reset")
                    clear_disabled_state(url)
    except Exception as e:
        global _last_disk_full_warn
        if _is_disk_full_error(e):
            now_t = time.time()
            if now_t - _last_disk_full_warn > 300:
                print("[limits] paused: disk full / DB unwritable. Free up disk space.")
                _last_disk_full_warn = now_t
        else:
            print(f"[limits] enforcement error: {e}")


def _enforce_loop():
    while True:
        _enforce_limits_once()
        time.sleep(CHECK_INTERVAL)


def start_enforcement_thread():
    t = threading.Thread(target=_enforce_loop, daemon=True, name="limit-enforcer")
    t.start()
    return t


# ---------------------------------------------------------------------------
# Flask App
# ---------------------------------------------------------------------------
app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")


@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/widget")
def widget():
    return send_from_directory(STATIC_DIR, "widget.html")


@app.get("/api/status")
def api_status():
    disk = disk_usage_info()
    disk_full = bool(disk["low"])

    # Filesystem-based reads keep working even when the disk is full, so the
    # streamer list and per-folder usage stay viewable.
    entries = list_entries()
    save_path = get_save_path()
    total_disk = 0
    for e in entries:
        folder = get_anchor_folder(e)
        if folder:
            total_disk += get_folder_size_cached(folder)

    # DB-backed numbers can fail when the disk is full; degrade instead of 500.
    actives, today_sec, grand_sec = [], 0, 0
    db_ok = True
    try:
        actives = active_sessions()
        today_sec = totals_today()
        grand_sec = grand_total()
    except (sqlite3.Error, OSError) as exc:
        if _is_disk_full_error(exc):
            disk_full, db_ok = True, False
        else:
            raise

    try:
        data = load_limits()
    except (OSError, ValueError) as exc:
        if _is_disk_full_error(exc):
            disk_full, data = True, {}
        else:
            raise

    payload = {
        "now": datetime.now().isoformat(timespec="seconds"),
        "root": str(ROOT),
        "url_config_path": str(URL_CONFIG),
        "url_config_exists": URL_CONFIG.exists(),
        "save_path": str(save_path),
        "save_path_exists": save_path.exists(),
        "folder_rules": get_folder_rules(),
        "monitoring": sum(1 for e in entries if e["enabled"]),
        "total_streamers": len(entries),
        "recording_count": len(actives),
        "today_total_sec": today_sec,
        "grand_total_sec": grand_sec,
        "total_disk_bytes": total_disk,
        "active_sessions": actives,
        "disabled_by_limit": data.get("_state", {}),
        # --- disk health (always present) ---
        "disk_full": disk_full,
        "db_ok": db_ok,
        "disk_free_bytes": disk["free_bytes"],
        "disk_total_bytes": disk["total_bytes"],
        "disk_used_bytes": disk["used_bytes"],
    }
    # --- overall system health (disk + recorder + db + suspect recordings) ---
    if _health is not None:
        try:
            payload["health"] = evaluate_current_health()
        except Exception as exc:
            payload["health"] = {"status": "ok", "issues": [],
                                 "error": str(exc)}
    if disk_full:
        payload["message"] = (
            "磁碟空間不足：資料庫可能無法讀寫，錄製與統計數據可能不準確。"
            "請清理磁碟空間後重新啟動錄製器。")
    return jsonify(payload)


@app.get("/api/streamers")
def api_streamers_list():
    disk = disk_usage_info()
    disk_full = bool(disk["low"])
    # DB aggregates may fail on a full disk; degrade to empty so the per-streamer
    # filesystem usage (disk_bytes) still renders and the user can clean up.
    try:
        url_totals = _per_url_totals()
        active_urls = {a["live_url"] for a in active_sessions()}
    except (sqlite3.Error, OSError) as exc:
        if _is_disk_full_error(exc):
            disk_full = True
            url_totals, active_urls = {}, set()
        else:
            raise
    limits_data = load_limits()
    state = limits_data.get("_state", {})
    out = []
    for e in list_entries():
        total_sec, sessions = url_totals.get(e["url"], (0, 0))
        lim = limits_data.get(e["url"]) or {}
        folder = get_anchor_folder(e)
        disk_bytes = get_folder_size_cached(folder) if folder else 0
        out.append({**e,
                    "total_sec": total_sec,
                    "sessions": sessions,
                    "is_recording": e["url"] in active_urls,
                    "max_session_minutes": lim.get("max_session_minutes"),
                    # No default injected: unset -> null -> UI shows "—" (empty).
                    "cooldown_minutes": lim.get("cooldown_minutes"),
                    "priority": bool(lim.get("priority")),
                    "note": lim.get("note") or "",
                    "disabled_by_limit": e["url"] in state,
                    "folder_path": str(folder) if folder else "",
                    "folder_exists": bool(folder and folder.exists()),
                    "disk_bytes": disk_bytes})
    return jsonify({"entries": out, "disk_full": disk_full,
                    "disk_free_bytes": disk["free_bytes"],
                    "disk_total_bytes": disk["total_bytes"]})


@app.post("/api/streamers")
def api_streamers_create():
    body = request.get_json(force=True) or {}
    url = (body.get("url") or "").strip()
    if not re.match(r"https?://", url):
        return jsonify({"error": "URL must start with http:// or https://"}), 400
    new_entry = {
        "enabled": bool(body.get("enabled", True)),
        "quality": body.get("quality") or None,
        "url": url,
        "anchor_name": (body.get("anchor_name") or "").strip(),
    }
    with _url_lock:
        lines = _read_lines()
        for ln in lines:
            p = parse_entry(ln)
            if p and p["url"] == url:
                return jsonify({"error": "URL already exists"}), 409
        lines.append(entry_to_line(new_entry))
        _write_lines(lines)
    if body.get("max_session_minutes"):
        set_limit(url, max_session_minutes=body["max_session_minutes"])
    if body.get("note"):
        set_limit(url, note=body["note"])
    if body.get("priority"):
        set_limit(url, priority=True)
    return jsonify({"ok": True})


@app.put("/api/streamers/<int:line_no>")
def api_streamers_update(line_no):
    body = request.get_json(force=True) or {}
    with _url_lock:
        lines = _read_lines()
        if not (0 <= line_no < len(lines)):
            return jsonify({"error": "line_no out of range"}), 404
        current = parse_entry(lines[line_no])
        if current is None:
            return jsonify({"error": "not a valid streamer line"}), 400
        for k in ("enabled", "quality", "url", "anchor_name"):
            if k in body:
                current[k] = body[k]
        lines[line_no] = entry_to_line(current)
        _write_lines(lines)
        url = current["url"]
    if "max_session_minutes" in body:
        set_limit(url, max_session_minutes=body["max_session_minutes"])
    if "cooldown_minutes" in body:
        set_limit(url, cooldown_minutes=body["cooldown_minutes"])
    if "note" in body:
        set_limit(url, note=body["note"])
    if "priority" in body:
        set_limit(url, priority=bool(body["priority"]))
    return jsonify({"ok": True})


@app.post("/api/streamers/<int:line_no>/toggle")
def api_streamers_toggle(line_no):
    with _url_lock:
        lines = _read_lines()
        if not (0 <= line_no < len(lines)):
            return jsonify({"error": "line_no out of range"}), 404
        e = parse_entry(lines[line_no])
        if e is None:
            return jsonify({"error": "not a valid streamer line"}), 400
        e["enabled"] = not e["enabled"]
        lines[line_no] = entry_to_line(e)
        _write_lines(lines)
        url = e["url"]
    clear_disabled_state(url)
    return jsonify({"ok": True, "enabled": e["enabled"]})


@app.delete("/api/streamers/<int:line_no>")
def api_streamers_delete(line_no):
    with _url_lock:
        lines = _read_lines()
        if not (0 <= line_no < len(lines)):
            return jsonify({"error": "line_no out of range"}), 404
        del lines[line_no]
        _write_lines(lines)
    return jsonify({"ok": True})


@app.post("/api/streamers/batch")
def api_streamers_batch():
    """Batch operations on multiple URLs.

    Body:
        {"urls": ["http://...", ...], "action": "delete" | "enable" | "disable"}
    """
    body = request.get_json(force=True) or {}
    urls = body.get("urls") or []
    action = (body.get("action") or "").lower()
    if not urls or not isinstance(urls, list):
        return jsonify({"error": "urls list is required"}), 400
    if action not in ("delete", "enable", "disable"):
        return jsonify({"error": "action must be delete/enable/disable"}), 400

    url_set = set(urls)
    n_changed = 0
    with _url_lock:
        lines = _read_lines()
        out_lines = []
        for line in lines:
            p = parse_entry(line)
            if p is None or p["url"] not in url_set:
                out_lines.append(line)
                continue
            if action == "delete":
                n_changed += 1
                continue  # skip
            elif action == "enable" and not p["enabled"]:
                p["enabled"] = True
                out_lines.append(entry_to_line(p))
                n_changed += 1
            elif action == "disable" and p["enabled"]:
                p["enabled"] = False
                out_lines.append(entry_to_line(p))
                n_changed += 1
            else:
                out_lines.append(line)
        _write_lines(out_lines)
    # Clear disabled-by-limit state for any URL we just touched
    if action in ("enable", "delete"):
        for u in urls:
            clear_disabled_state(u)
    return jsonify({"ok": True, "changed": n_changed, "action": action})


@app.post("/api/stop")
def api_stop_recording():
    body = request.get_json(force=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400
    cooldown = body.get("cooldown_minutes")
    if cooldown is not None:
        set_limit(url, cooldown_minutes=int(cooldown))
    changed = set_enabled(url, False, reason="manual_stop")
    mark_disabled_by_limit(url, "manual_stop")
    return jsonify({"ok": True, "changed": changed})


@app.get("/api/limits")
def api_limits_all():
    return jsonify(load_limits())


@app.put("/api/limits")
def api_limits_set():
    body = request.get_json(force=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400
    set_limit(url,
              max_session_minutes=body.get("max_session_minutes"),
              cooldown_minutes=body.get("cooldown_minutes"),
              note=body.get("note"),
              priority=body.get("priority"))
    return jsonify({"ok": True})


@app.post("/api/streamers/priority")
def api_streamer_priority():
    """Toggle the 特別關注 (priority / 每分鐘檢測開播) flag for one URL.
    Body: {"url": "...", "priority": true/false}."""
    body = request.get_json(force=True) or {}
    url = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url is required"}), 400
    priority = bool(body.get("priority"))
    set_limit(url, priority=priority)
    return jsonify({"ok": True, "url": url, "priority": priority})


@app.post("/api/limits/clear_cooldown")
def api_limits_clear_cooldown():
    """Clear the 'cooldown_minutes' (達上限後自動恢復時間) for every streamer,
    so they all read as empty/unset again. Other limits/notes are untouched."""
    cleared = 0
    with _limits_lock:
        data = load_limits()
        for key, entry in list(data.items()):
            if key == "_state" or not isinstance(entry, dict):
                continue
            if "cooldown_minutes" in entry:
                entry.pop("cooldown_minutes", None)
                cleared += 1
                # If the entry is now empty, drop it entirely to keep the file tidy.
                if not entry:
                    data.pop(key, None)
        save_limits(data)
    return jsonify({"ok": True, "cleared": cleared})


# ---------------------------------------------------------------------------
# GitHub auto-update endpoints
# ---------------------------------------------------------------------------
# Health + proactive alerts
# ---------------------------------------------------------------------------
@app.get("/api/health")
def api_health():
    if _health is None:
        return jsonify({"status": "ok", "issues": [],
                        "error": "health module not available"})
    try:
        return jsonify(evaluate_current_health())
    except Exception as e:
        return jsonify({"status": "ok", "issues": [], "error": str(e)})


@app.get("/api/alerts")
def api_alerts_get():
    if _alerts is None:
        return jsonify({"error": "alerts module not available"}), 500
    return jsonify(_alerts.load_alerts(ALERTS_FILE))


@app.put("/api/alerts")
def api_alerts_put():
    if _alerts is None:
        return jsonify({"error": "alerts module not available"}), 500
    body = request.get_json(silent=True) or {}
    try:
        _alerts.save_alerts(ALERTS_FILE, body)
    except Exception as e:
        return jsonify({"error": f"could not save: {e}"}), 500
    return jsonify(_alerts.load_alerts(ALERTS_FILE))


@app.post("/api/alerts/test")
def api_alerts_test():
    if _alerts is None:
        return jsonify({"error": "alerts module not available"}), 500
    cfg = _alerts.load_alerts(ALERTS_FILE)
    result = _alerts.send_test_alert(cfg)
    if not result.get("sent"):
        return jsonify({"ok": False,
                        "message": "沒有任何啟用的通知管道送出成功，請檢查設定。",
                        **result}), 200
    return jsonify({"ok": True,
                    "message": f"已送出測試通知：{', '.join(result['sent'])}",
                    **result})


# ---------------------------------------------------------------------------
# Recording start/stop notifications (Discord / webhook). Poll-driven by the
# watchdog loop, so it works without rebuilding the recorder exe.
# ---------------------------------------------------------------------------
@app.get("/api/rec_notify")
def api_rec_notify_get():
    if _rec_notify is None:
        return jsonify({"error": "rec_notify module not available"}), 500
    return jsonify(_rec_notify.get_status(REC_NOTIFY_FILE))


@app.put("/api/rec_notify")
def api_rec_notify_put():
    if _rec_notify is None:
        return jsonify({"error": "rec_notify module not available"}), 500
    body = request.get_json(silent=True) or {}
    try:
        _rec_notify.save_settings(REC_NOTIFY_FILE, body)
    except Exception as e:
        return jsonify({"error": f"could not save: {e}"}), 500
    return jsonify(_rec_notify.get_status(REC_NOTIFY_FILE))


@app.post("/api/rec_notify/test")
def api_rec_notify_test():
    if _rec_notify is None:
        return jsonify({"error": "rec_notify module not available"}), 500
    cfg = _rec_notify.load_settings(REC_NOTIFY_FILE)
    result = _rec_notify.send_test(cfg)
    details = result.get("details", {})
    if not result.get("sent"):
        # surface the real reason (network/proxy/HTTP status) so the user can act
        detail_str = "；".join(f"{k}: {v}" for k, v in details.items()) or "沒有啟用任何管道"
        return jsonify({"ok": False,
                        "message": f"送出失敗 — {detail_str}",
                        **result}), 200
    return jsonify({"ok": True,
                    "message": f"已送出測試通知：{', '.join(result['sent'])}",
                    **result})


@app.post("/api/rec_notify/status")
def api_rec_notify_status():
    """Send a one-off card of the CURRENT recording state (real active sessions)."""
    if _rec_notify is None:
        return jsonify({"error": "rec_notify module not available"}), 500
    cfg = _rec_notify.load_settings(REC_NOTIFY_FILE)
    try:
        actives = active_sessions()
    except Exception:
        actives = []
    result = _rec_notify.send_current_status(cfg, actives)
    details = result.get("details", {})
    n = result.get("count", 0)
    if not result.get("sent"):
        detail_str = "；".join(f"{k}: {v}" for k, v in details.items()) or "沒有啟用任何管道"
        return jsonify({"ok": False, "message": f"送出失敗 — {detail_str}",
                        **result}), 200
    return jsonify({"ok": True,
                    "message": f"已送出目前狀態（{n} 個直播中）：{', '.join(result['sent'])}",
                    **result})


# ---------------------------------------------------------------------------
# Discord 控制頻道機器人 -- 在指定頻道用文字指令新增/移除監控中的主播。跟上面
# 的 rec_notify（單向通知，webhook）是完全不同的東西：這個需要常駐連線監聽
# 頻道訊息，見 src/discord_bot.py 開頭的說明（這是專案唯一破例新增的第三方
# 依賴 discord.py，使用者已明確同意）。
# ---------------------------------------------------------------------------
@app.get("/api/discord_bot")
def api_discord_bot_get():
    if _discord_bot is None:
        return jsonify({"error": "discord_bot module not available"}), 500
    return jsonify({"settings": _discord_bot.load_settings(DISCORD_BOT_FILE),
                    "status": _discord_bot.status()})


@app.put("/api/discord_bot")
def api_discord_bot_put():
    if _discord_bot is None:
        return jsonify({"error": "discord_bot module not available"}), 500
    body = request.get_json(silent=True) or {}
    try:
        _discord_bot.save_settings(DISCORD_BOT_FILE, body)
    except Exception as e:
        return jsonify({"error": f"could not save: {e}"}), 500
    # 套用新設定：先斷開舊連線（如果有在跑）再依新設定決定要不要重新連線。
    # discord.py 的 Gateway 連線一旦建立就得走它自己的 close()，不能直接砍
    # thread，見 discord_bot.stop() 的說明。close() 是排程到舊連線自己的
    # event loop 執行、不是同步完成，這裡等一下讓舊 thread 有機會真的退出，
    # 避免 start() 誤判「還在跑」而跳過重新連線；就算沒等夠，舊連線最終還是
    # 會斷掉，只是使用者要多等幾秒才看到新設定生效。
    # 刻意用 try/except 包住：設定本身已經存檔成功了，連線層的任何意外都不該
    # 讓這個請求回 500、害使用者以為設定沒存到。
    try:
        _discord_bot.stop()
        time.sleep(0.5)
        _start_discord_bot()
    except Exception as e:
        print(f"[discord_bot] 重新連線時發生例外（設定已存檔，不影響）：{type(e).__name__}: {e}")
    return jsonify({"settings": _discord_bot.load_settings(DISCORD_BOT_FILE),
                    "status": _discord_bot.status()})



# ---------------------------------------------------------------------------
def _load_disk_policy():
    cfg = _alerts_settings()
    pol = (cfg.get("disk") or {}) if isinstance(cfg, dict) else {}
    if _diskmgr is not None:
        return _diskmgr.merge_policy(pol)
    return pol


@app.get("/api/config")
def api_config_get():
    """Recorder settings GUI (P1-1, #settings/recording). Whitelisted subset
    of config.ini -- see CONFIG_FIELDS / _config_cookie_keys above."""
    text = _config_read_text()
    if text is None:
        return jsonify({"error": "config.ini not found", "exists": False}), 404
    return jsonify(_config_response_payload(text))


@app.put("/api/config")
def api_config_put():
    """Save whitelisted config.ini fields. config.ini is only read by the
    recorder (DouyinLiveRecorder.exe) at its own startup/reload points, so the
    response always flags needs_recorder_restart when anything actually
    changed -- the web UI itself doesn't need a restart to keep working."""
    text = _config_read_text()
    if text is None:
        return jsonify({"error": "config.ini not found"}), 404
    body = request.get_json(silent=True) or {}
    new_text, changed = _config_apply_updates(text, body)
    if changed:
        try:
            try:
                shutil.copy2(APP_CONFIG, APP_CONFIG.with_suffix(".ini.bak"))
            except Exception:
                pass
            APP_CONFIG.write_text(new_text, encoding="utf-8-sig")
        except Exception as e:
            return jsonify({"error": f"could not save: {e}"}), 500
        invalidate_size_cache()  # save_path may have changed
    return jsonify(_config_response_payload(new_text, changed=changed))


@app.get("/api/disk/policy")
def api_disk_policy_get():
    return jsonify(_load_disk_policy())


@app.put("/api/disk/policy")
def api_disk_policy_put():
    if _alerts is None:
        return jsonify({"error": "alerts module not available"}), 500
    body = request.get_json(silent=True) or {}
    cfg = _alerts.load_alerts(ALERTS_FILE)
    cfg["disk"] = {**(cfg.get("disk") or {}), **body}
    try:
        _alerts.save_alerts(ALERTS_FILE, cfg)
    except Exception as e:
        return jsonify({"error": f"could not save: {e}"}), 500
    return jsonify(_load_disk_policy())


@app.post("/api/disk/cleanup")
def api_disk_cleanup():
    """Plan (and optionally execute) deletion of oldest recordings to free space.
    Body: {"dry_run": true}. Deletion only happens when cleanup_enabled AND
    dry_run is false. Returns the plan either way."""
    if _diskmgr is None:
        return jsonify({"error": "disk_manager module not available"}), 500
    body = request.get_json(silent=True) or {}
    dry_run = body.get("dry_run", True)
    policy = _load_disk_policy()
    disk = disk_usage_info()
    try:
        save_root = str(get_save_path())
        files = _diskmgr.iter_recording_files(save_root)
        active_paths = {a.get("file_path") for a in active_sessions() if a.get("file_path")}
        for f in files:
            f["active"] = f["path"] in active_paths
        plan = _diskmgr.plan_cleanup(disk["free_bytes"], files, policy)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    deleted, errors = [], []
    if not dry_run and plan.get("enabled") and plan.get("delete"):
        for path in plan["delete"]:
            try:
                os.remove(path)
                deleted.append(path)
            except Exception as e:
                errors.append({"path": path, "error": str(e)})
        _size_cache.clear()  # sizes changed
    return jsonify({"dry_run": dry_run, "plan": plan,
                    "deleted": deleted, "errors": errors})


# ---------------------------------------------------------------------------
# Post-recording auto-compression (H.265). Settings in config/compress.json.
# ---------------------------------------------------------------------------
@app.get("/api/compress")
def api_compress_get():
    if _compressor is None:
        return jsonify({"error": "compressor module not available"}), 500
    return jsonify(_compressor.get_status(COMPRESS_FILE, COMPRESS_STATE, ffmpeg_dir=ROOT / "ffmpeg"))


@app.put("/api/compress")
def api_compress_put():
    if _compressor is None:
        return jsonify({"error": "compressor module not available"}), 500
    body = request.get_json(silent=True) or {}
    try:
        _compressor.save_settings(COMPRESS_FILE, body)
    except Exception as e:
        return jsonify({"error": f"could not save: {e}"}), 500
    if body.get("enabled"):
        _compressor.trigger_scan()
    return jsonify(_compressor.get_status(COMPRESS_FILE, COMPRESS_STATE, ffmpeg_dir=ROOT / "ffmpeg"))


@app.post("/api/compress/scan")
def api_compress_scan():
    """Manual "立即開始壓縮" / "立即掃描" trigger. Always forces one full
    scan+compress pass regardless of the `enabled` (auto-compress) setting --
    per UIUX_SPEC §3.6 the switch only controls whether it runs automatically
    in the background; manual triggering must work independently of it."""
    if _compressor is None:
        return jsonify({"error": "compressor module not available"}), 500
    _compressor.trigger_scan(force=True)
    _size_cache.clear()
    _compress_overview_cache["data"] = None  # ratio will shift once this scan finishes
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Compression overview / ratio panel (UIUX_SPEC §3.6 + §6). Scanning every
# video file's size is a full os.walk of the save folder -- same cost class as
# get_folder_size_cached -- so this gets its own 60s TTL cache, independent of
# both the compressor's own per-file skip-cache and _size_cache above (this
# needs total/compressed/pending BYTE splits, not just one folder's total).
# ---------------------------------------------------------------------------
_compress_overview_cache = {"data": None, "at": 0.0}
_COMPRESS_OVERVIEW_TTL = 60


@app.get("/api/compress/overview")
def api_compress_overview():
    if _compressor is None:
        return jsonify({"error": "compressor module not available"}), 500
    now = time.time()
    cached = _compress_overview_cache["data"]
    if cached is not None and now - _compress_overview_cache["at"] < _COMPRESS_OVERVIEW_TTL:
        return jsonify(cached)
    try:
        settings = _compressor.load_settings(COMPRESS_FILE)
    except Exception:
        settings = None
    scan = _compressor.scan_overview(get_save_path(), settings)
    total_bytes = scan["total_bytes"]
    compressed_ratio = (scan["compressed_bytes"] / total_bytes) if total_bytes else 0.0
    # saved_bytes comes from the compressor's own running stats (bytes actually
    # freed by finished jobs), NOT derived from this scan -- the overview only
    # sees CURRENT file sizes, so it has no way to know how big an
    # already-compressed file used to be before it was replaced.
    saved_bytes = 0
    try:
        status = _compressor.get_status(COMPRESS_FILE, COMPRESS_STATE, ffmpeg_dir=ROOT / "ffmpeg")
        saved_bytes = int((status.get("stats") or {}).get("saved_bytes") or 0)
    except Exception:
        pass
    data = {
        "total": scan["total_files"],
        "total_bytes": total_bytes,
        "compressed": scan["compressed_files"],
        "compressed_bytes": scan["compressed_bytes"],
        "pending": scan["pending_files"],
        "pending_bytes": scan["pending_bytes"],
        "compressed_ratio": round(compressed_ratio, 4),
        "saved_bytes": saved_bytes,
        "cached_at": now,
    }
    _compress_overview_cache["data"] = data
    _compress_overview_cache["at"] = now
    return jsonify(data)


@app.post("/api/compress/pause")
def api_compress_pause():
    """Pause/resume the compression queue at runtime. Body: {"paused": bool}.
    Pausing aborts the in-progress job (retried later) without changing settings."""
    if _compressor is None:
        return jsonify({"error": "compressor module not available"}), 500
    body = request.get_json(silent=True) or {}
    _compressor.set_paused(bool(body.get("paused", True)))
    return jsonify({"ok": True, "paused": _compressor.is_paused()})


# ---------------------------------------------------------------------------
# Danmaku (弹幕) query. Storage in config/danmaku.db.
# ---------------------------------------------------------------------------
def _danmaku():
    if _danmaku_store is None:
        return None
    return _danmaku_store.get_store(DANMAKU_DB)


def _int_arg(name, default=None):
    v = request.args.get(name)
    if v is None or v == "":
        return default
    try:
        return int(v)
    except ValueError:
        return default


@app.get("/api/danmaku")
def api_danmaku_query():
    store = _danmaku()
    if store is None:
        return jsonify({"error": "danmaku module not available"}), 500
    limit = _int_arg("limit", 200)
    offset = _int_arg("offset", 0)
    filters = {
        "anchor": request.args.get("anchor") or None,
        "url": request.args.get("url") or None,
        "keyword": request.args.get("q") or None,
        "user": request.args.get("user") or None,
        "since": _int_arg("since"),
        "until": _int_arg("until"),
    }
    rows = store.query(limit=limit, offset=offset, order="desc", **filters)
    total = store.count(**filters)
    try:
        db_exists = DANMAKU_DB.exists()
        db_size = DANMAKU_DB.stat().st_size if db_exists else 0
    except Exception:
        db_exists, db_size = False, 0
    return jsonify({"rows": rows, "total": total,
                    "limit": limit, "offset": offset,
                    "capture": (_danmaku_capture.get_status()
                                if _danmaku_capture else None),
                    "db": {"path": str(DANMAKU_DB), "exists": db_exists,
                           "size_bytes": db_size, "total_rows": store.total()}})


@app.get("/api/danmaku/anchors")
def api_danmaku_anchors():
    store = _danmaku()
    if store is None:
        return jsonify({"error": "danmaku module not available"}), 500
    return jsonify({"anchors": store.anchors(), "total": store.total()})


@app.post("/api/danmaku/capture")
def api_danmaku_capture_toggle():
    """Enable/disable live danmaku capture at runtime. Body: {"enabled": bool}."""
    if _danmaku_capture is None:
        return jsonify({"error": "danmaku_capture module not available"}), 500
    body = request.get_json(silent=True) or {}
    _danmaku_capture.set_enabled(bool(body.get("enabled", True)))
    return jsonify({"ok": True, "status": _danmaku_capture.get_status()})


@app.post("/api/danmaku/purge_garbage")
def api_danmaku_purge_garbage():
    """One-off cleanup for the internalExt-as-content decode bug (fixed in
    danmaku_capture.py -- see AGENT.md): delete any already-stored rows whose
    content is that Douyin internal diagnostic string rather than real chat.
    Safe to call repeatedly; matches on the same fixed needle every time, no
    body/params needed."""
    store = _danmaku()
    if store is None:
        return jsonify({"error": "danmaku module not available"}), 500
    deleted = store.delete_content_matching("internal_src:pushserver")
    return jsonify({"ok": True, "deleted": deleted})


@app.get("/api/danmaku/export")
def api_danmaku_export():
    store = _danmaku()
    if store is None:
        return jsonify({"error": "danmaku module not available"}), 500
    out = ROOT / "logs" / f"danmaku_export_{int(time.time())}.csv"
    filters = {
        "anchor": request.args.get("anchor") or None,
        "url": request.args.get("url") or None,
        "keyword": request.args.get("q") or None,
        "user": request.args.get("user") or None,
        "since": _int_arg("since"),
        "until": _int_arg("until"),
    }
    try:
        p = store.export_csv(out, **filters)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "path": str(p)})


@app.get("/api/danmaku/sessions")
def api_danmaku_sessions():
    """Finished recordings that have a file_path, newest first — the pickable
    list for subtitle generation."""
    try:
        rows = _db_query(
            "SELECT anchor_name, platform, live_url, start_time, end_time, "
            "duration_sec, file_path FROM recording_sessions "
            "WHERE end_time IS NOT NULL AND file_path IS NOT NULL "
            "ORDER BY start_time DESC LIMIT 200")
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    out = []
    for anchor, platform, url, start, end, dur, fpath in rows:
        resolved = None
        try:
            if fpath:
                # 分段錄製時 file_path 存的是 ffmpeg 樣板（如 ..._%03d.ts），
                # 原始檔可能早被轉檔刪除；跟回放頁一樣要用 resolve_video 找
                # 真正存在的檔案，不能直接對 stored path 做 exists()。
                resolved = (_library.resolve_video(fpath, cache=_RESOLVE_VIDEO_CACHE) if _library is not None
                            else (fpath if Path(fpath).exists() else None))
        except Exception:
            resolved = None
        out.append({"anchor_name": anchor, "platform": platform,
                    "live_url": url, "start_time": start, "end_time": end,
                    "duration_sec": dur, "file_path": fpath,
                    "resolved_path": resolved,
                    "file_exists": resolved is not None})
    return jsonify({"sessions": out})


@app.post("/api/danmaku/subtitle")
def api_danmaku_subtitle():
    """Generate sidecar danmaku subtitles (.ass + .srt) for a recording.

    Body (either):
      {"file_path": "...", "start_time": "ISO", "duration_sec": N}
      {"anchor": "...", "start_time": "ISO", "until_time": "ISO",
       "out_path": "optional explicit stem or file"}
    Writes <video-stem>.danmaku.ass/.srt next to the video (or beside out_path).
    """
    body = request.get_json(silent=True) or {}
    result, status = _generate_danmaku_subtitle(
        file_path=(body.get("file_path") or "").strip(),
        start_iso=(body.get("start_time") or "").strip(),
        duration_sec=body.get("duration_sec"),
        anchor=body.get("anchor") or None,
        live_url=body.get("live_url") or None,
        with_user=bool(body.get("with_user")),
        out_path=(body.get("out_path") or "").strip(),
        until_iso=body.get("until_time"))
    return jsonify(result), status


def _generate_danmaku_subtitle(file_path, start_iso, duration_sec, anchor=None,
                               live_url=None, with_user=False, out_path="",
                               until_iso=None):
    """Shared core for /api/danmaku/subtitle and /api/library/clip/<id>/subtitle
    -- both need the exact same query + build + write logic, just sourced from
    a different picker UI. Returns (body_dict, http_status)."""
    if _danmaku_store is None or _danmaku_subtitle is None:
        return {"error": "danmaku modules not available"}, 500
    if not start_iso:
        return {"error": "start_time is required"}, 400
    try:
        start_epoch = int(datetime.fromisoformat(start_iso).timestamp())
    except Exception:
        return {"error": f"bad start_time: {start_iso}"}, 400

    # time window for the query: [start, start+duration] (+small margin)
    since = start_epoch
    until = None
    if duration_sec:
        until = start_epoch + int(duration_sec) + 5
    elif until_iso:
        try:
            until = int(datetime.fromisoformat(until_iso).timestamp())
        except Exception:
            until = None

    # url 優先於 anchor：跟 _clip_has_danmaku() / store.count() 的判斷邏輯一致。
    # 如果兩者都傳、用 AND 篩選，只要 danmaku 表裡存的原始 anchor_name 跟這個
    # session 的 anchor_name 有一絲落差（序号N 前綴、空白、擷取當下抓到的暱稱
    # 跟錄製時不同等），就會把明明存在（url 對得上）的彈幕全部濾掉，回報「這段
    # 時間沒有彈幕紀錄可用」——即使清單頁的「有彈幕」判斷（只靠 url）明明是 true。
    store = _danmaku_store.get_store(DANMAKU_DB)
    rows = store.query(anchor=None if live_url else (anchor or None),
                       url=live_url or None,
                       since=since, until=until, limit=5000, order="asc")
    if not rows:
        return {"ok": False, "message": "這段時間沒有彈幕紀錄可用", "count": 0}, 200

    ass = _danmaku_subtitle.build_ass(rows, start_epoch, duration=duration_sec)
    srt = _danmaku_subtitle.build_srt(rows, start_epoch, duration=duration_sec,
                                      with_user=with_user)

    # decide sidecar target. file_path from a picker UI may still be the raw
    # ffmpeg segment template (e.g. ..._%03d.ts) for 分段錄製 sessions --
    # resolve it to the real file on disk the same way the playback page does,
    # otherwise the sidecar gets written next to a file that doesn't exist.
    target = file_path or out_path
    if not target:
        return {"error": "file_path or out_path is required"}, 400
    if file_path and _library is not None:
        target = _library.resolve_video(file_path, cache=_RESOLVE_VIDEO_CACHE) or target
    ass_path, srt_path = _danmaku_subtitle.sidecar_paths(target)
    written = []
    try:
        Path(ass_path).write_text(ass, encoding="utf-8-sig")
        written.append(ass_path)
        Path(srt_path).write_text(srt, encoding="utf-8-sig")
        written.append(srt_path)
    except Exception as e:
        return {"error": f"write failed: {e}", "written": written}, 500
    return {"ok": True, "count": len(rows),
            "ass_path": ass_path, "srt_path": srt_path}, 200


# ---------------------------------------------------------------------------
# Recording library + date-based continuous player.
# ---------------------------------------------------------------------------
def _finished_sessions_for_library():
    """All finished recordings with a file_path, newest-start first."""
    try:
        rows = _db_query(
            "SELECT anchor_name, platform, live_url, start_time, end_time, "
            "duration_sec, file_path FROM recording_sessions "
            "WHERE end_time IS NOT NULL AND file_path IS NOT NULL "
            "ORDER BY start_time DESC LIMIT 2000")
    except Exception:
        return []
    return [{"anchor_name": a, "platform": p, "live_url": u,
             "start_time": st, "end_time": e, "duration_sec": d,
             "file_path": f}
            for a, p, u, st, e, d, f in rows]


def _clip_id(path):
    import hashlib
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


def _resolve_clip_session(clip_id):
    """Map a clip id back to its source session + real, in-bounds video path.
    Returns {**session, "resolved_path": real} or None if unknown / outside
    the save path (path-traversal guard). Session keys: anchor_name, platform,
    live_url, start_time, end_time, duration_sec, file_path (see
    _finished_sessions_for_library) -- for a 分段錄製 session, start_time/
    duration_sec are overridden to that *specific segment's* estimated window
    (library.estimate_segment_window, the same estimate build_playlist() used
    to list it), not the whole session's -- callers use these two fields to
    build the synced-danmaku time window (/api/library/clip/<id>/danmaku), and
    that has to match the ~30min clip actually being played, not the
    multi-hour session it came from.

    Also carries `session_key` (= the *unresolved* s["file_path"], stable for
    the session's whole lifetime regardless of later renames/compression) and
    the *original*, un-overridden `session_start_time`/`session_duration_sec`
    -- the 回放標記 (playback marker) endpoints need the true whole-session
    window to recompute every segment's epoch range (library.
    estimate_segment_window per index), not just the one segment this
    particular clip_id happens to point at."""
    if _library is None:
        return None
    save_root = str(get_save_path())
    segment_seconds = get_segment_seconds()
    for s in _finished_sessions_for_library():
        stored_path = s.get("file_path", "")
        segments = _library.resolve_all_segments(stored_path, cache=_RESOLVE_SEGMENTS_CACHE)
        if segments:
            for idx, real in segments:
                if _clip_id(real) != clip_id:
                    continue
                if not (_library.is_within(save_root, real) or _library.is_within(str(ROOT), real)):
                    return None
                start_time, duration_sec = _library.estimate_segment_window(
                    s.get("start_time", ""), int(s.get("duration_sec") or 0),
                    idx, segment_seconds)
                sess = {**s, "start_time": start_time, "duration_sec": duration_sec,
                        "session_key": stored_path,
                        "session_start_time": s.get("start_time", ""),
                        "session_duration_sec": int(s.get("duration_sec") or 0)}
                return {**sess, "resolved_path": real}
            continue
        real = _library.resolve_video(stored_path, cache=_RESOLVE_VIDEO_CACHE)
        if real and _clip_id(real) == clip_id:
            if _library.is_within(save_root, real) or _library.is_within(str(ROOT), real):
                return {**s, "resolved_path": real, "session_key": stored_path,
                        "session_start_time": s.get("start_time", ""),
                        "session_duration_sec": int(s.get("duration_sec") or 0)}
            return None
    return None


def _session_clip_windows(session_key, session_start_time, session_duration_sec):
    """[(start_epoch, end_epoch, clip_id), ...] for every currently-resolvable
    clip belonging to one recording session -- the input library.
    locate_epoch_in_windows() needs to map a stored mark's absolute epoch back
    to "which clip_id + what offset". Mirrors build_playlist()'s own
    segmented/non-segmented branching so the windows line up with the exact
    same clip ids the playback page already uses."""
    if _library is None:
        return []
    segment_seconds = get_segment_seconds()
    segments = _library.resolve_all_segments(session_key, cache=_RESOLVE_SEGMENTS_CACHE)
    windows = []
    if segments:
        for idx, real in segments:
            seg_start_iso, seg_duration = _library.estimate_segment_window(
                session_start_time, session_duration_sec, idx, segment_seconds)
            try:
                start_epoch = datetime.fromisoformat(seg_start_iso).timestamp()
            except Exception:
                continue
            windows.append((start_epoch, start_epoch + seg_duration, _clip_id(real)))
        return windows
    real = _library.resolve_video(session_key, cache=_RESOLVE_VIDEO_CACHE)
    if not real:
        return []
    try:
        start_epoch = datetime.fromisoformat(session_start_time or "").timestamp()
    except Exception:
        return []
    windows.append((start_epoch, start_epoch + session_duration_sec, _clip_id(real)))
    return windows


def _resolve_clip_id(clip_id):
    """Map a clip id back to its real, in-bounds video path. Returns None if
    unknown or outside the save path (path-traversal guard)."""
    sess = _resolve_clip_session(clip_id)
    return sess["resolved_path"] if sess else None


def _clip_danmaku_offsets(rows, start_epoch, duration=None):
    """Attach offset_sec (seconds into the clip) to each danmaku row and drop
    anything outside [0, duration+margin]. Pure/testable.

    Reuses the exact same "彈幕 ts − 錄影 start = 影片秒數" rule that
    danmaku_subtitle.py's _offsets() already implements for ASS/SRT
    generation — this is the read-only, JSON-friendly sibling of that
    helper (keeps id/ts instead of building subtitle cues), not a rewrite of
    the alignment logic itself.
    """
    out = []
    for r in rows or []:
        try:
            offset = int(r["ts"]) - int(start_epoch)
        except Exception:
            continue
        if offset < 0:
            continue
        if duration is not None:
            try:
                if offset > float(duration) + 5:
                    continue
            except (TypeError, ValueError):
                pass
        out.append({
            "id": r.get("id"),
            "ts": int(r["ts"]),
            "offset_sec": offset,
            "user": r.get("user") or "",
            "content": r.get("content") or "",
        })
    out.sort(key=lambda x: (x["offset_sec"], x["id"] or 0))
    return out


@app.get("/api/stats/overview")
def api_stats_overview():
    range_key, since = _stats_parse_range_args(request.args)
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    return jsonify({
        "range": range_key,
        "since": since.isoformat() if since else None,
        "summary": _stats_summary(since=since),
        "ranking": _stats_ranking(since=since, limit=limit),
    })


@app.get("/api/stats/anchor")
def api_stats_anchor():
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"error": "missing name"}), 400
    try:
        limit = int(request.args.get("limit", 100))
    except ValueError:
        limit = 100
    _range_key, since = _stats_parse_range_args(request.args)
    total_sec, session_count = _stats_anchor_total(name, since=since)
    return jsonify({
        "anchor_name": name,
        "total_sec": total_sec,
        "session_count": session_count,
        "sessions": _stats_sessions_for(name, limit=limit),
    })


@app.get("/api/stats/export")
def api_stats_export():
    """CSV download of the recording history (same columns/order as
    duration_tracker.DurationTracker.export_csv / query_duration.py --export),
    filtered by the same range/since as /api/stats/overview."""
    _range_key, since = _stats_parse_range_args(request.args)
    where = "WHERE end_time IS NOT NULL" + (" AND start_time >= ?" if since else "")
    params = (since.isoformat(timespec="seconds"),) if since else ()
    rows = _db_query(
        "SELECT anchor_name, platform, live_url, quality, start_time, end_time, "
        f"duration_sec, file_path, finished_reason FROM recording_sessions {where} "
        "ORDER BY start_time DESC", params)
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["主播", "平台", "直播間URL", "畫質", "開始", "結束",
                "時長(秒)", "時長(h:m:s)", "檔案", "結束原因"])
    for anchor, platform, url, quality, start, end, dur, fpath, reason in rows:
        dur = dur or 0
        dur_hms = _common.fmt_duration(dur) if _common else str(timedelta(seconds=dur))
        w.writerow([anchor, platform, url, quality, start, end,
                    dur, dur_hms, fpath, reason])
    payload = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so Excel opens 中文 correctly
    from flask import Response
    resp = Response(payload, mimetype="text/csv")
    resp.headers["Content-Disposition"] = f'attachment; filename="stats_export_{int(time.time())}.csv"'
    return resp


def _sessions_for_anchor(anchor):
    """_finished_sessions_for_library(), optionally filtered to one anchor.
    `anchor` should be the exact (canonical, prefix-stripped) anchor_name
    string as returned by /api/library/anchors -- compare against each
    session's canonicalized name too (main.py's "序号N " prefix must be
    stripped on both sides or the same real streamer never matches once N
    drifts between recordings; see library.canonical_anchor_name)."""
    sessions = _finished_sessions_for_library()
    if not anchor:
        return sessions
    return [s for s in sessions
            if (_library.canonical_anchor_name(s.get("anchor_name")) or "(未知主播)") == anchor]


def _clip_has_danmaku(item):
    """Whether any danmaku was captured during this clip's time range -- shown
    as a column in the day's clip list (使用者需求：標註是否有彈幕). Cheap,
    indexed COUNT query per clip (idx_dm_url / idx_dm_ts); best-effort like the
    rest of the danmaku feature -- any failure just means "no" rather than a
    500 for the whole clip list."""
    if _danmaku_store is None:
        return False
    try:
        start_epoch = int(datetime.fromisoformat(item.get("start_time") or "").timestamp())
    except Exception:
        return False
    duration = item.get("duration_sec")
    until = start_epoch + int(duration) + 5 if duration else None
    live_url = item.get("live_url") or None
    anchor = item.get("anchor_name") or None
    try:
        store = _danmaku_store.get_store(DANMAKU_DB)
        return store.count(url=live_url, anchor=None if live_url else anchor,
                           since=start_epoch, until=until) > 0
    except Exception:
        return False


@app.get("/api/library/anchors")
def api_library_anchors():
    if _library is None:
        return jsonify({"error": "library module not available"}), 500
    return jsonify({"anchors": _library.list_anchors(_finished_sessions_for_library())})


@app.get("/api/library/dates")
def api_library_dates():
    if _library is None:
        return jsonify({"error": "library module not available"}), 500
    anchor = (request.args.get("anchor") or "").strip()
    return jsonify({"dates": _library.group_by_date(_sessions_for_anchor(anchor),
                                                     segments_cache=_RESOLVE_SEGMENTS_CACHE)})


@app.get("/api/library/day")
def api_library_day():
    if _library is None:
        return jsonify({"error": "library module not available"}), 500
    date = (request.args.get("date") or "").strip()
    if not date:
        return jsonify({"error": "date is required (YYYY-MM-DD)"}), 400
    anchor = (request.args.get("anchor") or "").strip()
    sessions = _sessions_for_anchor(anchor)
    playlist = _library.build_playlist(sessions, date, id_for=_clip_id,
                                       segment_seconds=get_segment_seconds(),
                                       segments_cache=_RESOLVE_SEGMENTS_CACHE,
                                       video_cache=_RESOLVE_VIDEO_CACHE)
    for it in playlist:
        it["has_danmaku"] = _clip_has_danmaku(it)
    # strip absolute path / live_url from the client payload (internal-only,
    # already consumed above for has_danmaku); keep filename + id only
    out = [{k: v for k, v in it.items() if k not in ("path", "live_url")} for it in playlist]
    resp = {"date": date, "clips": out}
    if not out:
        # 診斷用：這天在資料庫裡有紀錄、但一段都放不出來——通常是 DB 存的 file_path
        # 跟磁碟上實際檔案對不上（改過路徑/砍檔/轉檔失敗等）。只在「查得到紀錄卻放不出
        # 影片」這種異常狀況才附上，正常情況（真的沒有紀錄）不會多這個欄位。
        dbg = []
        for s in sessions:
            try:
                dt = datetime.fromisoformat(s.get("start_time") or "")
            except Exception:
                continue
            if dt.strftime("%Y-%m-%d") != date:
                continue
            fp = s.get("file_path") or ""
            dbg.append({
                "anchor_name": s.get("anchor_name"),
                "start_time": s.get("start_time"),
                "stored_file_path": fp,
                "stored_path_exists": bool(fp) and Path(fp).exists(),
                "resolved": _library.resolve_video(fp, cache=_RESOLVE_VIDEO_CACHE) is not None,
            })
        if dbg:
            resp["debug"] = dbg
    return jsonify(resp)


@app.get("/api/library/video/<clip_id>")
def api_library_video(clip_id):
    real = _resolve_clip_id(clip_id)
    if not real or not Path(real).exists():
        abort(404)
    # conditional=True enables HTTP Range -> seeking + gapless <video> playback
    return send_file(real, conditional=True)


def _srt_to_vtt(srt_text):
    """Convert SRT to WebVTT (what HTML5 <track> needs). Commas -> dots in
    timestamps; prepend WEBVTT header; drop numeric index lines."""
    import re
    lines = ["WEBVTT", ""]
    for block in re.split(r"\r?\n\r?\n", srt_text.strip()):
        rows = block.splitlines()
        if not rows:
            continue
        if rows[0].strip().isdigit():
            rows = rows[1:]
        if not rows:
            continue
        rows[0] = rows[0].replace(",", ".")
        lines.append("\n".join(rows))
        lines.append("")
    return "\n".join(lines)


@app.get("/api/library/subtitle/<clip_id>")
def api_library_subtitle(clip_id):
    """Serve the danmaku sidecar subtitle for a clip as WebVTT (for <track>)."""
    if _library is None:
        abort(404)
    real = _resolve_clip_id(clip_id)
    if not real:
        abort(404)
    subs = _library.find_sidecar_subtitles(real)
    srt = subs.get("srt")
    if not srt or not Path(srt).exists():
        abort(404)
    try:
        text = Path(srt).read_text(encoding="utf-8-sig")
    except Exception:
        abort(404)
    from flask import Response
    return Response(_srt_to_vtt(text), mimetype="text/vtt")


@app.get("/api/library/clip/<clip_id>/danmaku")
def api_library_clip_danmaku(clip_id):
    """Danmaku for one playback clip, each row carrying offset_sec (seconds
    into the video) for the player's synced sidebar (UIUX_SPEC §3.4/§6)."""
    if _library is None:
        return jsonify({"error": "library module not available"}), 500
    if _danmaku_store is None:
        return jsonify({"error": "danmaku module not available"}), 500
    sess = _resolve_clip_session(clip_id)
    if not sess:
        abort(404)
    start_iso = sess.get("start_time") or ""
    try:
        start_epoch = int(datetime.fromisoformat(start_iso).timestamp())
    except Exception:
        return jsonify({"error": f"bad start_time: {start_iso}"}), 400
    duration = sess.get("duration_sec")
    since = start_epoch
    until = start_epoch + int(duration) + 5 if duration else None

    live_url = sess.get("live_url") or None
    anchor = sess.get("anchor_name") or None
    store = _danmaku_store.get_store(DANMAKU_DB)
    # prefer the room URL (stable capture key even if the anchor is renamed);
    # fall back to anchor name when a session has no live_url on record.
    rows = store.query(url=live_url, anchor=None if live_url else anchor,
                       since=since, until=until, limit=5000, order="asc")
    items = _clip_danmaku_offsets(rows, start_epoch, duration)
    return jsonify({"clip_id": clip_id, "start_epoch": start_epoch,
                    "duration_sec": duration, "count": len(items),
                    "danmaku": items})


@app.post("/api/library/clip/<clip_id>/subtitle")
def api_library_clip_subtitle(clip_id):
    """Generate the danmaku sidecar subtitle (.ass + .srt) for one playback
    clip directly -- the one-click '產生字幕' button next to a clip that has
    danmaku but no subtitle yet (回放頁 + 彈幕轉字幕 picker). Resolves the clip
    server-side (same as /danmaku above) so the frontend never needs the real
    file path or live_url. Body: {"with_user": bool}."""
    if _library is None:
        return jsonify({"error": "library module not available"}), 500
    sess = _resolve_clip_session(clip_id)
    if not sess:
        abort(404)
    body = request.get_json(silent=True) or {}
    result, status = _generate_danmaku_subtitle(
        file_path=sess.get("resolved_path") or "",
        start_iso=sess.get("start_time") or "",
        duration_sec=sess.get("duration_sec"),
        anchor=sess.get("anchor_name") or None,
        live_url=sess.get("live_url") or None,
        with_user=bool(body.get("with_user")))
    return jsonify(result), status


# ---------------------------------------------------------------------------
# 回放標記 (playback markers) -- 使用者需求：在目前播放位置加一個標記（單一時間
# 點或一個時間段）+ 備註，之後點一下就能直接跳到那個時間點；可以跨影片，但限
# 同一場分段錄製 session 內的相鄰片段（本來就是連續時間軸，只是被切成多個檔
# 案）。存放/命名慣例見 src/marks_store.py 開頭的說明。
# ---------------------------------------------------------------------------
@app.get("/api/library/clip/<clip_id>/marks")
def api_library_clip_marks(clip_id):
    """All marks belonging to the *whole session* this clip is part of (not
    just this one segment) -- a 分段錄製 session's marks may point at a
    different (usually adjacent) segment, and the frontend needs clip_id +
    local_offset_sec for each to know where to jump. Marks are looked up by
    the session's stable file_path key, then re-mapped to *currently
    resolvable* clip ids/offsets on every read (see library.
    locate_epoch_in_windows) -- so a rename/compression after the mark was
    made doesn't orphan it, as long as the session's segments are still
    resolvable at all."""
    if _library is None:
        return jsonify({"error": "library module not available"}), 500
    if _marks_store is None:
        return jsonify({"error": "marks module not available"}), 500
    sess = _resolve_clip_session(clip_id)
    if not sess:
        abort(404)
    session_key = sess.get("session_key") or ""
    if not session_key:
        return jsonify({"marks": []})
    windows = _session_clip_windows(session_key, sess.get("session_start_time") or "",
                                    sess.get("session_duration_sec") or 0)
    store = _marks_store.get_store(MARKS_DB)
    out = []
    for r in store.list_for_session(session_key):
        loc = _library.locate_epoch_in_windows(windows, r["start_epoch"])
        if not loc:
            continue  # session currently unresolvable at all -- skip, not 500
        mark_clip_id, local_offset = loc
        item = {
            "id": r["id"], "note": r["note"] or "",
            "start_epoch": r["start_epoch"], "end_epoch": r["end_epoch"],
            "clip_id": mark_clip_id, "local_offset_sec": round(local_offset, 2),
        }
        if r["end_epoch"] is not None:
            end_loc = _library.locate_epoch_in_windows(windows, r["end_epoch"])
            if end_loc:
                end_clip_id, end_local_offset = end_loc
                item["end_clip_id"] = end_clip_id
                item["end_local_offset_sec"] = round(end_local_offset, 2)
                item["duration_sec"] = round(r["end_epoch"] - r["start_epoch"], 2)
                item["spans_clips"] = end_clip_id != mark_clip_id
        out.append(item)
    out.sort(key=lambda x: x["start_epoch"])
    return jsonify({"marks": out})


@app.post("/api/library/clip/<clip_id>/marks")
def api_library_clip_marks_add(clip_id):
    """Add one mark. Body: {"start_offset_sec": number (seconds into *this*
    clip), "end_offset_sec": number|null, "note": str}. Converted to absolute
    epoch server-side (this clip's resolved start_time + offset) before
    storage -- see src/marks_store.py for why marks are epoch-based rather
    than clip-relative."""
    if _library is None:
        return jsonify({"error": "library module not available"}), 500
    if _marks_store is None:
        return jsonify({"error": "marks module not available"}), 500
    sess = _resolve_clip_session(clip_id)
    if not sess:
        abort(404)
    body = request.get_json(silent=True) or {}
    try:
        start_offset = float(body.get("start_offset_sec"))
    except (TypeError, ValueError):
        return jsonify({"error": "start_offset_sec is required (number)"}), 400
    if start_offset < 0:
        return jsonify({"error": "start_offset_sec must be >= 0"}), 400
    end_offset_raw = body.get("end_offset_sec")
    end_offset = None
    if end_offset_raw not in (None, ""):
        try:
            end_offset = float(end_offset_raw)
        except (TypeError, ValueError):
            return jsonify({"error": "end_offset_sec must be a number"}), 400
        if end_offset <= start_offset:
            return jsonify({"error": "end_offset_sec must be greater than start_offset_sec"}), 400
    note = (body.get("note") or "").strip()
    try:
        clip_start_epoch = datetime.fromisoformat(sess.get("start_time") or "").timestamp()
    except Exception:
        return jsonify({"error": f"bad clip start_time: {sess.get('start_time')!r}"}), 400
    session_key = sess.get("session_key") or ""
    if not session_key:
        return jsonify({"error": "session has no stable key"}), 500
    start_epoch = clip_start_epoch + start_offset
    end_epoch = clip_start_epoch + end_offset if end_offset is not None else None
    store = _marks_store.get_store(MARKS_DB)
    try:
        row = store.add(session_key, start_epoch, end_epoch, note)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(row), 201


@app.delete("/api/library/marks/<int:mark_id>")
def api_library_marks_delete(mark_id):
    if _marks_store is None:
        return jsonify({"error": "marks module not available"}), 500
    store = _marks_store.get_store(MARKS_DB)
    if not store.delete(mark_id):
        abort(404)
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
@app.get("/api/update/status")
def api_update_status():
    if _updater is None:
        return jsonify({
            "error": "update_checker module not available",
            "import_error": _updater_error,
            "hint": "Run start_widget.bat to install missing deps (requests).",
        }), 500
    return jsonify(_updater.load_state())


@app.post("/api/update/check")
def api_update_check():
    if _updater is None:
        return jsonify({"error": "update_checker module not available"}), 500
    return jsonify(_updater.check_for_update())


@app.post("/api/update/apply")
def api_update_apply():
    if _updater is None:
        return jsonify({"error": "update_checker module not available"}), 500
    body = request.get_json(silent=True) or {}
    return jsonify(_updater.apply_update(body.get("sha")))


# ---------------------------------------------------------------------------
# Open folder in Explorer (Windows) / fallback for other OS
# ---------------------------------------------------------------------------
@app.post("/api/folder/open")
def api_folder_open():
    body = request.get_json(force=True) or {}
    url = (body.get("url") or "").strip()
    path_str = (body.get("path") or "").strip()

    target = None
    if path_str:
        target = Path(path_str)
    elif url:
        for e in list_entries():
            if e["url"] == url:
                target = get_anchor_folder(e)
                break
    if not target:
        return jsonify({"error": "path or url is required"}), 400
    target = Path(target)
    if not target.exists():
        # Try to create it so Explorer doesn't error
        try:
            target.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return jsonify({"error": f"folder not found: {target} ({e})"}), 404

    try:
        if os.name == "nt":
            os.startfile(str(target))
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, "path": str(target)})


# ---------------------------------------------------------------------------
# Restart the recorder process (forces an immediate live-status re-scan)
# ---------------------------------------------------------------------------
@app.post("/api/recorder/restart")
def api_recorder_restart():
    """Force an immediate re-scan of all streamers by killing and respawning
    DouyinLiveRecorder.exe.  This bypasses the recorder's 300-sec poll loop
    because a fresh start scans every URL_config entry immediately.
    """
    if os.name != "nt":
        return jsonify({"error": "Only supported on Windows"}), 400
    import subprocess
    exe = ROOT / "DouyinLiveRecorder.exe"
    if not exe.exists():
        return jsonify({"error": f"recorder exe not found at {exe}"}), 404

    result = {"steps": [], "ok": True}
    try:
        # Step 1: hard-kill any running instance
        was_running = False
        kill = subprocess.run(["taskkill", "/f", "/im", "DouyinLiveRecorder.exe"],
                              capture_output=True, timeout=10, text=True, **_hidden_kw())
        if kill.returncode == 0:
            was_running = True
            result["steps"].append("killed previous recorder")
        else:
            result["steps"].append("recorder was not running")

        # Step 2: wait until the process is really gone (taskkill is async)
        for _ in range(20):  # up to 4 seconds
            check = subprocess.run(["tasklist", "/fi", "imagename eq DouyinLiveRecorder.exe", "/nh"],
                                   capture_output=True, timeout=5, text=True, **_hidden_kw())
            if "DouyinLiveRecorder.exe" not in check.stdout:
                break
            time.sleep(0.2)
        result["steps"].append("confirmed previous instance is gone")

        # NOTE: URLs disabled by the limit system (cooldown / max_session_reached)
        # are intentionally left alone — they stay paused. The restart only forces
        # a fresh scan of the currently-enabled URLs.

        # Step 3: spawn fresh recorder
        # CREATE_NEW_CONSOLE → recorder gets its own console window (it's a
        # console app and needs one to function).
        # DETACHED + NEW_GROUP would make it invisible AND console-less,
        # which makes the recorder exit immediately on startup.
        CREATE_NEW_CONSOLE = 0x00000010
        NEW_GROUP = 0x00000200
        proc = subprocess.Popen(
            [str(exe)], cwd=str(ROOT),
            creationflags=CREATE_NEW_CONSOLE | NEW_GROUP, close_fds=True)
        result["steps"].append(f"spawned new recorder (pid {proc.pid})")
        result["pid"] = proc.pid
        result["was_running"] = was_running

        # Step 4: verify the new process is alive
        time.sleep(1.0)
        if proc.poll() is not None:
            result["ok"] = False
            result["error"] = (f"recorder exited immediately, "
                               f"returncode={proc.returncode}")
            return jsonify(result), 500

    except Exception as e:
        return jsonify({"error": str(e), "steps": result.get("steps", [])}), 500
    return jsonify(result)


class _Tee:
    """Write to two streams at once (real console + log file)."""
    def __init__(self, a, b):
        self._a, self._b = a, b

    def write(self, s):
        for st in (self._a, self._b):
            try:
                st.write(s)
            except Exception:
                pass

    def flush(self):
        for st in (self._a, self._b):
            try:
                st.flush()
            except Exception:
                pass


def _setup_console_logfile(keep=5):
    """Tee stdout/stderr into logs/web_ui.log, rotating older copies (keep N).
    Best-effort: any failure leaves the console untouched."""
    try:
        logdir = ROOT / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        logfile = logdir / "web_ui.log"
        # Rotate previous logs: web_ui.log -> web_ui.log.1 -> ... up to keep.
        if logfile.exists():
            for i in range(keep - 1, 0, -1):
                older = logdir / f"web_ui.log.{i}"
                newer = logdir / (f"web_ui.log.{i-1}" if i > 1 else "web_ui.log")
                if newer.exists():
                    try:
                        if older.exists():
                            older.unlink()
                        newer.rename(older)
                    except Exception:
                        pass
        f = open(logfile, "a", encoding="utf-8", buffering=1)
        f.write(f"\n===== web_ui start {datetime.now().isoformat(timespec='seconds')} =====\n")
        sys.stdout = _Tee(sys.__stdout__, f)
        sys.stderr = _Tee(sys.__stderr__, f)
    except Exception as e:
        print(f"[web_ui] could not set up log file: {e}")


_dm_roomid_cache = {}   # web_rid -> (room_id, ts)


def _resolve_douyin_room_id(url):
    """Fetch the numeric Douyin room id for the IM websocket. Cached 5 min.
    Uses the recorder's own spider so signing stays consistent. Returns None on
    non-Douyin or failure."""
    if "douyin.com" not in url:
        return None
    now = time.time()
    hit = _dm_roomid_cache.get(url)
    if hit and now - hit[1] < 300:
        return hit[0]
    rid = None
    try:
        import asyncio
        from src import spider
        # read cookie from config.ini if present
        cookie = ""
        try:
            txt = APP_CONFIG.read_text(encoding="utf-8-sig")
            m = re.search(r"^抖音cookie\s*=\s*(.*)$", txt, re.MULTILINE)
            if m:
                cookie = m.group(1).strip()
        except Exception:
            pass
        data = asyncio.run(spider.get_douyin_web_stream_data(url=url, cookies=cookie))
        rid = data.get("id_str") or data.get("room_id") or None
        if rid:
            rid = str(rid)
    except Exception:
        rid = None
    _dm_roomid_cache[url] = (rid, now)
    return rid


def _active_douyin_streamers():
    """[{"url","anchor_name"}] for currently-recording Douyin streamers."""
    out = []
    try:
        for s in active_sessions():
            url = s.get("live_url") or ""
            if "douyin.com" in url:
                out.append({"url": url, "anchor_name": s.get("anchor_name", "")})
    except Exception:
        pass
    return out


def _start_danmaku_coordinator():
    store = _danmaku_store.get_store(DANMAKU_DB)

    def _cookies():
        try:
            txt = APP_CONFIG.read_text(encoding="utf-8-sig")
            m = re.search(r"^抖音cookie\s*=\s*(.*)$", txt, re.MULTILINE)
            return m.group(1).strip() if m else ""
        except Exception:
            return ""

    _danmaku_capture.start_coordinator(
        get_active_douyin=_active_douyin_streamers,
        store=store, get_cookies=_cookies,
        resolve_room_id=_resolve_douyin_room_id, poll_seconds=30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--root",
                    help="DouyinLiveRecorder install dir. Default: auto-detect.")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()
    _apply_root(_resolve_root(args.root))
    if not STATIC_DIR.exists():
        STATIC_DIR.mkdir(parents=True, exist_ok=True)

    # P0-5: keep a rotating log of console output so past failures survive a
    # restart (previously each launch's output vanished).
    _setup_console_logfile()

    # P0-4: if the requested port is taken, fall back to the next free one and
    # record the choice so the widget/floating window can find us.
    port = args.port
    if _netutil is not None:
        port = _netutil.pick_port(args.host, args.port)
        _netutil.write_port_file(PORT_FILE, port)
        if port != args.port:
            print(f"  [port] {args.port} busy -> using {port}")

    print("=" * 60)
    print("  DouyinLiveRecorder Web Console")
    print(f"  ROOT          : {ROOT}")
    print(f"  URL_config.ini: {URL_CONFIG} "
          f"({'OK' if URL_CONFIG.exists() else 'MISSING'})")
    print(f"  recording.db  : {DB_FILE} "
          f"({'OK' if DB_FILE.exists() else 'not yet'})")
    print(f"  limits.json   : {LIMITS_FILE} "
          f"({'OK' if LIMITS_FILE.exists() else 'will be created'})")
    print(f"  save path     : {get_save_path()}")
    print(f"  Listening     : http://{args.host}:{port}/")
    print("=" * 60)
    start_enforcement_thread()
    if start_watchdog_thread() is not None:
        print("  health watch : every 60s (alerts via config/alerts.json)")
    if _compressor is not None:
        try:
            _compressor.start_worker(get_save_path, COMPRESS_FILE,
                                     COMPRESS_STATE, ffmpeg_dir=ROOT / "ffmpeg")
            _cs = _compressor.load_settings(COMPRESS_FILE)
            print(f"  auto compress: {'ON' if _cs.get('enabled') else 'off'} "
                  f"(config/compress.json, H.265 crf {_cs.get('crf')})")
        except Exception as e:
            print(f"  auto compress: disabled ({e})")
    if _danmaku_capture is not None and _danmaku_store is not None:
        try:
            _start_danmaku_coordinator()
            print("  danmaku      : capture ON by default (toggle in 彈幕 tab; best-effort)")
        except Exception as e:
            print(f"  danmaku      : disabled ({e})")
    if _updater is not None:
        try:
            _updater.start_background_checker(interval_hours=6.0, auto_apply=True)
            print("  update check : every 6h, auto-apply spider.py")
        except Exception as e:
            print(f"  update check : disabled ({e})")
    if _discord_bot is not None:
        try:
            if _start_discord_bot():
                print("  discord bot  : starting (見「設定→通知」頁狀態；需要背景連線建立時間)")
            else:
                print(f"  discord bot  : off ({_discord_bot.status().get('last_error') or '未啟用'})")
        except Exception as e:
            print(f"  discord bot  : disabled ({e})")
    app.run(host=args.host, port=port, debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
