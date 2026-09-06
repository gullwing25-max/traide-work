#!/usr/bin/env python3
"""日足の取得(日付ごと)。data/bars/YYYYMMDD.parquet にキャッシュし、再実行時はスキップする。

使い方:
    JQUANTS_API_KEY=xxx python scripts/fetch_bars.py \
        --start 2025-10-01 --end 2026-06-14

Freeプランのデータ範囲は変動しうるので、決め打ちせず境界を実際に叩いて確認する
(--probe-only で境界確認だけ行うこともできる)。
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.jquants_client import JQuantsClient  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def business_days(start: str, end: str) -> list[str]:
    idx = pd.bdate_range(start=start, end=end)
    return [d.strftime("%Y%m%d") for d in idx]


def probe_range(client: JQuantsClient, start: str, end: str) -> None:
    """開始日・終了日それぞれ1日だけ叩いて、実際にデータが返ってくるか確認する。"""
    for label, date in [("開始日", start), ("終了日", end)]:
        d = pd.Timestamp(date).strftime("%Y%m%d")
        df = client.get_daily_bars_cached(d)
        status = f"{len(df)}銘柄" if not df.empty else "0件(非営業日 or 範囲外)"
        logger.info("境界確認 [%s] %s -> %s", label, d, status)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-10-01")
    ap.add_argument("--end", default="2026-06-14")
    ap.add_argument("--cache-dir", default="data/bars")
    ap.add_argument("--probe-only", action="store_true", help="境界日の確認だけ行い終了する")
    args = ap.parse_args()

    client = JQuantsClient(cache_dir=args.cache_dir)

    probe_range(client, args.start, args.end)
    if args.probe_only:
        return

    dates = business_days(args.start, args.end)
    logger.info("対象営業日候補: %d日 (%s 〜 %s)", len(dates), dates[0], dates[-1])

    fetched_dates: list[str] = []
    empty_dates: list[str] = []
    skipped_cached = 0

    for i, date in enumerate(dates, start=1):
        already_cached = client.cache_path(date).exists()
        df = client.get_daily_bars_cached(date)
        if already_cached:
            skipped_cached += 1
        if df.empty:
            empty_dates.append(date)
        else:
            fetched_dates.append(date)
        if i % 10 == 0 or i == len(dates):
            logger.info("進捗: %d/%d (取得済みだったためスキップ: %d)", i, len(dates), skipped_cached)

    logger.info("=" * 60)
    logger.info("取得完了")
    logger.info("データあり: %d日", len(fetched_dates))
    logger.info("データなし(非営業日/範囲外): %d日", len(empty_dates))
    if fetched_dates:
        logger.info(
            "実際にデータが取得できた範囲: %s 〜 %s",
            min(fetched_dates),
            max(fetched_dates),
        )
    else:
        logger.warning("データが1日も取得できませんでした。APIキー・範囲を確認してください。")


if __name__ == "__main__":
    main()
