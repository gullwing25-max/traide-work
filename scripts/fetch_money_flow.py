#!/usr/bin/env python3
"""J-Quantsを使わず、yfinance経由で日次の資金流入量をトラックする。

対象銘柄は約定履歴CSV(SaveFile_*)から自動抽出するか、--codesで直接指定する。

使い方:
    python scripts/fetch_money_flow.py --csv SaveFile_000001_004203.csv \
        --start 2025-10-01 --end 2026-06-14

    python scripts/fetch_money_flow.py --codes 7203,9984,6758 \
        --start 2025-10-01 --end 2026-06-14
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.csv_loader import load_sbi_trades  # noqa: E402
from src.money_flow import (  # noqa: E402
    compute_money_flow,
    extract_codes_from_trades,
    get_history_cached,
    write_money_flow_chart,
    write_money_flow_csv,
    write_money_flow_summary_md,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="SBI証券の約定履歴CSV(銘柄コード抽出元)")
    ap.add_argument("--codes", help="銘柄コードのカンマ区切りリスト(--csvの代わりに直接指定)")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--cache-dir", default="data/money_flow")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--ma-window", type=int, default=20, help="移動合計の営業日数")
    ap.add_argument("--top-n", type=int, default=5, help="チャートに表示する上位銘柄数")
    ap.add_argument(
        "--rate-limit-seconds", type=float, default=0.5, help="銘柄ごとの取得間隔(秒)"
    )
    args = ap.parse_args()

    if not args.csv and not args.codes:
        ap.error("--csv か --codes のどちらかを指定してください")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        trades = load_sbi_trades(args.csv)
        codes = extract_codes_from_trades(trades)

    if not codes:
        logger.warning("対象銘柄が0件のため終了します")
        return

    logger.info("対象銘柄: %d件 (%s)", len(codes), ", ".join(codes))

    frames: list[pd.DataFrame] = []
    no_data_codes: list[str] = []
    for i, code in enumerate(codes, start=1):
        bars = get_history_cached(
            code, args.start, args.end, args.cache_dir, rate_limit_seconds=args.rate_limit_seconds
        )
        if bars.empty:
            no_data_codes.append(code)
            continue
        frames.append(compute_money_flow(bars, code, ma_window=args.ma_window))
        if i % 10 == 0 or i == len(codes):
            logger.info("進捗: %d/%d", i, len(codes))

    if not frames:
        logger.warning("全銘柄でデータが取得できませんでした")
        result = pd.DataFrame()
    else:
        result = pd.concat(frames, ignore_index=True)

    results_dir = Path(args.results_dir)
    write_money_flow_csv(result, results_dir / "money_flow_daily.csv")
    if not result.empty:
        write_money_flow_chart(result, results_dir / "money_flow_top.png", top_n=args.top_n)
    write_money_flow_summary_md(
        result,
        results_dir / "money_flow_summary.md",
        start=args.start,
        end=args.end,
        ma_window=args.ma_window,
        no_data_codes=no_data_codes,
    )

    logger.info("完了。%s を参照。", results_dir)


if __name__ == "__main__":
    main()
