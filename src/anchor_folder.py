"""
src/anchor_folder.py — 推導每位主播「實際」的錄影資料夾 / 錄影檔（純邏輯，無 IO）。

背景：web_ui 以前用 `<save_path>/<平台>直播/<URL_config 的主播名>` 去「猜」資料夾，
但 recorder（main.py 約 1165–1275 行）真正的路徑規則是：

    <video_save_path 或 <exe 目錄>/downloads>/<platform>
        [/<clean_name(anchor)>]          保存文件夹是否以作者区分（預設 是）
        [/<YYYY-MM-DD>]                  保存文件夹是否以时间区分
        [/<title>_<anchor> 或 /<YYYY-MM-DD>_<title>]   保存文件夹是否以标题区分（有標題時）
        /<clean_name(anchor)>_[<title>_]<YYYY-MM-DD_HH-MM-SS>[_NNN].<ext>

platform 字串是 main.py 自己的（'抖音直播'、'SOOP'、'TikTok直播'…，不一定以「直播」結尾），
主播名經過 clean_name（非法字元換底線、全形括號轉半形、可選去 emoji），且沒填「主播:」時
用直播間抓到的暱稱。所以猜名稱常常猜錯 → 資料夾不存在 → disk_bytes 永遠 0。

新做法以 DB 為主：recording_sessions 每列有 live_url / platform / file_path，
從 file_path 反推主播那一層資料夾（不依賴 config.ini 的儲存路徑設定，因此相對路徑、
預設路徑、磁碟代號大小寫不一致都不影響）：

  * 檔名一定以 `<anchor>_` 開頭，而「以作者区分」時的主播資料夾名稱就是同一個 anchor 字串，
    且它的上一層是 platform 資料夾。所以在 file_path 的上層各段中，由右往左找
    「檔名以 `<段名>_` 開頭、且上一段等於 DB 的 platform」的那一段，就是主播資料夾。
    日期資料夾 / 標題資料夾（`<date>_<title>`、`<title>_<anchor>`）都不會符合。
  * 找不到 → 這場是「沒有以作者区分」錄的（多位主播共用同一個資料夾），不能用資料夾大小，
    改成「該場錄影檔所在資料夾裡、檔名以該場前綴開頭的檔案」。前綴 = 檔名到時間戳
    `YYYY-MM-DD_HH-MM-SS` 為止，因此同時涵蓋：
      - 分段檔 `_000.ts` `_001.ts` …（DB 只記第一段）
      - 轉檔後的 `.mp4`（main.py converts_mp4：同 stem 換副檔名）
      - H.265 壓縮後 `<stem>_hevc.mp4` 與壓縮中的 `<stem>_hevc.part.<enc>.mp4`
        （src/compressor.py：`path.stem + suffix + ".mp4"`，suffix 預設 `_hevc`、可設定；
        壓縮成功後刪原檔，所以 DB 的 file_path 本身可能已不存在，但前綴不變）
  * DB 推不出來（沒有任何帶 file_path 的紀錄）才退回名稱猜測，並套用跟 main.py
    一模一樣的 clean_name 規則與 main.py 的 platform 資料夾名稱。

本模組只做字串運算；列目錄 / stat / 快取都在 web_ui.py。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# main.py 的 clean_name（逐字複製；main.py 匯入時會連帶載入 httpx / spider /
# 全域設定讀取等副作用，所以不能直接 from main import clean_name）
# ---------------------------------------------------------------------------
_RSTR = r"[\/\\\:\*\？?\"\<\>\|&#.。,， ~！· ]"   # = main.py rstr

# = src/utils.py remove_emojis 的 pattern（utils.py 匯入 execjs，web 側不能直接 import）
_EMOJI_RE = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002702-\U000027B0"
    "]+",
    flags=re.UNICODE,
)


def clean_name(input_text: str, clean_emoji: bool = True) -> str:
    """與 main.py clean_name 相同的規則（clean_emoji 對應 config「是否去除名称中的表情符号」）。"""
    cleaned = re.sub(_RSTR, "_", (input_text or "").strip()).strip("_")
    cleaned = cleaned.replace("（", "(").replace("）", ")")
    if clean_emoji:
        cleaned = _EMOJI_RE.sub("_", cleaned).strip("_")
    return cleaned or "空白昵称"


# web_ui.guess_platform 的短名稱 → main.py 實際用的 platform（= 平台資料夾名稱）。
# 沒列出的一律是「短名稱 + 直播」（抖音 → 抖音直播）。
_PLATFORM_FOLDER_EXCEPTIONS = {
    "17Live": "17Live", "Acfun": "Acfun", "CHZZK": "CHZZK", "FlexTV": "FlexTV",
    "LiveMe": "LiveMe", "PandaTV": "PandaTV", "Picarto": "Picarto",
    "PopkonTV": "PopkonTV", "SOOP": "SOOP", "ShowRoom": "ShowRoom",
    "TwitCasting": "TwitCasting", "TwitchTV": "TwitchTV", "VV星球": "VV星球",
    "WinkTV": "WinkTV", "Youtube": "Youtube", "千度热播": "千度热播",
    "浪Live": "浪Live", "Faceit": "faceit", "Shopee": "shopee",
}


def platform_folder_name(platform: Optional[str]) -> str:
    """web_ui 的平台短名稱 → main.py 的平台資料夾名稱。"""
    if not platform or platform == "其他":
        return "未知平台"
    return _PLATFORM_FOLDER_EXCEPTIONS.get(platform, f"{platform}直播")


# ---------------------------------------------------------------------------
# 路徑字串處理（Windows / POSIX 都吃：反斜線、混用分隔符、磁碟代號、UNC）
# ---------------------------------------------------------------------------
def split_path(path: str) -> Tuple[str, List[str]]:
    """'F:\\rec/抖音直播\\a\\x.ts' -> ('', ['F:', 'rec', '抖音直播', 'a', 'x.ts'])
    回傳 (前綴, 各段)。前綴是 '/'（POSIX 絕對路徑）或 '//'（UNC）或 ''。"""
    s = (path or "").replace("\\", "/")
    prefix = ""
    if s.startswith("//"):
        prefix = "//"
    elif s.startswith("/"):
        prefix = "/"
    parts = [p for p in s.split("/") if p and p != "."]
    return prefix, parts


def join_path(prefix: str, parts: Sequence[str]) -> str:
    return prefix + "/".join(parts)


def path_key(path: str) -> str:
    """比較用的正規化 key（Windows 不分大小寫；分隔符統一）。"""
    prefix, parts = split_path(path)
    return join_path(prefix, parts).casefold()


_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}")
_SEG_TAIL_RE = re.compile(r"_\d{3,}$")


def session_prefix(basename: str) -> str:
    """錄影檔名 → 該場所有衍生檔共用的前綴（到時間戳為止）。
    'A_2026-10-24_12-00-00_000.ts' -> 'A_2026-10-24_12-00-00'"""
    last = None
    for m in _TS_RE.finditer(basename):
        last = m
    if last is not None:
        return basename[:last.end()]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    return _SEG_TAIL_RE.sub("", stem)


def name_matches_prefix(name: str, prefix: str) -> bool:
    """name 是否屬於該場（prefix 之後必須緊接 '.' 或 '_' 或結束，避免誤吃到更長的名稱）。"""
    if not prefix or not name.startswith(prefix):
        return False
    rest = name[len(prefix):]
    return rest == "" or rest[0] in "._"


def derive_from_file_path(file_path: str, platform: Optional[str]) -> Optional[Dict[str, Any]]:
    """單一場錄影的 file_path → 位置描述。

    回傳：
      {"kind": "author", "folder": <主播資料夾>}
      {"kind": "shared", "dir": <該場所在資料夾>, "prefix": <該場檔名前綴>}
      或 None（file_path 不可用）。
    """
    if not file_path:
        return None
    prefix, parts = split_path(file_path)
    if len(parts) < 2:
        return None
    base = parts[-1]
    if "%" in base:  # 未解析的 ffmpeg segment pattern（舊資料），仍可取前綴
        base = re.sub(r"%0?\d*d", "000", base)
    plat_key = (platform or "").casefold()
    # 由右往左找主播資料夾（不含檔名本身；index 0 是磁碟代號/最上層，不可能是主播資料夾）
    for i in range(len(parts) - 2, 0, -1):
        comp = parts[i]
        if not base.startswith(comp + "_"):
            continue
        if plat_key and parts[i - 1].casefold() != plat_key:
            continue
        return {"kind": "author", "folder": join_path(prefix, parts[:i + 1])}
    return {"kind": "shared", "dir": join_path(prefix, parts[:-1]),
            "prefix": session_prefix(base)}


def strip_record_prefix(name: Optional[str]) -> str:
    """'序号12 京圈太子' -> '京圈太子'（main.py 的 record_name 格式）。"""
    if not name:
        return ""
    m = re.match(r"^\s*序号\s*\d+\s+(.+)$", name)
    return m.group(1).strip() if m else name.strip()


def build_locations(rows: Iterable[Sequence[Any]]) -> Dict[str, Dict[str, Any]]:
    """DB 列 → {live_url: location}。

    rows: (live_url, platform, file_path[, anchor_name])，**新到舊排序**（ORDER BY id DESC）。
    location:
      {"source": "db",
       "platform": 最新一筆的 DB platform（平台資料夾名稱，可能 None）,
       "anchor": 最新一筆的主播名（DB 存的是 main.py 的 record_name「序号N 名稱」，
                 這裡去掉「序号N 」；名稱本身已經過 clean_name）,
       "folders": [主播資料夾，新到舊、不重複],     # 以作者区分的場次
       "shared": [(dir, prefix), ...],              # 共用資料夾的場次
       "folder": 代表資料夾（folders[0]，否則最新共用場次的 dir）}
    沒有任何可用 file_path 的 URL 仍會出現（只有 platform），folder 為 None，
    由呼叫端退回名稱猜測。
    """
    out: Dict[str, Dict[str, Any]] = {}
    seen: Dict[str, set] = {}
    for row in rows:
        url, platform, file_path = row[0], row[1], row[2]
        anchor = strip_record_prefix(row[3]) if len(row) > 3 else ""
        if not url:
            continue
        loc = out.get(url)
        if loc is None:
            loc = {"source": "db", "platform": platform or None, "anchor": anchor,
                   "folders": [], "shared": [], "folder": None}
            out[url] = loc
            seen[url] = set()
        else:
            if not loc["platform"] and platform:
                loc["platform"] = platform
            if not loc["anchor"] and anchor:
                loc["anchor"] = anchor
        d = derive_from_file_path(file_path, platform)
        if d is None:
            continue
        if d["kind"] == "author":
            k = path_key(d["folder"])
            if k not in seen[url]:
                seen[url].add(k)
                loc["folders"].append(d["folder"])
        else:
            k = ("s", path_key(d["dir"]), d["prefix"])
            if k not in seen[url]:
                seen[url].add(k)
                loc["shared"].append((d["dir"], d["prefix"]))
    for loc in out.values():
        if loc["folders"]:
            loc["folder"] = loc["folders"][0]
        elif loc["shared"]:
            loc["folder"] = loc["shared"][0][0]
    return out


def guess_location(save_path: str, platform_folder: str, anchor_name: str,
                   rules: Dict[str, bool], clean_emoji: bool = True) -> Optional[Dict[str, Any]]:
    """DB 推不出來時的退路：照 main.py 規則猜。

    以作者区分 → {"kind": "author", "folder": <save>/<platform>/<clean(anchor)>}
    否則       → {"kind": "shared_guess", "folder": <save>/<platform>,
                  "anchor_prefix": "<clean(anchor)>_"}（呼叫端遞迴比對檔名）
    """
    if not anchor_name:
        return None
    prefix, parts = split_path(save_path)
    base = join_path(prefix, parts + [platform_folder])
    name = clean_name(anchor_name, clean_emoji)
    if rules.get("by_author", True):
        return {"kind": "author", "folder": base + "/" + name}
    return {"kind": "shared_guess", "folder": base, "anchor_prefix": name + "_"}


def guess_name_matches(name: str, anchor_prefix: str) -> bool:
    """共用資料夾的猜測比對：`<anchor>_` 開頭且帶時間戳的錄影檔。
    限制：主播 A 與 A_B 在共用資料夾時無法區分（只用於 DB 沒資料的退路）。"""
    return name.startswith(anchor_prefix) and bool(_TS_RE.search(name[len(anchor_prefix):]))


def select_shared_files(listing: Iterable[Dict[str, Any]], prefix: str) -> List[Dict[str, Any]]:
    """從某資料夾的檔案清單（{"name", ...}）挑出屬於該場的檔案。"""
    return [f for f in listing if name_matches_prefix(f.get("name", ""), prefix)]
