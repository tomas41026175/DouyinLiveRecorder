"""
query_danmaku.py  ── 直播彈幕（弹幕）查詢 CLI

放在專案根目錄（與 main.py 同層）。讀取 config/danmaku.db（由 web_ui 的彈幕
擷取層寫入）。

使用範例：
    python query_danmaku.py --anchors                     列出有彈幕的主播
    python query_danmaku.py --anchor "京圈太子"           查某主播的彈幕
    python query_danmaku.py --keyword "晚安"              關鍵字搜尋
    python query_danmaku.py --anchor "京圈太子" --since 2026-06-01
    python query_danmaku.py --keyword "抽獎" --export logs/dm.csv
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Resolve the same DB web_ui uses: recorder root (parent) if that looks like the
# install dir, else this folder.
_SELF = Path(__file__).resolve().parent


def _resolve_db() -> Path:
    for base in (_SELF.parent, _SELF):
        cand = base / "config" / "danmaku.db"
        if cand.exists():
            return cand
    return _SELF / "config" / "danmaku.db"


sys.path.insert(0, str(_SELF))
from src.danmaku_store import DanmakuStore  # noqa: E402


def _parse_dt(s: str) -> int:
    # accept ISO 'T' separator too (e.g. 2026-06-24T20:00:00)
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m"):
        try:
            return int(datetime.strptime(s, fmt).timestamp())
        except ValueError:
            continue
    raise SystemExit(f"無法解析時間：{s}（用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM）")


def main() -> None:
    p = argparse.ArgumentParser(description="直播彈幕查詢")
    p.add_argument("--anchors", action="store_true", help="列出有彈幕的主播與則數")
    p.add_argument("--anchor", help="某位主播的彈幕")
    p.add_argument("--url", help="用直播間 URL 篩選")
    p.add_argument("--keyword", help="內容關鍵字搜尋")
    p.add_argument("--user", help="發言者暱稱篩選")
    p.add_argument("--since", help="起始時間 YYYY-MM-DD[ HH:MM]")
    p.add_argument("--until", help="結束時間 YYYY-MM-DD[ HH:MM]")
    p.add_argument("--limit", type=int, default=200, help="最多幾則（預設 200）")
    p.add_argument("--asc", action="store_true", help="由舊到新排序（預設新到舊）")
    p.add_argument("--export", help="匯出 CSV 路徑")
    p.add_argument("--subtitle", metavar="VIDEO",
                   help="為某支錄影產生彈幕字幕（.ass+.srt 旁載）。需搭配 --start；"
                        "時間對齊用 --start（影片開始時間 ISO）與 --duration（秒）")
    p.add_argument("--start", help="影片開始時間 ISO，例 2026-06-24T20:00:00")
    p.add_argument("--duration", type=int, help="影片長度（秒），用來過濾範圍")
    p.add_argument("--with-user", action="store_true", help="SRT 字幕含發言者名稱")
    args = p.parse_args()

    db = _resolve_db()
    if not db.exists():
        print(f"找不到彈幕資料庫：{db}\n（尚未擷取過彈幕，或路徑不同）")
        return
    store = DanmakuStore(db)

    if args.anchors:
        rows = store.anchors()
        if not rows:
            print("目前沒有任何彈幕紀錄。")
            return
        print(f"{'主播':<28}{'則數':<8}{'最後時間':<20}")
        print("-" * 60)
        for a in rows:
            last = (datetime.fromtimestamp(a["last_ts"]).strftime("%Y-%m-%d %H:%M")
                    if a["last_ts"] else "-")
            print(f"{(a['anchor_name'] or '?')[:26]:<28}{a['count']:<8}{last:<20}")
        return

    since = _parse_dt(args.since) if args.since else None
    until = _parse_dt(args.until) if args.until else None
    filters = dict(anchor=args.anchor, url=args.url, keyword=args.keyword,
                   user=args.user, since=since, until=until)

    if args.subtitle:
        if not args.start:
            raise SystemExit("--subtitle 需搭配 --start（影片開始時間 ISO）")
        from src.danmaku_subtitle import build_ass, build_srt, sidecar_paths
        start_epoch = _parse_dt(args.start)
        s_until = (start_epoch + args.duration + 5) if args.duration else None
        rows = store.query(anchor=args.anchor, url=args.url,
                           since=start_epoch, until=s_until,
                           limit=5000, order="asc")
        if not rows:
            print("這段時間沒有彈幕可用。")
            return
        ass = build_ass(rows, start_epoch, duration=args.duration)
        srt = build_srt(rows, start_epoch, duration=args.duration,
                        with_user=args.with_user)
        ass_path, srt_path = sidecar_paths(args.subtitle)
        Path(ass_path).write_text(ass, encoding="utf-8-sig")
        Path(srt_path).write_text(srt, encoding="utf-8-sig")
        print(f"已產生 {len(rows)} 則彈幕字幕：\n  {ass_path}\n  {srt_path}")
        return

    if args.export:
        out = store.export_csv(args.export, **filters)
        print(f"已匯出：{out}（{store.count(**filters)} 則）")
        return

    rows = store.query(limit=args.limit, order="asc" if args.asc else "desc",
                       **filters)
    total = store.count(**filters)
    print(f"符合條件共 {total} 則，顯示 {len(rows)} 則：")
    print("-" * 90)
    for r in rows:
        t = datetime.fromtimestamp(r["ts"]).strftime("%m-%d %H:%M:%S")
        user = (r["user"] or "")[:12]
        print(f"{t}  [{(r['anchor_name'] or '')[:10]}] {user}: {r['content']}")


if __name__ == "__main__":
    main()
