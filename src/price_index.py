"""キャッシュ済み日足(data/bars/*.parquet)から、銘柄コード×日付の終値ルックアップと
取引カレンダー(実際にデータが存在する日の集合)を構築する。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def normalize_code(code: str) -> str:
    """SBI CSVの4桁銘柄コードをJ-Quantsの5桁コード(4桁+末尾0)に変換する。すでに5桁ならそのまま。"""
    code = str(code).strip()
    if len(code) == 4:
        return code + "0"
    return code


class PriceIndex:
    def __init__(self, cache_dir: str | Path = "data/bars"):
        self.cache_dir = Path(cache_dir)
        self._by_code: dict[str, pd.Series] = {}  # code -> Series(index=date(Timestamp), value=close)
        self.calendar: list[pd.Timestamp] = []
        self._load()

    def _load(self) -> None:
        files = sorted(self.cache_dir.glob("*.parquet"))
        if not files:
            logger.warning("価格キャッシュが空です: %s", self.cache_dir)
            return

        frames = []
        for f in files:
            df = pd.read_parquet(f)
            if df.empty:
                continue
            frames.append(df)

        if not frames:
            logger.warning("価格キャッシュに有効なデータがありません")
            return

        all_df = pd.concat(frames, ignore_index=True)
        all_df["date"] = pd.to_datetime(all_df["date"], format="%Y%m%d", errors="coerce")
        all_df = all_df.dropna(subset=["date"])

        self.calendar = sorted(all_df["date"].unique())
        logger.info(
            "取引カレンダーを構築: %d営業日 (%s 〜 %s)",
            len(self.calendar),
            pd.Timestamp(self.calendar[0]).date(),
            pd.Timestamp(self.calendar[-1]).date(),
        )

        for code, g in all_df.groupby("code"):
            s = g.set_index("date")["close"].sort_index()
            s = s[~s.index.duplicated(keep="last")]
            self._by_code[str(code)] = s

        logger.info("価格インデックス構築完了: %d銘柄", len(self._by_code))

    def close(self, code: str, date: pd.Timestamp) -> float | None:
        s = self._by_code.get(normalize_code(code))
        if s is None:
            return None
        val = s.get(pd.Timestamp(date))
        if val is None or pd.isna(val):
            return None
        return float(val)

    def nth_trading_day_after(self, date: pd.Timestamp, n: int) -> pd.Timestamp | None:
        """カレンダー上で date と同じか直後の営業日を0番目として、n営業日後の日付を返す。
        範囲外ならNone。
        """
        if not self.calendar:
            return None
        date = pd.Timestamp(date)
        import bisect

        pos = bisect.bisect_left(self.calendar, date)
        target = pos + n
        if target >= len(self.calendar):
            return None
        return self.calendar[target]

    def calendar_position(self, date: pd.Timestamp) -> int | None:
        if not self.calendar:
            return None
        date = pd.Timestamp(date)
        import bisect

        pos = bisect.bisect_left(self.calendar, date)
        if pos >= len(self.calendar) or self.calendar[pos] != date:
            return None
        return pos
