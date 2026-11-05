"""URL_config.ini 單行的組裝 + 主播名逗號檢查（純邏輯，給 web_ui 用；workbench#107）。

recorder（main.py 讀 URL_config.ini 的迴圈，約 2137 行）用 re.split('[,，]', line)
切整行，再依段數當成「網址[,名稱]」或「畫質,網址[,名稱]」。主播名裡只要有半形或
全形逗號就會多切出段：
- 3 段：被當成「畫質, 網址, 名稱」→ 網址變成「主播: xxx」，這位主播不會被錄；
- 4 段以上：解包丟例外，那一輪清單後面的行都不會被讀到。
main.py 編在 exe 裡，所以由 web_ui 寫檔這端把名稱裡的逗號換成底線（entry_to_line），
讀到既有的壞行則在主播清單標警告（name_has_comma）。
"""
import re
from typing import Any, Mapping, Optional

_NAME_COMMA_RE = re.compile(r"[,，]")   # = main.py 切行用的 '[,，]'


def name_has_comma(name: Optional[str]) -> bool:
    """主播名含半形／全形逗號 → recorder 會把這行讀錯（主播清單標警告）。"""
    return bool(_NAME_COMMA_RE.search(name or ""))


def sanitize_anchor_name(name: Optional[str]) -> str:
    """主播名裡的半形／全形逗號換成底線，其他字元原樣保留。"""
    return _NAME_COMMA_RE.sub("_", name or "")


def entry_to_line(entry: Mapping[str, Any]) -> str:
    """{"enabled","quality","url","anchor_name"} → URL_config.ini 一行（原 web_ui.entry_to_line）。"""
    pieces = []
    if entry.get("quality"):
        pieces.append(entry["quality"])
    pieces.append(entry["url"])
    if entry.get("anchor_name"):
        pieces.append(f"主播: {sanitize_anchor_name(entry['anchor_name'])}")
    line = ",".join(pieces)
    if not entry.get("enabled", True):
        line = "#" + line
    return line
