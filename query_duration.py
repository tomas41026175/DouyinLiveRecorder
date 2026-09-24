"""
query_duration.py  ── 主播錄製時長查詢 CLI

放在專案根目錄（與 main.py 同層），錄製過程中或結束後都能執行。

使用範例：
    python query_duration.py --top 10
    python query_duration.py --anchor "京圈太子"
    python query_duration.py --since 2026-05-01
    python query_duration.py --export logs/duration_report.csv
    python query_duration.py --today
"""
from __future__ import annotations

import argparse
from datetime import datetime

from src import common
from src.duration_tracker import get_tracker


def _fmt(seconds: int) -> str:
    return common.fmt_duration(seconds)


def main() -> None:
    p = argparse.ArgumentParser(description="主播錄製時長查詢")
    p.add_argument("--top", type=int, default=0, help="列出總時長前 N 名的主播")
    p.add_argument("--anchor", help="列出某位主播的歷次錄製")
    p.add_argument("--since", help="YYYY-MM-DD，只統計這天之後的紀錄")
    p.add_argument("--today", action="store_true", help="只看今天")
    p.add_argument("--month", help="YYYY-MM，只看該月")
    p.add_argument("--export", help="匯出 CSV 路徑")
    args = p.parse_args()

    since: datetime | None = None
    if args.today:
        since = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    elif args.month:
        since = datetime.strptime(args.month, "%Y-%m")
    elif args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d")

    t = get_tracker()

    if args.export:
        out = t.export_csv(args.export, since=since)
        print(f"已匯出：{out}")
        return

    if args.anchor:
        rows = t.sessions_for(args.anchor)
        total = t.total_seconds(args.anchor, since=since)
        print(f"主播：{args.anchor}    累積：{_fmt(total)}    場次：{len(rows)}")
        print("-" * 100)
        print(f"{'開始':<20}{'結束':<20}{'時長':<12}{'畫質':<8}{'狀態':<10}")
        for r in rows:
            end = r.end.strftime("%Y-%m-%d %H:%M") if r.end else "錄製中"
            print(f"{r.start.strftime('%Y-%m-%d %H:%M'):<20}"
                  f"{end:<20}{_fmt(r.duration_sec or 0):<12}"
                  f"{(r.quality or ''):<8}{(r.finished_reason or ''):<10}")
        return

    # default：top N
    top = args.top or 20
    rows = t.total_by_anchor(since=since)[:top]
    label = f"自 {since.date()} 起" if since else "全部歷史"
    print(f"主播錄製總時長排行（{label}）  總人數：{len(rows)}")
    print("-" * 60)
    print(f"{'排名':<6}{'主播':<30}{'時長':<14}{'場次':<6}")
    for i, (name, dur, cnt) in enumerate(rows, 1):
        print(f"{i:<6}{name[:28]:<30}{_fmt(dur):<14}{cnt:<6}")


if __name__ == "__main__":
    main()
