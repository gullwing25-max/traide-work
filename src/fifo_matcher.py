"""信用新規買 (open) を 信用返済売/現引 (close) でFIFO突合し、往復レコードを作る。

- 銘柄コードごとにキューを持ち、先入先出しで消す
- 部分決済は株数ベースで分割し、決済損益は約定数量で按分する
- 期首の持ち越し建玉(対応する新規買が履歴に無い返済/現引)は落とすが、
  件数・株数を必ずログに出す
- 現引は「決済損益」が報告されない(売却ではなく現金での引き取りのため)。
  ここでは実現損益0として扱い、その旨をログに残す。この扱いはPoCの簡略化であり
  経済的な損益を正確に表すものではない。
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

import pandas as pd

logger = logging.getLogger(__name__)

OPEN_KIND = "信用新規買"
CLOSE_KINDS = {"信用返済売", "現引"}
IGNORED_KINDS_OF_INTEREST = {"信用新規売"}  # 買い建てのみの想定に反するので出現したらログ


@dataclass
class _Lot:
    open_date: pd.Timestamp
    qty: float
    open_px: float


@dataclass
class MatchStats:
    unmatched_close_count: int = 0
    unmatched_close_qty: float = 0.0
    unmatched_detail: list[dict] = field(default_factory=list)
    genbiki_count: int = 0
    genbiki_qty: float = 0.0
    short_open_count: int = 0
    n_open_rows: int = 0
    n_close_rows: int = 0


def match_fifo(trades: pd.DataFrame) -> tuple[pd.DataFrame, MatchStats]:
    stats = MatchStats()
    queues: dict[str, deque[_Lot]] = {}
    roundtrips: list[dict] = []

    trades = trades.sort_values("約定日_dt", kind="stable").reset_index(drop=True)

    for _, row in trades.iterrows():
        kind = row["取引種別"]
        code = row["銘柄コード"]

        if kind == OPEN_KIND:
            stats.n_open_rows += 1
            q = queues.setdefault(code, deque())
            q.append(_Lot(open_date=row["約定日_dt"], qty=float(row["約定数量"]), open_px=float(row["約定単価"])))
            continue

        if kind in IGNORED_KINDS_OF_INTEREST:
            stats.short_open_count += 1
            continue

        if kind not in CLOSE_KINDS:
            continue  # 現物取引など、信用のFIFOには関係しない

        stats.n_close_rows += 1
        if kind == "現引":
            stats.genbiki_count += 1
            stats.genbiki_qty += float(row["約定数量"])

        q = queues.setdefault(code, deque())
        remaining = float(row["約定数量"])
        row_qty = float(row["約定数量"])
        row_pl = float(row["受渡金額/決済損益"]) if kind == "信用返済売" else 0.0
        close_px = float(row["約定単価"])
        close_date = row["約定日_dt"]

        while remaining > 1e-9 and q:
            lot = q[0]
            matched_qty = min(lot.qty, remaining)
            pl_alloc = row_pl * (matched_qty / row_qty) if row_qty > 0 else 0.0
            roundtrips.append(
                {
                    "open_date": lot.open_date,
                    "close_date": close_date,
                    "code": code,
                    "qty": matched_qty,
                    "open_px": lot.open_px,
                    "close_px": close_px,
                    "hold_days": (close_date - lot.open_date).days,
                    "actual_pl": pl_alloc,
                    "close_kind": kind,
                }
            )
            lot.qty -= matched_qty
            remaining -= matched_qty
            if lot.qty <= 1e-9:
                q.popleft()

        if remaining > 1e-9:
            stats.unmatched_close_count += 1
            stats.unmatched_close_qty += remaining
            stats.unmatched_detail.append(
                {"code": code, "date": close_date, "kind": kind, "qty": remaining}
            )

    if stats.unmatched_close_count:
        logger.warning(
            "未突合の返済/現引 (期首持ち越し建玉と推定): %d件, %s株。除外します。",
            stats.unmatched_close_count,
            f"{stats.unmatched_close_qty:,.0f}",
        )
    if stats.genbiki_count:
        logger.info(
            "現引 %d件 (%s株) はFIFOキューから除外しましたが、実現損益は0として記録しています"
            "(現引には市場価格でのP&Lが発生しないため)。",
            stats.genbiki_count,
            f"{stats.genbiki_qty:,.0f}",
        )
    if stats.short_open_count:
        logger.warning(
            "信用新規売(空売り)が%d件見つかりました。買い建てのみを想定した集計のため無視しています。",
            stats.short_open_count,
        )

    remaining_open_qty = sum(lot.qty for q in queues.values() for lot in q)
    if remaining_open_qty > 1e-9:
        logger.info(
            "データ期間末で未決済の建玉が %s株残っています(往復として出力していません)。",
            f"{remaining_open_qty:,.0f}",
        )

    rt_df = pd.DataFrame(roundtrips)
    logger.info("FIFO突合で作成した往復レコード: %d件", len(rt_df))
    return rt_df, stats
