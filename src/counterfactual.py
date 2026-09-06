"""「建日からN営業日後の東証終値で決済していたら」の反実仮想損益を計算する。

- 実際の決済が N営業日より前なら、その建玉は影響を受けない(実績をそのまま使う)
- そうでなければ、建日からN営業日後の終値で決済したと仮定して損益を再計算する
- コスト:
    - 信用金利 = 建玉金額(建値×株数) × 年利 / 365 × 保有日数(カレンダー日数)
    - 売買手数料は往復あたり定額のパラメータ
- 価格データが範囲外/欠損で参照できない場合は評価不能(NaN)としてマークする
"""
from __future__ import annotations

import logging

import pandas as pd

from .price_index import PriceIndex

logger = logging.getLogger(__name__)

DEFAULT_NS = [3, 5, 10, 15, 20, 25]
DEFAULT_ANNUAL_INTEREST_RATE = 0.028
DEFAULT_COMMISSION_PER_ROUNDTRIP = 0.0


def _trading_days_held(open_date: pd.Timestamp, close_date: pd.Timestamp) -> int:
    """土日を除いた営業日ベースの保有日数の近似値(日本の祝日は考慮しない簡易カウント)。"""
    if close_date <= open_date:
        return 0
    return len(pd.bdate_range(start=open_date, end=close_date)) - 1


def compute_counterfactual(
    roundtrips: pd.DataFrame,
    price_index: PriceIndex,
    ns: list[int] = DEFAULT_NS,
    annual_rate: float = DEFAULT_ANNUAL_INTEREST_RATE,
    commission_per_roundtrip: float = DEFAULT_COMMISSION_PER_ROUNDTRIP,
) -> pd.DataFrame:
    df = roundtrips.copy()
    df["trading_days_held"] = [
        _trading_days_held(o, c) for o, c in zip(df["open_date"], df["close_date"])
    ]

    for n in ns:
        pls: list[float] = []
        evaluables: list[bool] = []
        notes: list[str] = []
        hold_days_eff: list[float] = []

        for _, row in df.iterrows():
            if row["trading_days_held"] < n:
                pls.append(row["actual_pl"])
                evaluables.append(True)
                notes.append("actual_before_n")
                hold_days_eff.append(row["hold_days"])
                continue

            exit_date = price_index.nth_trading_day_after(row["open_date"], n)
            if exit_date is None:
                pls.append(float("nan"))
                evaluables.append(False)
                notes.append("no_future_price_data")
                hold_days_eff.append(float("nan"))
                continue

            exit_price = price_index.close(row["code"], exit_date)
            if exit_price is None:
                pls.append(float("nan"))
                evaluables.append(False)
                notes.append("no_price_for_code")
                hold_days_eff.append(float("nan"))
                continue

            calendar_days = (exit_date - row["open_date"]).days
            position_value = row["open_px"] * row["qty"]
            interest = position_value * annual_rate * calendar_days / 365.0
            pl = (exit_price - row["open_px"]) * row["qty"] - commission_per_roundtrip - interest
            pls.append(pl)
            evaluables.append(True)
            notes.append("counterfactual")
            hold_days_eff.append(calendar_days)

        df[f"pl_n{n}"] = pls
        df[f"evaluable_n{n}"] = evaluables
        df[f"note_n{n}"] = notes
        df[f"hold_days_n{n}"] = hold_days_eff

        n_eval = sum(evaluables)
        logger.info(
            "N=%d: 評価対象 %d/%d件 (%.1f%%)",
            n,
            n_eval,
            len(df),
            100.0 * n_eval / len(df) if len(df) else 0.0,
        )

    return df
