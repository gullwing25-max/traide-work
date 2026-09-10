#!/usr/bin/env python3
"""資金流入量トラッキング機能の自己検証(pytest不使用、素のassertのみ)。

ネットワーク・yfinance実アクセス不要。ティッカー変換、銘柄コード抽出、
資金流入量の計算、キャッシュ読み書き、レポート出力を合成データで検証する。

使い方: python3 tests/test_money_flow.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.money_flow import (
    compute_money_flow,
    extract_codes_from_trades,
    get_history_cached,
    to_yf_ticker,
    write_money_flow_chart,
    write_money_flow_csv,
    write_money_flow_summary_md,
)


def test_to_yf_ticker():
    assert to_yf_ticker("7203") == "7203.T"
    assert to_yf_ticker("72030") == "7203.T"
    assert to_yf_ticker(" 9984 ") == "9984.T"


def test_extract_codes_from_trades():
    trades = pd.DataFrame(
        {"銘柄コード": ["7203", "72030", "9984", "7203", "abc", "12"]}
    )
    codes = extract_codes_from_trades(trades)
    assert codes == ["7203", "9984"], codes


def fake_bars(prices: list[float], volumes: list[int], start="2026-01-05") -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=len(prices))
    return pd.DataFrame(
        {
            "date": dates,
            "open": prices,
            "high": [p + 1 for p in prices],
            "low": [p - 1 for p in prices],
            "close": prices,
            "volume": volumes,
        }
    )


def test_compute_money_flow_signs_and_cumsum():
    # 1000 -> 1010 (up) -> 1005 (down) -> 1005 (flat)
    bars = fake_bars([1000, 1010, 1005, 1005], [100, 200, 300, 400])
    out = compute_money_flow(bars, "1234", ma_window=2)

    assert list(out["sign"]) == [0, 1, -1, 0]
    expected_flow = [0.0, 1010 * 200, -(1005 * 300), 0.0]
    assert list(out["money_flow_yen"]) == expected_flow

    expected_cum = []
    running = 0.0
    for v in expected_flow:
        running += v
        expected_cum.append(running)
    assert list(out["cum_money_flow_yen"]) == expected_cum

    # ma_window=2: 直近2営業日の移動合計
    expected_ma2 = [
        expected_flow[0],
        expected_flow[0] + expected_flow[1],
        expected_flow[1] + expected_flow[2],
        expected_flow[2] + expected_flow[3],
    ]
    assert list(out["ma2_money_flow_yen"]) == expected_ma2
    assert (out["code"] == "1234").all()


def test_get_history_cached_writes_and_reads_cache():
    calls = {"n": 0}

    def fake_fetch(ticker, start, end):
        calls["n"] += 1
        return fake_bars([100, 101, 102], [10, 20, 30])

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        df1 = get_history_cached("1234", "2026-01-05", "2026-01-07", cache_dir, fetch_fn=fake_fetch)
        assert len(df1) == 3
        assert calls["n"] == 1
        assert (cache_dir / "1234.parquet").exists()

        # 2回目はキャッシュを読むだけでfetch_fnは呼ばれない
        df2 = get_history_cached("1234", "2026-01-05", "2026-01-06", cache_dir, fetch_fn=fake_fetch)
        assert calls["n"] == 1
        assert len(df2) == 2  # 範囲でフィルタされる


def test_get_history_cached_empty_result():
    def fake_fetch_empty(ticker, start, end):
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        df = get_history_cached("9999", "2026-01-05", "2026-01-07", cache_dir, fetch_fn=fake_fetch_empty)
        assert df.empty
        assert (cache_dir / "9999.parquet").exists()


def test_reports_write_files():
    bars_a = fake_bars([1000, 1010, 1020, 1015], [100, 200, 150, 300])
    bars_b = fake_bars([500, 495, 490, 500], [1000, 900, 800, 1200])
    df = pd.concat(
        [compute_money_flow(bars_a, "1111"), compute_money_flow(bars_b, "2222")],
        ignore_index=True,
    )

    with tempfile.TemporaryDirectory() as tmp:
        results_dir = Path(tmp)
        write_money_flow_csv(df, results_dir / "money_flow_daily.csv")
        write_money_flow_chart(df, results_dir / "money_flow_top.png", top_n=2)
        write_money_flow_summary_md(
            df,
            results_dir / "money_flow_summary.md",
            start="2026-01-05",
            end="2026-01-08",
            ma_window=20,
            no_data_codes=["3333"],
        )
        assert (results_dir / "money_flow_daily.csv").exists()
        assert (results_dir / "money_flow_top.png").exists()
        summary = (results_dir / "money_flow_summary.md").read_text(encoding="utf-8")
        assert "3333" in summary
        assert "1111" in summary and "2222" in summary


def main() -> None:
    test_to_yf_ticker()
    test_extract_codes_from_trades()
    test_compute_money_flow_signs_and_cumsum()
    test_get_history_cached_writes_and_reads_cache()
    test_get_history_cached_empty_result()
    test_reports_write_files()
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
