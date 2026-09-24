"""
compressor.py - Post-recording H.265 auto-compression for DouyinLiveRecorder.

Goal: reduce storage consumption WITHOUT touching in-progress recordings.

Parallel multi-encoder pipeline (auto-detected):
  * On startup we probe which HEVC encoders this ffmpeg actually supports —
    hevc_nvenc (NVIDIA dGPU), hevc_qsv (Intel iGPU / QuickSync),
    hevc_amf (AMD APU/GPU), and libx265 (CPU). Each available encoder becomes
    one "lane".
  * All lanes pull from ONE shared, thread-safe queue of candidate files, each
    grabbing a DIFFERENT file. A single video is never split across encoders
    (that would wreck quality at the seams); instead, different files compress
    simultaneously, so throughput scales with the number of working encoders.
  * `max_lanes` (and per-encoder enable flags) let the user cap parallelism;
    "auto" uses every detected hardware encoder plus optionally CPU.

How safety is guaranteed (unchanged, per file):
  * A file is only "finished" when its mtime has been stable for
    `stable_minutes`, so we never touch a file still being recorded/converted.
  * Output goes to `<stem>_hevc.part.mp4`; only after ffmpeg exits 0 AND the
    output duration matches the input (within max(2s,1%)) AND it is actually
    smaller, is it renamed to `<stem>_hevc.mp4` and the original deleted.
  * Files already HEVC / too small / previously failed are remembered in
    `config/compress_state.json` and not retried forever.
  * ffmpeg runs at below-normal CPU priority so live recording is never starved.

Hosted by web_ui.py (runs from source, no exe rebuild needed).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import common as _common

GB = 1024 ** 3
MB = 1024 ** 2

# Encoder registry: key -> (ffmpeg encoder name, human label, is_hardware)
ENCODERS = {
    "nvidia": ("hevc_nvenc", "NVIDIA 獨顯", True),
    "intel":  ("hevc_qsv",   "Intel 內顯", True),
    "amd":    ("hevc_amf",   "AMD 內顯/顯卡", True),
    "cpu":    ("libx265",    "CPU", False),
}

DEFAULT_COMPRESS = {
    "enabled": False,
    "encoder": "auto",          # "auto" = use enabled_encoders (or all detected);
                                # or a single key ("nvidia"/"intel"/"amd"/"cpu")
    "enabled_encoders": [],     # explicit user pick of which lanes to run.
                                # [] = auto (every detected encoder, honoring use_cpu_lane)
    "use_cpu_lane": True,       # in pure-auto mode, also run a CPU (libx265) lane
    "max_lanes": 0,             # 0 = no cap (one lane per available encoder)
    "crf": 26,                  # 23 = near-lossless bigger, 28 = smaller softer
    "preset": "fast",           # libx265 preset / mapped for hw encoders
    "stable_minutes": 15,       # file mtime must be older than this
    "min_size_mb": 100,         # don't bother with small files
    "scan_interval_minutes": 10,
    "min_free_ratio": 1.0,      # need free space >= input_size * ratio
    "suffix": "_hevc",
    "extensions": [".flv", ".mp4", ".ts", ".mkv"],
}

# NVENC preset map: reuse the libx265-style names the UI already shows.
_NVENC_PRESET = {"veryfast": "p2", "fast": "p4", "medium": "p5",
                 "slow": "p6", "veryslow": "p7"}
# QSV preset map (QuickSync uses veryfast..veryslow too, but be explicit).
_QSV_PRESET = {"veryfast": "veryfast", "fast": "fast", "medium": "medium",
               "slow": "slow", "veryslow": "veryslow"}


def build_encode_args(settings: Dict[str, Any], encoder_key: str) -> List[str]:
    """ffmpeg -c:v ... args for one specific encoder lane. Hardware encoders use
    constant-quality on roughly the CRF scale, so the UI's quality number carries
    over. Audio is always stream-copied (lossless) by the caller."""
    s = _deep_merge(DEFAULT_COMPRESS, settings or {})
    q = str(s["crf"])
    preset = str(s["preset"])
    name = ENCODERS.get(encoder_key, ENCODERS["cpu"])[0]
    if name == "hevc_nvenc":
        return ["-c:v", "hevc_nvenc", "-preset", _NVENC_PRESET.get(preset, "p4"),
                "-rc", "vbr", "-cq", q, "-b:v", "0", "-tag:v", "hvc1"]
    if name == "hevc_qsv":
        return ["-c:v", "hevc_qsv", "-preset", _QSV_PRESET.get(preset, "fast"),
                "-global_quality", q, "-tag:v", "hvc1"]
    if name == "hevc_amf":
        # AMF uses -qp_i/-qp_p in CQP mode; quality_enhancement off for speed.
        return ["-c:v", "hevc_amf", "-rc", "cqp",
                "-qp_i", q, "-qp_p", q, "-tag:v", "hvc1"]
    return ["-c:v", "libx265", "-preset", preset, "-crf", q, "-tag:v", "hvc1"]


# ---------------------------------------------------------------------------
# Module state
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_worker: Optional[threading.Thread] = None      # the coordinator thread
_wake = threading.Event()
_last_scan_at: Optional[float] = None
_last_error: Optional[str] = None
_runtime_note: Optional[str] = None
_paused: bool = False

# Set by trigger_scan(force=True) -- lets ONE manual "立即開始壓縮" pass run the
# queue to completion even when settings["enabled"] is False (UIUX_SPEC §3.6:
# "即使關閉自動壓縮也能手動觸發，開關只控制是否自動背景執行"). Cleared by the
# coordinator only after that forced batch finishes, so lane threads (which
# re-check the gate on every file) stay open for the whole manual run instead
# of the gate slamming shut again after the coordinator's own first check.
_force_run: bool = False

# Per-lane live state, keyed by encoder_key. Each: {file, percent, started_at,
# in_bytes, proc}. Protected by _lock.
_lanes: Dict[str, Dict[str, Any]] = {}

# Shared batch progress for overall queue bar.
_queue: Dict[str, Any] = {"total": 0, "done": 0, "claimed": 0, "active": False}


def _set_runtime_note(msg: Optional[str]) -> None:
    global _runtime_note
    _runtime_note = msg


def set_paused(paused: bool) -> None:
    """Pause/resume the queue at runtime. Pausing aborts every in-progress lane
    immediately; those files are left untouched and retried after resume."""
    global _paused
    _paused = bool(paused)
    if _paused:
        with _lock:
            procs = [ln.get("proc") for ln in _lanes.values()]
        for proc in procs:
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
    else:
        _wake.set()


def is_paused() -> bool:
    return _paused


def _gate_open(s: Dict[str, Any]) -> bool:
    """Should the coordinator/lanes proceed? True when auto-compress is
    enabled, OR a manual run is in progress (_force_run) -- either way, never
    while paused. Equivalent to the old bare `s.get("enabled")` check whenever
    _force_run is False (its default), so every pre-existing caller keeps
    identical behavior; only the new manual-trigger path (trigger_scan(force=
    True), see api_compress_scan in web_ui.py) exercises the new branch."""
    if _paused:
        return False
    return bool(s.get("enabled")) or _force_run


# ---------------------------------------------------------------------------
# Settings / state persistence (same pattern as alerts.py)
# ---------------------------------------------------------------------------
# Thin alias, not a re-implementation (Stage 5 tech-debt cleanup, UIUX_SPEC.md
# §9.1) -- every existing internal caller of `_deep_merge(...)` in this file
# keeps working unchanged.
_deep_merge = _common.deep_merge


def load_settings(path) -> Dict[str, Any]:
    return _common.load_json_settings(path, DEFAULT_COMPRESS)


def save_settings(path, data: Dict[str, Any]) -> None:
    _common.save_json_settings(path, data, DEFAULT_COMPRESS)


def _load_state(path) -> Dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("skip", {})
            data.setdefault("stats", {"files": 0, "in_bytes": 0, "out_bytes": 0})
            data.setdefault("history", [])
            return data
    except Exception:
        pass
    return {"skip": {}, "stats": {"files": 0, "in_bytes": 0, "out_bytes": 0},
            "history": []}


# Persisting state is shared across lanes, so guard writes with a dedicated lock.
_state_lock = threading.Lock()


def _save_state(path, state: Dict[str, Any]) -> None:
    try:
        with _state_lock:
            skip = state.get("skip", {})
            if len(skip) > 2000:
                for k in list(skip)[: len(skip) - 2000]:
                    skip.pop(k, None)
            state["history"] = state.get("history", [])[-50:]
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    except Exception:
        pass


def _file_key(p: Path) -> str:
    try:
        st = p.stat()
        return f"{p}|{st.st_size}|{int(st.st_mtime)}"
    except Exception:
        return str(p)


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe helpers
# ---------------------------------------------------------------------------
def _popen_flags() -> dict:
    if os.name == "nt":
        # BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
        return {"creationflags": 0x00004000 | 0x08000000}
    return {}


def find_tool(name: str, ffmpeg_dir: Optional[Path]) -> Optional[str]:
    exe = f"{name}.exe" if os.name == "nt" else name
    if ffmpeg_dir:
        cand = Path(ffmpeg_dir) / exe
        if cand.exists():
            return str(cand)
    return shutil.which(name)


def probe(ffprobe: str, path: Path, entries: str) -> Optional[str]:
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", entries,
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            stderr=subprocess.DEVNULL, timeout=60, **_popen_flags())
        return out.decode("utf-8", "ignore").strip().splitlines()[0] if out.strip() else None
    except Exception:
        return None


def probe_duration(ffprobe: str, path: Path) -> Optional[float]:
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            stderr=subprocess.DEVNULL, timeout=60, **_popen_flags())
        return float(out.decode().strip())
    except Exception:
        return None


def probe_vcodec(ffprobe: str, path: Path) -> Optional[str]:
    return probe(ffprobe, path, "stream=codec_name")


# ---------------------------------------------------------------------------
# Encoder auto-detection
# ---------------------------------------------------------------------------
_enc_cache: Dict[str, Any] = {}


def detect_encoders(ffmpeg_dir: Optional[Path] = None) -> Dict[str, bool]:
    """Return {encoder_key: available?} by parsing `ffmpeg -encoders`. Cached.
    Note: presence in the build does not 100% guarantee the hardware works at
    runtime (e.g. no NVIDIA card), but compress_file falls back to CPU if a
    hardware lane fails, so this is a safe first filter."""
    if "map" in _enc_cache:
        return _enc_cache["map"]
    avail = {k: False for k in ENCODERS}
    try:
        ffmpeg = find_tool("ffmpeg", ffmpeg_dir)
        if ffmpeg:
            out = subprocess.check_output([ffmpeg, "-hide_banner", "-encoders"],
                                          stderr=subprocess.STDOUT, timeout=15,
                                          **_popen_flags())
            text = out.decode("utf-8", "ignore")
            for key, (name, _label, _hw) in ENCODERS.items():
                avail[key] = name in text
    except Exception:
        # at minimum assume CPU libx265 is usable if ffmpeg exists at all
        avail["cpu"] = True
    _enc_cache["map"] = avail
    return avail


def available_encoder_keys(settings: Dict[str, Any],
                           ffmpeg_dir: Optional[Path] = None) -> List[str]:
    """Resolve which lanes to run, honoring settings (priority order):

    1. enabled_encoders == explicit list -> exactly those that are detected
       (lets the user hand-pick which encoders run in parallel right now).
    2. encoder == a specific key -> just that one lane (if available).
    3. otherwise "auto": every detected hardware encoder, plus CPU if
       use_cpu_lane is on.
    All paths fall back to CPU if nothing else is usable, and respect max_lanes.
    """
    s = _deep_merge(DEFAULT_COMPRESS, settings or {})
    det = detect_encoders(ffmpeg_dir)
    order = ["nvidia", "intel", "amd", "cpu"]

    picked = [k for k in (s.get("enabled_encoders") or []) if k in ENCODERS]
    if picked:
        lanes = [k for k in order if k in picked and det.get(k)]
    else:
        enc = str(s.get("encoder", "auto")).lower()
        if enc != "auto" and enc in ENCODERS:
            lanes = [enc] if det.get(enc) else []
        else:
            lanes = [k for k in ("nvidia", "intel", "amd") if det.get(k)]
            if s.get("use_cpu_lane", True) and det.get("cpu"):
                lanes.append("cpu")

    if not lanes and det.get("cpu"):
        lanes = ["cpu"]
    cap = int(s.get("max_lanes", 0) or 0)
    if cap > 0:
        lanes = lanes[:cap]
    return lanes


# ---------------------------------------------------------------------------
# Thread-safe candidate queue (shared by all lanes)
# ---------------------------------------------------------------------------
_q_lock = threading.Lock()
_candidates: List[Path] = []
_inflight: set = set()   # paths currently claimed by some lane


def _queue_fill(cands: List[Path]) -> None:
    global _candidates
    with _q_lock:
        _candidates = list(cands)
        _inflight.clear()
    with _lock:
        _queue.update(total=len(cands), done=0, claimed=0,
                      active=len(cands) > 0)


def _queue_take() -> Optional[Path]:
    """Pop the next unclaimed candidate (oldest first). None if empty."""
    with _q_lock:
        while _candidates:
            p = _candidates.pop(0)
            if str(p) in _inflight:
                continue
            _inflight.add(str(p))
            with _lock:
                _queue["claimed"] = _queue.get("claimed", 0) + 1
            return p
    return None


def _queue_release(p: Path, done_ok: bool) -> None:
    with _q_lock:
        _inflight.discard(str(p))
    with _lock:
        if done_ok:
            _queue["done"] = _queue.get("done", 0) + 1


def _queue_clear() -> None:
    global _candidates
    with _q_lock:
        _candidates = []
        _inflight.clear()
    with _lock:
        _queue.update(total=0, done=0, claimed=0, active=False)


# ---------------------------------------------------------------------------
# Candidate scan (pure-ish, unit-testable)
# ---------------------------------------------------------------------------
def scan_candidates(save_path: Path, settings: Dict[str, Any],
                    skip_keys: Dict[str, Any]) -> List[Path]:
    s = _deep_merge(DEFAULT_COMPRESS, settings or {})
    exts = {e.lower() for e in s["extensions"]}
    suffix = s["suffix"]
    stable_sec = float(s["stable_minutes"]) * 60
    min_bytes = float(s["min_size_mb"]) * MB
    now = time.time()
    out: List[Path] = []
    if not save_path or not Path(save_path).exists():
        return out
    for root, _dirs, files in os.walk(save_path):
        for name in files:
            p = Path(root) / name
            if p.suffix.lower() not in exts:
                continue
            stem = p.stem
            if stem.endswith(suffix) or f"{suffix}.part" in name:
                continue
            try:
                st = p.stat()
            except Exception:
                continue
            if st.st_size < min_bytes:
                continue
            if now - st.st_mtime < stable_sec:
                continue                      # possibly still recording/converting
            if _file_key(p) in skip_keys:
                continue
            out.append(p)
    out.sort(key=lambda x: x.stat().st_mtime if x.exists() else 0)  # oldest first
    return out


# ---------------------------------------------------------------------------
# Overview scan (compression ratio panel, UIUX_SPEC §3.6 / §6). Pure
# filesystem walk -- classifies every recording by filename only (no ffprobe),
# so it's cheap enough to run behind the short cache web_ui.py puts in front
# of it (same TTL-cache shape as web_ui.get_folder_size_cached).
# ---------------------------------------------------------------------------
def scan_overview(save_path, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Walk `save_path` once and split recording files into "compressed"
    (filename already carries the `_hevc` suffix -- i.e. compress_file already
    finished it) vs "pending" (a plain recording not yet compressed). Partial/
    in-progress temp outputs (`<suffix>.part.*`) are excluded from both
    buckets: they're neither a finished result nor a stable candidate yet, so
    counting them either way would double-count against the original file
    that's still sitting there being replaced."""
    s = _deep_merge(DEFAULT_COMPRESS, settings or {})
    exts = {e.lower() for e in s["extensions"]}
    suffix = s["suffix"]
    total_files = total_bytes = 0
    compressed_files = compressed_bytes = 0
    if save_path and Path(save_path).exists():
        for root, _dirs, files in os.walk(save_path):
            for name in files:
                p = Path(root) / name
                if p.suffix.lower() not in exts:
                    continue
                if f"{suffix}.part" in name:
                    continue
                try:
                    size = p.stat().st_size
                except Exception:
                    continue
                total_files += 1
                total_bytes += size
                if p.stem.endswith(suffix):
                    compressed_files += 1
                    compressed_bytes += size
    return {
        "total_files": total_files,
        "total_bytes": total_bytes,
        "compressed_files": compressed_files,
        "compressed_bytes": compressed_bytes,
        "pending_files": total_files - compressed_files,
        "pending_bytes": total_bytes - compressed_bytes,
    }


# ---------------------------------------------------------------------------
# Compression of a single file on a specific lane/encoder
# ---------------------------------------------------------------------------
def compress_file(path: Path, settings: Dict[str, Any], state: Dict[str, Any],
                  state_file, ffmpeg: str, ffprobe: str,
                  encoder_key: str = "cpu") -> Dict[str, Any]:
    """Compress one file using `encoder_key`. Returns {"ok": bool, "reason": ...}.
    On a hardware-encoder failure, falls back to CPU once so the file still gets
    compressed instead of being skipped forever."""
    s = _deep_merge(DEFAULT_COMPRESS, settings or {})
    suffix = s["suffix"]
    key = _file_key(path)

    def _skip(reason: str) -> Dict[str, Any]:
        state["skip"][key] = {"reason": reason, "at": int(time.time())}
        _save_state(state_file, state)
        return {"ok": False, "reason": reason}

    try:
        in_size = path.stat().st_size
    except Exception:
        return {"ok": False, "reason": "stat_failed"}

    free = shutil.disk_usage(path.parent).free
    if free < in_size * float(s["min_free_ratio"]):
        return {"ok": False, "reason": "low_free_space"}  # retry next scan

    if probe_vcodec(ffprobe, path) == "hevc":
        return _skip("already_hevc")

    in_dur = probe_duration(ffprobe, path)
    if not in_dur or in_dur <= 0:
        return _skip("probe_failed")

    # Per-lane temp names so two lanes never collide on the same output path.
    final = path.with_name(path.stem + suffix + ".mp4")
    tmp = path.with_name(f"{path.stem}{suffix}.part.{encoder_key}.mp4")
    if final.exists():
        out_dur = probe_duration(ffprobe, final)
        if out_dur and abs(out_dur - in_dur) <= max(2.0, in_dur * 0.01):
            path.unlink(missing_ok=True)
            return {"ok": True, "reason": "already_done",
                    "in_bytes": in_size, "out_bytes": final.stat().st_size}
        return _skip("output_name_taken")

    started = time.time()

    def _run(enc_key: str) -> int:
        """Run one ffmpeg pass on `enc_key`, streaming progress into the lane."""
        cmd = ([ffmpeg, "-y", "-i", str(path)] + build_encode_args(s, enc_key) +
               ["-c:a", "copy",
                "-progress", "pipe:1", "-nostats", "-loglevel", "error",
                str(tmp)])
        with _lock:
            _lanes[encoder_key] = {"encoder": encoder_key, "file": str(path),
                                   "percent": 0.0, "started_at": int(started),
                                   "in_bytes": in_size}
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, **_popen_flags())
        with _lock:
            _lanes[encoder_key]["proc"] = proc
        try:
            for raw in proc.stdout:  # type: ignore[union-attr]
                line = raw.decode("utf-8", "ignore").strip()
                if line.startswith("out_time_ms="):
                    try:
                        done = int(line.split("=", 1)[1]) / 1_000_000
                        with _lock:
                            if encoder_key in _lanes:
                                _lanes[encoder_key]["percent"] = round(
                                    min(99.9, done / in_dur * 100), 1)
                    except Exception:
                        pass
            return proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
            with _lock:
                if encoder_key in _lanes:
                    _lanes[encoder_key].pop("proc", None)

    is_hw = ENCODERS.get(encoder_key, ENCODERS["cpu"])[2]
    try:
        rc = _run(encoder_key)
        if _paused:
            tmp.unlink(missing_ok=True)
            return {"ok": False, "reason": "paused"}
        # Hardware lane failed -> retry this file on CPU once.
        if rc != 0 and is_hw:
            tmp.unlink(missing_ok=True)
            rc = _run("cpu")
            if _paused:
                tmp.unlink(missing_ok=True)
                return {"ok": False, "reason": "paused"}
            if rc == 0:
                _set_runtime_note(
                    f"{ENCODERS[encoder_key][1]} 編碼失敗，已改用 CPU 壓這檔；"
                    f"可在面板取消該編碼器或檢查驅動")
    except Exception as e:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        with _lock:
            _lanes.pop(encoder_key, None)
        return _skip(f"ffmpeg_error:{type(e).__name__}")
    finally:
        with _lock:
            _lanes.pop(encoder_key, None)

    if rc != 0:
        tmp.unlink(missing_ok=True)
        return _skip(f"ffmpeg_exit_{rc}")

    # ---- verification before touching the original -------------------------
    try:
        out_size = tmp.stat().st_size
    except Exception:
        return _skip("output_missing")
    out_dur = probe_duration(ffprobe, tmp)
    if not out_dur or abs(out_dur - in_dur) > max(2.0, in_dur * 0.01):
        tmp.unlink(missing_ok=True)
        return _skip("duration_mismatch")
    if out_size <= 0 or out_size >= in_size:
        tmp.unlink(missing_ok=True)
        return _skip("not_smaller")

    # Two lanes could (rarely) both finish a same-named final; guard the rename.
    with _state_lock:
        if final.exists():
            tmp.unlink(missing_ok=True)
            return {"ok": False, "reason": "raced_output"}
        tmp.rename(final)
    try:
        path.unlink()
    except Exception:
        return _skip("delete_failed_output_kept")

    with _state_lock:
        st = state["stats"]
        st["files"] = st.get("files", 0) + 1
        st["in_bytes"] = st.get("in_bytes", 0) + in_size
        st["out_bytes"] = st.get("out_bytes", 0) + out_size
        state["history"].append({
            "file": str(path), "in_bytes": in_size, "out_bytes": out_size,
            "saved_bytes": in_size - out_size, "encoder": encoder_key,
            "took_sec": int(time.time() - started), "at": int(time.time()),
        })
    _save_state(state_file, state)
    return {"ok": True, "in_bytes": in_size, "out_bytes": out_size,
            "encoder": encoder_key}


# ---------------------------------------------------------------------------
# Worker: coordinator + one lane thread per available encoder
# ---------------------------------------------------------------------------
def start_worker(get_save_path: Callable[[], Path], settings_file,
                 state_file, ffmpeg_dir: Optional[Path] = None,
                 log: Callable[[str], None] = print) -> Optional[threading.Thread]:
    """Idempotent: starts the background scan/compress coordinator once."""
    global _worker
    if _worker is not None and _worker.is_alive():
        return _worker

    def _lane(enc_key: str, state: Dict[str, Any], ffmpeg: str, ffprobe: str,
              settings_file_):
        while True:
            s = load_settings(settings_file_)
            if not _gate_open(s):
                return
            p = _queue_take()
            if p is None:
                return
            try:
                r = compress_file(p, s, state, state_file, ffmpeg, ffprobe, enc_key)
            except Exception as e:
                r = {"ok": False, "reason": f"lane_error:{type(e).__name__}"}
            _queue_release(p, bool(r.get("ok")))
            if r.get("ok"):
                saved = r.get("in_bytes", 0) - r.get("out_bytes", 0)
                log(f"[compress:{enc_key}] {p.name}: -{saved / GB:.2f}GB")
            elif r.get("reason") == "paused":
                return
            elif r.get("reason") == "low_free_space":
                log(f"[compress:{enc_key}] skip {p.name}: low free space")
                return  # space is global; other lanes will hit it too

    def _coordinator():
        global _last_scan_at, _last_error, _force_run
        while True:
            was_forced = _force_run  # snapshot: this pass is a manual run
            try:
                s = load_settings(settings_file)
                interval = max(60, float(s["scan_interval_minutes"]) * 60)
                if not _gate_open(s):
                    _wake.wait(timeout=interval); _wake.clear(); continue
                ffmpeg = find_tool("ffmpeg", ffmpeg_dir)
                ffprobe = find_tool("ffprobe", ffmpeg_dir)
                if not ffmpeg or not ffprobe:
                    _last_error = "ffmpeg/ffprobe not found"
                    _wake.wait(timeout=interval); _wake.clear(); continue
                lanes = available_encoder_keys(s, ffmpeg_dir)
                if not lanes:
                    _last_error = "no usable encoder detected"
                    _wake.wait(timeout=interval); _wake.clear(); continue
                _last_error = None
                state = _load_state(state_file)
                cands = scan_candidates(Path(get_save_path()), s,
                                        state.get("skip", {}))
                _last_scan_at = time.time()
                if cands:
                    _queue_fill(cands)
                    log(f"[compress] {len(cands)} 檔待壓，啟用 {len(lanes)} 條編碼線：{','.join(lanes)}")
                    threads = []
                    for enc_key in lanes:
                        t = threading.Thread(
                            target=_lane,
                            args=(enc_key, state, ffmpeg, ffprobe, settings_file),
                            daemon=True, name=f"compress-{enc_key}")
                        t.start()
                        threads.append(t)
                    for t in threads:
                        t.join()
                    _queue_clear()
            except Exception as e:
                _last_error = f"{type(e).__name__}: {e}"
                log(f"[compress] loop error: {_last_error}")
            finally:
                # Consume the force flag only once this pass's batch (if any)
                # has fully drained -- clearing it earlier would let the lane
                # threads' own gate re-check slam shut mid-batch.
                if was_forced:
                    _force_run = False
            _wake.wait(timeout=interval)
            _wake.clear()

    _worker = threading.Thread(target=_coordinator, daemon=True,
                               name="auto-compressor")
    _worker.start()
    return _worker


def trigger_scan(force: bool = False) -> None:
    """Wake the coordinator early. `force=True` (used by the manual "立即開始
    壓縮" button, see api_compress_scan in web_ui.py) makes it run one full
    scan+compress pass even if auto-compress is currently disabled."""
    global _force_run
    if force:
        _force_run = True
    _wake.set()


# ---------------------------------------------------------------------------
# Status (consumed by web_ui /api/compress)
# ---------------------------------------------------------------------------
def nvenc_available(ffmpeg_dir: Optional[Path] = None) -> bool:
    """Backward-compat helper: is the NVIDIA encoder present?"""
    return detect_encoders(ffmpeg_dir).get("nvidia", False)


def get_status(settings_file, state_file,
               ffmpeg_dir: Optional[Path] = None) -> Dict[str, Any]:
    s = load_settings(settings_file)
    state = _load_state(state_file)
    with _lock:
        lanes_snapshot = {k: {kk: vv for kk, vv in v.items() if kk != "proc"}
                          for k, v in _lanes.items()}
        q = dict(_queue)
    now = int(time.time())
    lanes_out = []
    in_progress_frac = 0.0
    for enc_key, ln in lanes_snapshot.items():
        if not ln.get("file"):
            continue
        elapsed = max(0, now - int(ln.get("started_at", now)))
        pct = ln.get("percent", 0.0)
        in_progress_frac += (pct / 100.0)
        lanes_out.append({
            "encoder": enc_key,
            "encoder_label": ENCODERS.get(enc_key, ("", enc_key))[1],
            "file": ln.get("file"),
            "name": Path(ln["file"]).name if ln.get("file") else None,
            "percent": pct,
            "elapsed_sec": elapsed,
        })
    # Overall queue progress: fully-done files + fraction of in-flight ones.
    queue = None
    if q.get("active") or q.get("total"):
        total = max(0, int(q.get("total", 0)))
        done = max(0, int(q.get("done", 0)))
        processed = min(total, done + in_progress_frac)
        queue = {
            "total": total,
            "done": done,
            "claimed": int(q.get("claimed", 0)),
            "remaining": max(0, total - q.get("claimed", 0)),
            "active_lanes": len(lanes_out),
            "percent": round((processed / total * 100.0) if total else 0.0, 1),
        }
    # Backward-compatible single "current" = first active lane.
    current = None
    if lanes_out:
        first = lanes_out[0]
        current = {"file": first["file"], "percent": first["percent"],
                   "started_at": now - first["elapsed_sec"],
                   "elapsed_sec": first["elapsed_sec"]}
    st = state.get("stats", {})
    det = detect_encoders(ffmpeg_dir)
    return {
        "settings": s,
        "running": _worker is not None and _worker.is_alive(),
        "paused": _paused,
        "current": current,
        "lanes": lanes_out,
        "queue": queue,
        "detected_encoders": [
            {"key": k, "label": ENCODERS[k][1], "encoder": ENCODERS[k][0],
             "available": det.get(k, False), "hardware": ENCODERS[k][2]}
            for k in ENCODERS
        ],
        "active_encoder_keys": available_encoder_keys(s, ffmpeg_dir),
        "last_scan_at": int(_last_scan_at) if _last_scan_at else None,
        "last_error": _last_error,
        "note": _runtime_note,
        "gpu_available": det.get("nvidia", False),
        "stats": {
            "files": st.get("files", 0),
            "in_bytes": st.get("in_bytes", 0),
            "out_bytes": st.get("out_bytes", 0),
            "saved_bytes": max(0, st.get("in_bytes", 0) - st.get("out_bytes", 0)),
        },
        "history": list(reversed(state.get("history", [])[-10:])),
    }
