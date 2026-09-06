#!/usr/bin/env python3
"""FIFO突合 -> 反実仮想計算 -> レポート出力までを一気通貫で実行する。

事前に scripts/fetch_bars.py で data/bars/ に日足キャッシュを作っておくこと。

使い方:
    python scripts/run_backtest.py --csv SaveFile_000001_004203.csv
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.csv_loader import load_sbi_trades  # noqa: E402
from src.counterfactual import DEFAULT_NS, compute_counterfactual  # noqa: E402
from src.fifo_matcher import match_fifo  # noqa: E402
from src.price_index import PriceIndex  # noqa: E402
from src.report import write_pl_chart, write_roundtrips_csv, write_summary_md  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="SBI証券の約定履歴CSV")
    ap.add_argument("--bars-dir", default="data/bars")
    ap.add_argument("--results-dir", default="results")
    ap.add_argument(
        "--ns", default=",".join(str(n) for n in DEFAULT_NS), help="試すNのリスト(カンマ区切り)"
    )
    ap.add_argument("--annual-rate", type=float, default=0.028, help="信用金利の年率パラメータ")
    ap.add_argument(
        "--commission-per-roundtrip", type=float, default=0.0, help="往復あたり手数料の定額パラメータ"
    )
    args = ap.parse_args()

    ns = [int(x) for x in args.ns.split(",") if x.strip()]

    trades = load_sbi_trades(args.csv)
    roundtrips, match_stats = match_fifo(trades)
    total_roundtrips = len(roundtrips)

    price_index = PriceIndex(cache_dir=args.bars_dir)
    result = compute_counterfactual(
        roundtrips,
        price_index,
        ns=ns,
        annual_rate=args.annual_rate,
        commission_per_roundtrip=args.commission_per_roundtrip,
    )

    results_dir = Path(args.results_dir)
    write_roundtrips_csv(result, results_dir / "roundtrips.csv")
    write_pl_chart(result, ns, results_dir / "pl_vs_n.png")
    write_summary_md(
        result,
        ns,
        match_stats,
        annual_rate=args.annual_rate,
        commission_per_roundtrip=args.commission_per_roundtrip,
        total_roundtrips_before_filter=total_roundtrips,
        path=results_dir / "summary.md",
    )

    logger.info("完了。%s を参照。", results_dir)


if __name__ == "__main__":
    main()
