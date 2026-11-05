"""臨時錄製（工作檯看直播的「錄製」按鈕）的純邏輯，IO 由 web_ui 注入、可單元測試。

- 錄「現在看的這一場」，不加入監控主播清單（不碰 URL_config.ini、不經過錄影 exe）。
- 綁著頁面：前端每 10 秒送心跳，超過 HEARTBEAT_TIMEOUT 秒沒心跳（關掉頁面、換主播、手機鎖屏斷線）就自動停；
  按停止或離開頁面時前端也會直接送停止。
- web_ui 直接起 ffmpeg 以 -c copy 錄 FLV（沒有 FLV 才用 HLS），先寫 .ts，停止後轉封裝成 .mp4（失敗就保留 .ts）。
- 安全上限：同時最多 MAX_ACTIVE 場、每場最長 MAX_SECONDS（ffmpeg -t）。
"""
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

HEARTBEAT_TIMEOUT = 30
MAX_ACTIVE = 3
MAX_SECONDS = 6 * 3600
FOLDER = "手動錄製"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")

# 錄最好的畫質：原畫 → 超清 → 高清 → 標清（純音訊不錄）
_RECORD_ORDER = ["origin", "hd", "sd", "md", "ld"]


def _https(u: str) -> str:
    return "https://" + u[len("http://"):] if u.startswith("http://") else u


def best_record_url(room: Dict[str, Any], stream_data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """room_data（與解析好的 stream_data）→ {"url", "quality", "kind"}：優先 FLV（單一連線、最穩），沒有才 HLS。"""
    for key in _RECORD_ORDER:
        main = ((stream_data or {}).get(key) or {}).get("main") or {}
        for kind in ("flv", "hls"):
            u = main.get(kind)
            if u:
                return {"url": _https(u), "quality": key, "kind": kind}
    su = (room or {}).get("stream_url") or {}
    for kind, field in (("flv", "flv_pull_url"), ("hls", "hls_pull_url_map")):
        m = su.get(field) or {}
        for k in ("FULL_HD1", "HD1", "SD2", "SD1"):
            if m.get(k):
                return {"url": _https(m[k]), "quality": k, "kind": kind}
    return None


def safe_name(name: str, fallback: str = "未命名") -> str:
    """Windows 檔名不能有 \\/:*?"<>| 與控制字元；去頭尾空白與點，最長 40 字。"""
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (name or "").strip()).strip(" .")
    return (s or fallback)[:40]


def plan_output(save_root: Path, anchor: str, now: datetime) -> Path:
    """<直播保存路径>/手動錄製/<主播>/<主播>_YYYY-MM-DD_HH-MM-SS.ts"""
    a = safe_name(anchor)
    return Path(save_root) / FOLDER / a / f"{a}_{now:%Y-%m-%d_%H-%M-%S}.ts"


def ffmpeg_cmd(ffmpeg: str, src: str, out: Path, max_seconds: int = MAX_SECONDS) -> List[str]:
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-rw_timeout", "15000000",
        "-user_agent", USER_AGENT,
        "-headers", "Referer: https://live.douyin.com/\r\n",
        "-i", src,
        "-map", "0", "-c", "copy", "-t", str(int(max_seconds)),
        "-f", "mpegts", str(out),
    ]


def remux_cmd(ffmpeg: str, ts: Path) -> List[str]:
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(ts),
            "-map", "0", "-c", "copy", "-movflags", "+faststart", str(ts.with_suffix(".mp4"))]


class LimitError(Exception):
    pass


class AdhocRegistry:
    """進行中的臨時錄製。spawn(cmd) 回傳 process-like（poll()／terminate()／stdin）；stop(proc) 由 web_ui 注入（送 q 優雅結束）。"""

    def __init__(self, spawn: Callable[[List[str]], Any], stop: Callable[[Any], None],
                 clock: Callable[[], float] = time.time, max_active: int = MAX_ACTIVE,
                 timeout: int = HEARTBEAT_TIMEOUT):
        self._spawn = spawn
        self._stop = stop
        self._clock = clock
        self.max_active = max_active
        self.timeout = timeout
        self._lock = threading.Lock()
        self._items: Dict[str, Dict[str, Any]] = {}

    def start(self, url: str, anchor: str, cmd: List[str], out: Path) -> Dict[str, Any]:
        with self._lock:
            self._reap_locked()
            for it in self._items.values():
                if it["url"] == url and it["state"] == "recording":
                    it["seen"] = self._clock()
                    return self._public(it)  # 同一場已經在錄：沿用（重新整理頁面、重複按）
            if sum(1 for it in self._items.values() if it["state"] == "recording") >= self.max_active:
                raise LimitError(f"同時最多錄 {self.max_active} 場")
            proc = self._spawn(cmd)
            now = self._clock()
            rid = uuid.uuid4().hex[:12]
            self._items[rid] = {"id": rid, "url": url, "anchor": anchor, "file": str(out), "proc": proc,
                                "started": now, "seen": now, "state": "recording", "reason": None, "ended": None}
            return self._public(self._items[rid])

    def heartbeat(self, rid: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            self._reap_locked()
            it = self._items.get(rid)
            if it is None:
                return None
            if it["state"] == "recording":
                it["seen"] = self._clock()
            return self._public(it)

    def stop(self, rid: str, reason: str = "stopped") -> Optional[Dict[str, Any]]:
        with self._lock:
            it = self._items.get(rid)
            if it is None:
                return None
            self._end_locked(it, reason)
            return self._public(it)

    def reap(self) -> List[Dict[str, Any]]:
        """心跳逾時的停掉、ffmpeg 自己結束的（斷流、到上限）標成 ended；回傳這次結束的，web_ui 拿去轉 mp4。"""
        with self._lock:
            return self._reap_locked()

    def active(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._reap_locked()
            return [self._public(it) for it in self._items.values() if it["state"] == "recording"]

    def drain_finished(self) -> List[Dict[str, Any]]:
        """已結束、還沒交給轉檔的（只交一次）；結束超過 10 分鐘的紀錄移除。"""
        with self._lock:
            out = []
            now = self._clock()
            for rid, it in list(self._items.items()):
                if it["state"] != "recording" and not it.get("handed"):
                    it["handed"] = True
                    out.append(self._public(it))
                if it["state"] != "recording" and it["ended"] and now - it["ended"] > 600:
                    self._items.pop(rid, None)
            return out

    def proc_of(self, rid: str) -> Any:
        with self._lock:
            it = self._items.get(rid)
            return it["proc"] if it else None

    # ── internal ──
    def _end_locked(self, it: Dict[str, Any], reason: str) -> None:
        if it["state"] != "recording":
            return
        try:
            self._stop(it["proc"])
        except Exception:
            pass
        it["state"] = "ended"
        it["reason"] = reason
        it["ended"] = self._clock()

    def _reap_locked(self) -> List[Dict[str, Any]]:
        now = self._clock()
        ended = []
        for it in self._items.values():
            if it["state"] != "recording":
                continue
            if it["proc"].poll() is not None:
                it["state"] = "ended"
                it["reason"] = "stream_ended"
                it["ended"] = now
                ended.append(self._public(it))
            elif now - it["seen"] > self.timeout:
                self._end_locked(it, "left")
                ended.append(self._public(it))
        return ended

    def _public(self, it: Dict[str, Any]) -> Dict[str, Any]:
        end = it["ended"] if it["ended"] else self._clock()
        return {"id": it["id"], "url": it["url"], "anchor": it["anchor"], "file": it["file"],
                "state": it["state"], "reason": it["reason"],
                "startedAt": datetime.fromtimestamp(it["started"]).isoformat(timespec="seconds"),
                "elapsedSec": int(max(0, end - it["started"]))}
