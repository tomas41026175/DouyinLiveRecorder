"""直播即時觀看（工作檯 /rec/live 用）的純邏輯，無 IO、可單元測試。

web_ui.py 的 /api/live/* 只負責：呼叫 spider 解析直播間、讀彈幕 DB、維護觀看中名單；
判斷與資料整理都在這裡：

- normalize_live_input：使用者輸入（抖音號、直播間網址、App 分享文字、主頁網址）→ 直播網址
- extract_qualities：spider 回傳的 room_data → 各畫質 HLS 網址（一律 https）
- WatchRegistry：「觀看中」名單（心跳 + TTL），併入彈幕擷取的對象，沒在錄的直播間也有彈幕

實測（2026-11-04）：抖音 CDN 的 HLS 網址回傳 http://，從 https 網頁讀取會被擋（mixed content）；
改 https:// 後跨網站讀取（CORS）正常、7 天後才過期。所以這裡一律改寫成 https。
"""
import hashlib
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlsplit

LIVE_PREFIX = "https://live.douyin.com/"

# 抖音號：字母、數字、底線、點（官方規則 6–16 字元；房間號是純數字，也走同一條路）
_DOUYIN_ID = re.compile(r"^[A-Za-z0-9_.]{2,32}$")
_URL_IN_TEXT = re.compile(r"https?://[\x21-\x7e]+")

# 畫質：spider 的 live_core_sdk_data.stream_data 鍵名 → 顯示名稱、排序（低 → 高）
# hls_pull_url_map 的鍵名對照：SD1=ld、SD2=sd、HD1=hd、FULL_HD1=origin（實測同一條 or4 串流）
QUALITY_ORDER = ["ao", "ld", "md", "sd", "hd", "origin"]
QUALITY_LABEL = {
    "ao": "純音訊", "ld": "標清", "md": "流暢", "sd": "高清", "hd": "超清", "origin": "藍光",
}
_MAP_KEY = {"SD1": "ld", "SD2": "sd", "HD1": "hd", "FULL_HD1": "origin", "ORIGIN": "origin"}
DEFAULT_QUALITY = "hd"


def normalize_live_input(text: str) -> Optional[str]:
    """使用者輸入 → 直播網址；無法辨識回 None（前端改為搜尋清單）。

    - 抖音號／房間號（`abc_123`、`336950279642`）→ https://live.douyin.com/<id>
    - 文字裡含 http(s) 網址（App 分享文字、整串貼上）→ 取最後一個網址
    - 少了 https:// 的 live.douyin.com/… 或 v.douyin.com/… → 補上
    與工作檯新增主播的 normalizeLiveUrl 規則一致。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    last = max(raw.rfind("http://"), raw.rfind("https://"))
    urls = _URL_IN_TEXT.findall(raw[last:]) if last >= 0 else []
    if urls:
        v = urls[0].rstrip("，。、！）)」』】")
    elif re.match(r"^(live\.|v\.|www\.)?douyin\.com/", raw):
        v = "https://" + (raw if raw.startswith(("live.", "v.", "www.")) else "live." + raw)
    elif _DOUYIN_ID.match(raw):
        v = LIVE_PREFIX + raw
    else:
        return None
    if v.rstrip("/") + "/" == LIVE_PREFIX:
        return None
    if "douyin.com" not in urlsplit(v).netloc:
        return None
    return v


def canonical_url(url: str) -> str:
    """live.douyin.com/<id>?xxx → 去掉 query 與結尾斜線；其他網址原樣。
    彈幕 DB 以網址為鍵，觀看名單與錄影清單要用同一種寫法才不會重複連線。"""
    u = (url or "").strip()
    if u.startswith(("https://live.douyin.com/", "http://live.douyin.com/")):
        u = "https://" + u.split("://", 1)[1].split("?")[0].split("#")[0].rstrip("/")
    return u


def url_key(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode("utf-8")).hexdigest()[:16]


def to_https(url: str) -> str:
    return "https://" + url[len("http://"):] if url.startswith("http://") else url


def _stream_data(room: Dict[str, Any]) -> Dict[str, Any]:
    """live_core_sdk_data.pull_data.stream_data（JSON 字串）→ dict；沒有回 {}。"""
    import json
    su = room.get("stream_url") or {}
    try:
        raw = (su.get("live_core_sdk_data") or {}).get("pull_data", {}).get("stream_data")
        return (json.loads(raw) or {}).get("data") or {} if raw else {}
    except Exception:
        return {}


def _sdk_options(room: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """options.qualities：{sdk_key: {resolution, v_bit_rate}}（決定哪些畫質真的有開）"""
    su = room.get("stream_url") or {}
    try:
        qs = (su.get("live_core_sdk_data") or {}).get("pull_data", {}).get("options", {}).get("qualities") or []
    except Exception:
        qs = []
    return {q.get("sdk_key"): q for q in qs if isinstance(q, dict) and q.get("sdk_key")}


def extract_qualities(room: Dict[str, Any]) -> List[Dict[str, Any]]:
    """room_data → [{key, label, url, resolution, bitrate}]，低 → 高，網址一律 https。

    優先用 stream_data（含純音訊 ao）；options.qualities 有列的才算主播真的有開那個畫質
    （ao 例外，永遠提供）。stream_data 解析不到時退回 hls_pull_url_map。同一條串流只留一份。
    """
    sd = _stream_data(room)
    opts = _sdk_options(room)
    found: Dict[str, Dict[str, Any]] = {}
    for key, v in sd.items():
        hls = ((v or {}).get("main") or {}).get("hls") if isinstance(v, dict) else None
        if key not in QUALITY_LABEL or not hls:
            continue
        if opts and key != "ao" and key not in opts:
            continue
        o = opts.get(key) or {}
        found[key] = {"key": key, "label": QUALITY_LABEL[key], "url": to_https(hls),
                      "resolution": o.get("resolution"), "bitrate": o.get("v_bit_rate")}
    if not [k for k in found if k != "ao"]:
        hmap = (room.get("stream_url") or {}).get("hls_pull_url_map") or {}
        for mk, u in hmap.items():
            key = _MAP_KEY.get(mk)
            if key and u and key not in found:
                o = opts.get(key) or {}
                found[key] = {"key": key, "label": QUALITY_LABEL[key], "url": to_https(u),
                              "resolution": o.get("resolution"), "bitrate": o.get("v_bit_rate")}
    seen, out = set(), []
    for key in QUALITY_ORDER:
        q = found.get(key)
        if not q:
            continue
        path = urlsplit(q["url"]).path
        if path in seen:
            continue
        seen.add(path)
        out.append(q)
    return out


def pick_default(qualities: List[Dict[str, Any]], prefer: str = DEFAULT_QUALITY) -> Optional[str]:
    """偏好畫質有就用；沒有就取比它低的最高一檔；再沒有取最低的影像畫質。"""
    video = [q["key"] for q in qualities if q["key"] != "ao"]
    if not video:
        return qualities[0]["key"] if qualities else None
    if prefer in video:
        return prefer
    rank = {k: i for i, k in enumerate(QUALITY_ORDER)}
    lower = [k for k in video if rank[k] < rank.get(prefer, 0)]
    return lower[-1] if lower else video[0]


def is_live_page(url: str) -> bool:
    """live.douyin.com/<id>：直接走網頁版 API（錄影本來就用這條，最穩）。"""
    return urlsplit(url or "").netloc.lower() == "live.douyin.com" and len(urlsplit(url).path.strip("/")) > 0


def room_is_live(room: Dict[str, Any]) -> bool:
    return bool(room) and room.get("status") == 2 and bool(room.get("stream_url"))


def live_url_from_room(room: Dict[str, Any]) -> Optional[str]:
    """App 端 API（短網址、主頁走的那條）回的 room 帶 owner.web_rid → https://live.douyin.com/<web_rid>。
    換成這個網址後：錄影清單比對得到、「加入錄影清單」存的是正式網址、之後改走網頁版 API。"""
    rid = str(((room or {}).get("owner") or {}).get("web_rid") or "").strip()
    return LIVE_PREFIX + rid if re.fullmatch(r"[A-Za-z0-9_.]{2,32}", rid) else None


def cookie_attempts(cookie: str) -> List[str]:
    """先用設定檔的 cookie，失敗再不帶 cookie 試一次（2026-11-04 實測：帶設定檔 cookie 時 App 端 API 回不出資料，
    不帶就正常）。沒設 cookie 只試一次。"""
    c = (cookie or "").strip()
    return [c, ""] if c else [""]


def pick_room(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """多次嘗試的結果挑一個：直播中的優先，其次有狀態的（例如確定未開播），都沒有回 {}。"""
    for r in results:
        if room_is_live(r):
            return r
    for r in results:
        if r and r.get("status") is not None:
            return r
    return {}


def summarize_room(url: str, room: Dict[str, Any]) -> Dict[str, Any]:
    """spider room_data → /api/live/source 的回應（不含任何 cookie／簽名以外的內部欄位）。"""
    room = room or {}
    live = room.get("status") == 2 and bool(room.get("stream_url"))
    qualities = extract_qualities(room) if live else []
    owner = room.get("owner") or {}
    cover = ((room.get("cover") or {}).get("url_list") or [None])[0]
    su = room.get("stream_url") or {}
    return {
        "url": canonical_url(url),
        "key": url_key(url),
        "live": live and bool(qualities),
        "anchorName": room.get("anchor_name") or owner.get("nickname") or "",
        "title": room.get("title") or "",
        "roomId": str(room.get("id_str") or room.get("id") or "") or None,
        "cover": to_https(cover) if cover else None,
        "orientation": "landscape" if su.get("stream_orientation") == 2 else "portrait",
        "qualities": qualities,
        "defaultQuality": pick_default(qualities),
    }


class WatchRegistry:
    """觀看中名單：前端每分鐘 touch 一次，超過 ttl 秒沒 touch 就移除。

    併入彈幕擷取的對象（_active_douyin_streamers），讓沒在錄的直播間也能擷取彈幕；
    最多 max_items 位，避免開太多 websocket。
    """

    def __init__(self, ttl: int = 120, max_items: int = 6, clock: Callable[[], float] = time.time):
        self.ttl = ttl
        self.max_items = max_items
        self._clock = clock
        self._lock = threading.Lock()
        self._items: Dict[str, Dict[str, Any]] = {}

    def touch(self, url: str, anchor_name: str = "", room_id: Optional[str] = None) -> bool:
        u = canonical_url(url)
        if not u:
            return False
        now = self._clock()
        with self._lock:
            self._prune(now)
            if u not in self._items and len(self._items) >= self.max_items:
                oldest = min(self._items, key=lambda k: self._items[k]["seen"])
                self._items.pop(oldest, None)
            prev = self._items.get(u) or {}
            self._items[u] = {"url": u, "anchor_name": anchor_name or prev.get("anchor_name", ""),
                              "room_id": room_id or prev.get("room_id"), "seen": now}
        return True

    def drop(self, url: str) -> None:
        with self._lock:
            self._items.pop(canonical_url(url), None)

    def active(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._prune(self._clock())
            return [dict(v) for v in self._items.values()]

    def _prune(self, now: float) -> None:
        for k in [k for k, v in self._items.items() if now - v["seen"] > self.ttl]:
            self._items.pop(k, None)


def merge_capture_targets(recording: List[Dict[str, Any]], watching: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """錄製中 + 觀看中 → 彈幕擷取對象，以 canonical_url 去重（錄製中的優先，保留原網址寫法，
    因為既有彈幕都存在那個網址底下）。"""
    out, seen = [], set()
    for a in recording or []:
        c = canonical_url(a.get("url") or "")
        if c and c not in seen:
            seen.add(c)
            out.append(a)
    for w in watching or []:
        c = canonical_url(w.get("url") or "")
        if c and c not in seen and "douyin.com" in c:
            seen.add(c)
            item = {"url": w["url"], "anchor_name": w.get("anchor_name", "")}
            if w.get("room_id"):
                item["room_id"] = w["room_id"]
            out.append(item)
    return out


def shape_danmaku(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """DB 列（desc）→ 前端用的 asc 清單，只留需要的欄位。"""
    return [{"id": r.get("id"), "ts": r.get("ts"), "user": r.get("user") or "", "content": r.get("content") or ""}
            for r in reversed(rows or []) if (r.get("msg_type") or "chat") == "chat"]
