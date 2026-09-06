#!/usr/bin/env python3
"""合成データによるパイプラインの自己検証(pytest不使用、素のassertのみ)。

実データ・JQUANTS_API_KEYは不要。CSVローダー→FIFO突合→反実仮想→レポート出力までを
合成データで一気通貫にテストする。

使い方: python3 tests/test_pipeline.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.counterfactual import compute_counterfactual
from src.csv_loader import load_sbi_trades
from src.fifo_matcher import match_fifo
from src.price_index import PriceIndex
from src.report import write_pl_chart, write_roundtrips_csv, write_summary_md


def row(date, name, code, kind, qty, px, fee, tax, settle_date, amount):
    return f"{date},{name},{code},東証,{kind},2026/07/31,一般,課税,{qty},{px},{fee},{tax},{settle_date},{amount}"


def build_fake_csv(path: Path, calendar: pd.DatetimeIndex) -> tuple[pd.Timestamp, pd.Timestamp]:
    header = "約定日,銘柄,銘柄コード,市場,取引,期限,預り,課税,約定数量,約定単価,手数料/諸経費等,税額,受渡日,受渡金額/決済損益"
    lines = ["照会条件", "期間:2026/01/01〜2026/06/30", "", header]

    # シナリオ1: 単純な全量決済(実績900円)
    lines.append(row("2026/01/05", "テストA", "1234", "信用新規買(1815)", 100, 1000, 0, 0, "2026/01/07", 0))
    lines.append(row("2026/01/07", "テストA", "1234", "信用返済売(2237)", 100, 1010, 100, 0, "2026/01/09", 900))

    # シナリオ2: 部分決済(2つの新規買を1つの返済売でまたぐ)
    lines.append(row("2026/01/08", "テストA", "1234", "信用新規買(1815)", 100, 1000, 0, 0, "2026/01/10", 0))
    lines.append(row("2026/01/09", "テストA", "1234", "信用新規買(1815)", 100, 1050, 0, 0, "2026/01/13", 0))
    lines.append(row("2026/01/12", "テストA", "1234", "信用返済売(2237)", 150, 1100, 150, 0, "2026/01/14", 1500))

    # シナリオ3: 現引(実現損益なし扱い)
    lines.append(row("2026/01/10", "テストB", "5555", "信用新規買(1815)", 50, 2000, 0, 0, "2026/01/14", 0))
    lines.append(row("2026/01/14", "テストB", "5555", "現引", 50, 2000, 0, 0, "2026/01/16", 100000))

    # シナリオ4: 未突合(期首持ち越し想定)
    lines.append(row("2026/01/15", "テストC", "9999", "信用返済売(2237)", 100, 500, 50, 0, "2026/01/19", 200))

    # シナリオ5: 無視すべき行
    lines.append(row("2026/01/16", "テストD", "4444", "株式現物買", 10, 300, 0, 0, "2026/01/20", -3000))
    lines.append(row("2026/01/17", "テストE", "7777", "信用新規売", 20, 800, 0, 0, "2026/01/21", 0))

    # シナリオ6: 長期保有、価格カレンダー範囲内(反実仮想が計算できるケース)
    open_b, close_b = calendar[0], calendar[-1]
    lines.append(row(open_b.strftime("%Y/%m/%d"), "テストF", "5678", "信用新規買(1815)", 200, 1000, 0, 0, "2026/01/07", 0))
    lines.append(row(close_b.strftime("%Y/%m/%d"), "テストF", "5678", "信用返済売(2237)", 200, 1105, 200, 0, "2026/02/04", -500))

    # シナリオ7: 価格カレンダー範囲外になるケース(評価不能を確認)
    open_c = calendar[-2]
    lines.append(row(open_c.strftime("%Y/%m/%d"), "テストG", "3333", "信用新規買(1815)", 30, 1500, 0, 0, "2026/01/31", 0))
    lines.append(row("2026/06/15", "テストG", "3333", "信用返済売(2237)", 30, 1400, 30, 0, "2026/06/17", -200))

    path.write_bytes(("\n".join(lines) + "\n").encode("cp932"))
    return open_b, open_c


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bars_dir = tmp_path / "bars"
        results_dir = tmp_path / "results"
        bars_dir.mkdir()
        csv_path = tmp_path / "fake_trades.csv"

        calendar = pd.bdate_range("2026-01-05", "2026-02-02")
        for i, d in enumerate(calendar):
            price = 1000 + 5 * i
            pd.DataFrame(
                [{"code": "56780", "date": d.strftime("%Y%m%d"), "open": price, "high": price, "low": price, "close": price}]
            ).to_parquet(bars_dir / f"{d.strftime('%Y%m%d')}.parquet", index=False)

        open_b, open_c = build_fake_csv(csv_path, calendar)

        trades = load_sbi_trades(str(csv_path))
        roundtrips, stats = match_fifo(trades)

        assert len(roundtrips) == 5, len(roundtrips)
        assert stats.unmatched_close_count == 1 and stats.unmatched_close_qty == 100
        assert stats.genbiki_count == 1 and stats.genbiki_qty == 50
        assert stats.short_open_count == 1

        r1 = roundtrips[(roundtrips["code"] == "1234") & (roundtrips["open_px"] == 1000) & (roundtrips["close_px"] == 1010)]
        assert len(r1) == 1 and abs(r1.iloc[0]["actual_pl"] - 900) < 1e-6

        r2a = roundtrips[(roundtrips["code"] == "1234") & (roundtrips["open_px"] == 1000) & (roundtrips["close_px"] == 1100)]
        r2b = roundtrips[(roundtrips["code"] == "1234") & (roundtrips["open_px"] == 1050) & (roundtrips["close_px"] == 1100)]
        assert len(r2a) == 1 and abs(r2a.iloc[0]["qty"] - 100) < 1e-6 and abs(r2a.iloc[0]["actual_pl"] - 1000) < 1e-6
        assert len(r2b) == 1 and abs(r2b.iloc[0]["qty"] - 50) < 1e-6 and abs(r2b.iloc[0]["actual_pl"] - 500) < 1e-6

        # 現引は経済的な決済ではないため往復レコードとしては出力されない
        r3 = roundtrips[roundtrips["code"] == "5555"]
        assert len(r3) == 0, len(r3)

        price_index = PriceIndex(cache_dir=str(bars_dir))
        ns = [3, 5, 10, 15, 20, 25]
        result = compute_counterfactual(roundtrips, price_index, ns=ns, annual_rate=0.028, commission_per_roundtrip=100.0)

        rb = result[result["code"] == "5678"].iloc[0]
        assert price_index.calendar_position(open_b) == 0
        for n in [3, 5, 10, 15, 20]:
            assert rb[f"evaluable_n{n}"]
            exit_price = 1000 + 5 * n
            calendar_days = (calendar[n] - open_b).days
            interest = (1000 * 200) * 0.028 * calendar_days / 365.0
            expected = (exit_price - 1000) * 200 - 100.0 - interest
            assert abs(rb[f"pl_n{n}"] - expected) < 1e-6, (n, rb[f"pl_n{n}"], expected)
        assert rb["evaluable_n25"] and rb["note_n25"] == "actual_before_n"
        assert abs(rb["pl_n25"] - rb["actual_pl"]) < 1e-6

        rc = result[result["code"] == "3333"].iloc[0]
        assert price_index.calendar_position(open_c) == len(calendar) - 2
        for n in ns:
            assert not rc[f"evaluable_n{n}"], (n, rc[f"note_n{n}"])

        write_roundtrips_csv(result, results_dir / "roundtrips.csv")
        write_pl_chart(result, ns, results_dir / "pl_vs_n.png")
        write_summary_md(
            result, ns, stats, annual_rate=0.028, commission_per_roundtrip=100.0,
            total_roundtrips_before_filter=len(roundtrips), path=results_dir / "summary.md",
        )
        assert (results_dir / "roundtrips.csv").exists()
        assert (results_dir / "pl_vs_n.png").exists()
        assert (results_dir / "summary.md").exists()

    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
