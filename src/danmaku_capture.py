"""
danmaku_capture.py - Best-effort Douyin live-chat (弹幕) capture.

⚠️  IMPORTANT / HONEST CAVEAT
    Douyin's danmaku travels over a proprietary WebSocket + protobuf protocol
    that is reverse-engineered and changes periodically. This module implements
    a WebSocket client and a *minimal* protobuf reader using only the Python
    standard library (socket / ssl / struct / zlib / gzip) — NO extra deps.
    It was written carefully but could NOT be verified against live Douyin in
    the build environment. Treat it as best-effort: if Douyin changes the
    protocol or signing, capture may stop working and need adjustment. The
    query/store/UI layers are independent and fully reliable regardless.

Design (mirrors compressor/watchdog): hosted by web_ui.py. A coordinator polls
active recording sessions; for each live Douyin streamer it opens one capture
thread that connects to the IM websocket and writes decoded chat lines into the
DanmakuStore. Threads are torn down when the streamer goes offline.

Only Douyin is implemented (all current streamers are Douyin). Other platforms
are ignored gracefully.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import random
import re
import ssl
import struct
import threading
import time
import urllib.parse
from base64 import b64encode
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_coordinator: Optional[threading.Thread] = None
_workers: Dict[str, "DouyinDanmakuClient"] = {}   # url -> client
_enabled = True   # default on: capture starts automatically, no manual toggle needed
_last_error: Optional[str] = None
_last_close: Optional[Dict[str, Any]] = None
_stats = {"connected": 0, "messages": 0}


def is_enabled() -> bool:
    return _enabled


def set_enabled(on: bool) -> None:
    global _enabled
    _enabled = bool(on)


def get_status() -> Dict[str, Any]:
    with _lock:
        active = [{"url": u, "anchor": c.anchor_name,
                   "connected": c.connected, "messages": c.msg_count}
                  for u, c in _workers.items()]
    return {
        "enabled": _enabled,
        "running": _coordinator is not None and _coordinator.is_alive(),
        "active_connections": [a for a in active if a["connected"]],
        "workers": active,
        "last_error": _last_error,
        "last_close": _last_close,
        "total_messages": _stats["messages"],
    }


# ---------------------------------------------------------------------------
# Minimal protobuf reader (enough to walk Douyin's frames without a .proto)
# ---------------------------------------------------------------------------
def _read_varint(buf: bytes, pos: int):
    """Return (value, new_pos). Standard protobuf base-128 varint."""
    result = 0
    shift = 0
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return result, pos


def _iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for a protobuf message.
    value is int (varint/fixed) or bytes (length-delimited)."""
    pos = 0
    n = len(buf)
    while pos < n:
        key, pos = _read_varint(buf, pos)
        field = key >> 3
        wire = key & 0x07
        if wire == 0:      # varint
            val, pos = _read_varint(buf, pos)
            yield field, wire, val
        elif wire == 2:    # length-delimited (bytes/string/submessage)
            length, pos = _read_varint(buf, pos)
            val = buf[pos:pos + length]
            pos += length
            yield field, wire, val
        elif wire == 5:    # 32-bit
            val = buf[pos:pos + 4]; pos += 4
            yield field, wire, val
        elif wire == 1:    # 64-bit
            val = buf[pos:pos + 8]; pos += 8
            yield field, wire, val
        else:
            break  # unknown wire type -> stop (frame likely changed)


def _as_text(b: bytes) -> Optional[str]:
    try:
        s = b.decode("utf-8")
    except Exception:
        return None
    # keep only strings that look like real text (printable-ish)
    if not s or not s.strip():
        return None
    return s


# Douyin webcast protobuf field layout (from the widely-published community
# reverse-engineering of this protocol -- e.g. the schema vendored by
# saermart/DouyinLiveWebFetcher and the many forks of it):
#   PushFrame: field 7 = payloadType(str), field 8 = payload(bytes, possibly gzip)
#   Response:  field 1 = messagesList (repeated Message), field 5 = internalExt
#              (a diagnostic string like "internal_src:pushserver|first_req_ms:
#              ...|seq:...|wss_msg_type:...|wrds_v:..." -- NOT chat content)
#   Message:   field 1 = method(str), field 2 = payload(bytes)
#   ChatMessage: field 1 = Common(sub-msg), field 2 = User(sub-msg), field 3 =
#              content(str)
#   User:      field 3 = nickName(str)
#
# BUG FIXED: this used to read ChatMessage field 5 as content and field 3 as
# the User submessage -- both wrong (swapped/off from the real schema above).
# There was also a fallback that re-parsed the *entire* Response payload as if
# it were a single ChatMessage whenever the messagesList walk found nothing.
# Because Response.internalExt really is field 5, and the old code treated
# field 5 as "content", that fallback was reliably extracting internalExt's
# diagnostic string and storing it as fake chat content -- which is exactly
# the "internal_src:pushserver|..." garbage users were seeing in captured
# danmaku. Fixed by correcting the field numbers and removing the unsafe
# fallback entirely: if the structure doesn't match, return no messages
# (best-effort -> silently miss data, never fabricate wrong data).
_JUNK_CONTENT_RE = re.compile(r"internal_src:|wss_msg_type:|first_req_ms:\d")


def _decode_chat_messages(payload: bytes) -> List[Dict[str, str]]:
    """Best-effort walk of a decoded Response payload -> list of chat dicts."""
    out: List[Dict[str, str]] = []

    def _extract_chat(msg_bytes: bytes):
        content = None
        user = None
        for field, wire, val in _iter_fields(msg_bytes):
            if wire != 2:
                continue
            if field == 3 and content is None:      # ChatMessage.content
                content = _as_text(val)
            elif field == 2 and user is None:        # ChatMessage.user -> User submsg
                for uf, uw, uv in _iter_fields(val):
                    if uw == 2 and uf == 3:          # User.nickName
                        user = _as_text(uv)
                        break
        # defense in depth: even if a field-number guess is ever wrong again,
        # never store something that's obviously protocol/diagnostic metadata
        # rather than a person's chat message.
        if content and not _JUNK_CONTENT_RE.search(content):
            out.append({"user": user or "", "content": content})

    # Response.messagesList = field 1 (repeated length-delimited)
    for field, wire, val in _iter_fields(payload):
        if wire != 2 or field != 1:
            continue
        # each val is a Message{ method=field1, payload=field2 }
        method = None
        body = None
        for mf, mw, mv in _iter_fields(val):
            if mw == 2 and mf == 1 and method is None:
                method = _as_text(mv)
            elif mw == 2 and mf == 2 and body is None:
                body = mv
        # Only decode confirmed chat messages -- Douyin multiplexes many
        # message types (gift/like/member/social/room-stats/...) over the
        # same channel, and guessing content out of the wrong type is exactly
        # how the internalExt bug above happened. Skip anything we can't
        # positively identify instead of guessing.
        if body and method and "ChatMessage" in method:
            _extract_chat(body)
    return out


def _parse_close_frame(payload: bytes):
    """RFC6455 close frame: optional 2-byte status code + UTF-8 reason."""
    if len(payload) < 2:
        return None, ""
    try:
        code = struct.unpack(">H", payload[:2])[0]
    except Exception:
        return None, ""
    try:
        reason = payload[2:].decode("utf-8", errors="replace")
    except Exception:
        reason = ""
    return code, reason


def _maybe_gunzip(data: bytes) -> bytes:
    if len(data) >= 2 and data[0] == 0x1F and data[1] == 0x8B:
        try:
            return gzip.decompress(data)
        except Exception:
            return data
    return data


def parse_push_frame(frame: bytes) -> List[Dict[str, str]]:
    """Top-level: a PushFrame whose field 8 is the (maybe gzip) payload that is a
    Response containing chat messages. Returns extracted chat dicts."""
    payload = None
    for field, wire, val in _iter_fields(frame):
        if wire == 2 and field == 8:
            payload = val
    if payload is None:
        # some builds send the Response directly
        payload = frame
    payload = _maybe_gunzip(payload)
    try:
        return _decode_chat_messages(payload)
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Minimal WebSocket client (RFC 6455) over ssl, stdlib only
# ---------------------------------------------------------------------------
class _WS:
    def __init__(self, host, port, path, headers, timeout=20):
        self.host, self.port, self.path = host, port, path
        self.headers = headers
        self.timeout = timeout
        self.sock = None

    def connect(self):
        import socket
        raw = socket.create_connection((self.host, self.port), self.timeout)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
        key = b64encode(os.urandom(16)).decode()
        lines = [f"GET {self.path} HTTP/1.1",
                 f"Host: {self.host}",
                 "Upgrade: websocket",
                 "Connection: Upgrade",
                 f"Sec-WebSocket-Key: {key}",
                 "Sec-WebSocket-Version: 13"]
        for k, v in (self.headers or {}).items():
            lines.append(f"{k}: {v}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        resp = self._read_until(b"\r\n\r\n")
        if b"101" not in resp.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"ws handshake failed: {resp[:80]!r}")

    def _read_until(self, sep):
        data = b""
        while sep not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data

    def _recv_exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                raise ConnectionError("socket closed")
            data += chunk
        return data

    def recv_frame(self):
        """Return (opcode, payload_bytes)."""
        b0, b1 = self._recv_exact(2)
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        length = b1 & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked and payload:
            payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
        return opcode, payload

    def send_frame(self, data, opcode=0x2):
        # client frames must be masked
        b0 = 0x80 | opcode
        header = bytes([b0])
        length = len(data)
        mask = os.urandom(4)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack(">H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", length)
        masked = bytes(data[i] ^ mask[i % 4] for i in range(length))
        self.sock.sendall(header + mask + masked)

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Douyin danmaku client
# ---------------------------------------------------------------------------
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36")

# Per-process random device id (19 digits, same magnitude Douyin's own web
# client uses) -- used for user_unique_id/wss_push_did below. Randomized
# instead of a hardcoded constant so every install doesn't present the exact
# same device id to Douyin.
_DEVICE_ID = str(random.randint(7_000_000_000_000_000_000, 7_999_999_999_999_999_999))


def _find_sign_js() -> Optional[Path]:
    """Locate the vendored Douyin websocket-signature JS. NOT included in
    this repo -- it's a large (50KB+), officially-undocumented, frequently
    -changing third-party script, not something reasonable to hand-port or
    vendor sight-unseen (unlike ab_sign.py, which is a from-scratch, auditable
    reimplementation of a *different*, simpler signing scheme used for HTTP
    APIs). See AGENT.md for where to get a copy (e.g. sign.js from
    tomas41026175/DouyinLiveWebFetcher) and where to put it."""
    candidate = Path(__file__).resolve().parent / "javascript" / "douyin_danmaku_sign.js"
    return candidate if candidate.exists() else None


def _generate_ws_signature(query: str) -> Optional[str]:
    """Douyin's webcast IM websocket needs a `signature` query param computed
    by a dedicated JS algorithm -- NOT the same as a-bogus (used for Douyin's
    HTTP h5 APIs, e.g. spider.py's room-enter call). An earlier version of
    this function wrongly reused a-bogus here; a real capture confirmed that
    produced a rejected handshake (CDN answered plain HTTP 200, never
    upgraded). The real algorithm (per the widely-used, actively-maintained
    reference tomas41026175/DouyinLiveWebFetcher's liveMan.py
    generateSignature()): MD5 a specific ordered subset of the connection
    params, then run that hash through the vendored JS's exported
    `get_sign(md5_hex)` function.

    Requires execjs + a real JS runtime (Node.js). Both are already project
    dependencies -- spider.py uses execjs the same way for several other
    platforms (liveme/haixiu/taobao/migu signing) -- so this isn't a new
    dependency, just reuse of an existing one. Returns None if the JS file
    isn't vendored or execjs/Node aren't available; caller must treat that as
    best-effort and connect unsigned."""
    js_path = _find_sign_js()
    if js_path is None:
        return None
    try:
        import execjs
    except Exception:
        return None
    keys = ("live_id", "aid", "version_code", "webcast_sdk_version",
            "room_id", "sub_room_id", "sub_channel_id", "did_rule",
            "user_unique_id", "device_platform", "device_type", "ac",
            "identity")
    wss_map = dict(urllib.parse.parse_qsl(query))
    param = ",".join(f"{k}={wss_map.get(k, '')}" for k in keys)
    md5_param = hashlib.md5(param.encode("utf-8")).hexdigest()
    try:
        ctx = execjs.compile(js_path.read_text(encoding="utf-8"))
        return ctx.call("get_sign", md5_param)
    except Exception as e:
        global _last_error
        _last_error = f"signature JS failed: {type(e).__name__}: {e}"
        return None


class DouyinDanmakuClient(threading.Thread):
    def __init__(self, url: str, room_id: str, anchor_name: str,
                 store, cookies: str = "", on_msg: Optional[Callable] = None):
        super().__init__(daemon=True, name=f"danmaku-{room_id}")
        self.url = url
        self.room_id = room_id
        self.anchor_name = anchor_name
        self.store = store
        self.cookies = cookies
        self.on_msg = on_msg
        self.connected = False
        self.msg_count = 0
        # NOTE: must NOT be named `_stop` -- threading.Thread already has a
        # private bound method `self._stop()` that CPython calls internally
        # (from is_alive()/_wait_for_tstate_lock and from the thread's own
        # bootstrap teardown) whenever the thread finishes. Naming this
        # attribute `_stop` silently shadows that method with an Event
        # instance, so the very next internal call to `self._stop()` raises
        # `TypeError: 'Event' object is not callable` -- which previously
        # crashed the coordinator's poll loop on every cycle after a
        # connection died, effectively halting all capture.
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def _ws_url(self):
        # Full param set copied from a known-working reference implementation
        # (tomas41026175/DouyinLiveWebFetcher's liveMan.py, last confirmed
        # working 2025-09-27) instead of a partial/reconstructed-from-memory
        # set -- our previous params were stale/incomplete enough that
        # Douyin's CDN edge (volc-dcdn) rejected the handshake outright,
        # answering plain HTTP 200 instead of 101 Switching Protocols.
        # room_id/timestamps/device id are substituted per-connection; the
        # reference hardcodes example values for these instead, which happens
        # to still work for them but isn't something to copy verbatim.
        now_ms = int(time.time() * 1000)
        query = (
            "app_name=douyin_web&version_code=180800&webcast_sdk_version=1.0.14-beta.0"
            "&update_version_code=1.0.14-beta.0&compress=gzip&device_platform=web&cookie_enabled=true"
            "&screen_width=1536&screen_height=864&browser_language=zh-CN&browser_platform=Win32"
            "&browser_name=Mozilla"
            "&browser_version=5.0%20(Windows%20NT%2010.0;%20Win64;%20x64)%20AppleWebKit/537.36%20(KHTML,"
            "%20like%20Gecko)%20Chrome/126.0.0.0%20Safari/537.36"
            "&browser_online=true&tz_name=Asia/Shanghai"
            f"&cursor=d-1_u-1_fh-{random.randint(10**18, 10**19-1)}_t-{now_ms}_r-1"
            f"&internal_ext=internal_src:dim|wss_push_room_id:{self.room_id}|wss_push_did:{_DEVICE_ID}"
            f"|first_req_ms:{now_ms}|fetch_time:{now_ms}|seq:1|wss_info:0-{now_ms}-0-0"
            f"|wrds_v:{random.randint(10**18, 10**19-1)}"
            "&host=https://live.douyin.com&aid=6383&live_id=1&did_rule=3&endpoint=live_pc&support_wrds=1"
            f"&user_unique_id={_DEVICE_ID}&im_path=/webcast/im/fetch/&identity=audience"
            f"&need_persist_msg_count=15&insert_task_id=&live_reason=&room_id={self.room_id}&heartbeatDuration=0"
        )
        sig = _generate_ws_signature(query)
        if sig:
            query += f"&signature={sig}"
        return "/webcast/im/push/v2/?" + query

    def run(self):
        global _last_error, _last_close
        headers = {
            "User-Agent": _UA,
            "Origin": "https://live.douyin.com",
        }
        if self.cookies:
            headers["Cookie"] = self.cookies
        ws = _WS("webcast100-ws-web-lq.douyin.com", 443, self._ws_url(), headers)
        connected_at = None
        try:
            ws.connect()
            self.connected = True
            connected_at = time.time()
            with _lock:
                _stats["connected"] = _stats.get("connected", 0) + 1
            last_hb = time.time()
            while not self._stop_event.is_set():
                try:
                    ws.sock.settimeout(5)
                    opcode, payload = ws.recv_frame()
                except Exception:
                    if self._stop_event.is_set():
                        break
                    # timeout -> send heartbeat and continue
                    if time.time() - last_hb > 10:
                        try:
                            ws.send_frame(b"", opcode=0x9)  # ping
                            last_hb = time.time()
                        except Exception:
                            break
                    continue
                if opcode == 0x8:      # close
                    code, reason = _parse_close_frame(payload)
                    with _lock:
                        _last_close = {
                            "anchor": self.anchor_name, "code": code,
                            "reason": reason,
                            "connected_seconds": (round(time.time() - connected_at, 1)
                                                   if connected_at else None),
                            "messages_received": self.msg_count,
                            "at": time.time(),
                        }
                    break
                if opcode in (0x1, 0x2) and payload:
                    self._handle(payload)
                if time.time() - last_hb > 10:
                    try:
                        ws.send_frame(b"", opcode=0x9)
                        last_hb = time.time()
                    except Exception:
                        break
        except Exception as e:
            _last_error = f"{self.anchor_name}: {type(e).__name__}: {e}"
        finally:
            self.connected = False
            ws.close()

    def _handle(self, payload: bytes):
        chats = parse_push_frame(payload)
        if not chats:
            return
        rows = [{"streamer_url": self.url, "anchor_name": self.anchor_name,
                 "platform": "抖音", "user": c["user"], "content": c["content"],
                 "msg_type": "chat"} for c in chats]
        try:
            self.store.record_many(rows)
        except Exception:
            pass
        self.msg_count += len(rows)
        with _lock:
            _stats["messages"] = _stats.get("messages", 0) + len(rows)
        if self.on_msg:
            try:
                self.on_msg(rows)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Coordinator hosted by web_ui
# ---------------------------------------------------------------------------
def _extract_room_id(url: str, resolve_room_id: Optional[Callable]) -> Optional[str]:
    if resolve_room_id:
        try:
            rid = resolve_room_id(url)
            if rid:
                return str(rid)
        except Exception:
            pass
    return None


def start_coordinator(get_active_douyin: Callable[[], List[Dict[str, Any]]],
                      store, get_cookies: Callable[[], str] = lambda: "",
                      resolve_room_id: Optional[Callable] = None,
                      poll_seconds: int = 30,
                      log: Callable[[str], None] = print):
    """Start the background coordinator once. It (dis)connects danmaku clients to
    match the set of live Douyin streamers.

    get_active_douyin() -> [{"url","anchor_name","room_id"(optional)}, ...]
    resolve_room_id(url) -> room_id string (optional helper if not in the dict)
    """
    global _coordinator

    def _loop():
        global _last_error
        while True:
            try:
                if not _enabled:
                    _shutdown_all()
                    time.sleep(poll_seconds)
                    continue
                actives = get_active_douyin() or []
                want = {}
                for a in actives:
                    url = a.get("url")
                    if not url:
                        continue
                    rid = a.get("room_id") or _extract_room_id(url, resolve_room_id)
                    if rid:
                        want[url] = (rid, a.get("anchor_name", ""))
                with _lock:
                    have = set(_workers.keys())
                # start new
                for url, (rid, anchor) in want.items():
                    if url in have and _workers[url].is_alive():
                        continue
                    c = DouyinDanmakuClient(url, rid, anchor, store,
                                            cookies=get_cookies())
                    c.start()
                    with _lock:
                        _workers[url] = c
                    log(f"[danmaku] connect {anchor or url} (room {rid})")
                # stop gone
                for url in list(have):
                    if url not in want:
                        with _lock:
                            c = _workers.pop(url, None)
                        if c:
                            c.stop()
                            log(f"[danmaku] disconnect {c.anchor_name or url}")
            except Exception as e:
                _last_error = f"coordinator: {type(e).__name__}: {e}"
                log(f"[danmaku] {_last_error}")
            time.sleep(poll_seconds)

    if _coordinator is not None and _coordinator.is_alive():
        return _coordinator
    _coordinator = threading.Thread(target=_loop, daemon=True,
                                    name="danmaku-coordinator")
    _coordinator.start()
    return _coordinator


def _shutdown_all():
    with _lock:
        clients = list(_workers.values())
        _workers.clear()
    for c in clients:
        c.stop()
