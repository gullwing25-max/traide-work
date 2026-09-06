"""SBI証券 約定履歴CSVのロード。

ファイルはShift-JIS(cp932)。先頭に照会条件のメタ行が数行あり、
`約定日,` で始まる行が本当のヘッダなので、それを探してそこから読む。
"""
from __future__ import annotations

import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "約定日",
    "銘柄",
    "銘柄コード",
    "市場",
    "取引",
    "期限",
    "預り",
    "課税",
    "約定数量",
    "約定単価",
    "手数料/諸経費等",
    "税額",
    "受渡日",
    "受渡金額/決済損益",
]

# 「信用新規買(1815)」のような取引種別末尾の (数字) は約定管理用のコードで
# 種別の意味には関係しないため、突合ロジックでは接頭辞だけを見る。
_TRADE_KIND_RE = re.compile(r"^(?P<kind>[^\(（]+)")


def trade_kind(raw: str) -> str:
    """「信用新規買(1815)」→「信用新規買」のように末尾の(数字)を除いた種別名を返す。"""
    m = _TRADE_KIND_RE.match(str(raw).strip())
    return m.group("kind").strip() if m else str(raw).strip()


def find_header_row(path: str, encoding: str = "cp932") -> int:
    with open(path, encoding=encoding, errors="strict") as f:
        for i, line in enumerate(f):
            if line.startswith("約定日,"):
                return i
    raise ValueError(f"ヘッダ行(約定日,...)が見つかりません: {path}")


def load_sbi_trades(path: str, encoding: str = "cp932") -> pd.DataFrame:
    """SBI証券の約定履歴CSVを読み込み、正規化したDataFrameを返す。"""
    header_row = find_header_row(path, encoding=encoding)
    logger.info("ヘッダ行を検出: %d行目 (%s)", header_row, path)

    df = pd.read_csv(
        path,
        encoding=encoding,
        skiprows=header_row,
        dtype=str,
    )
    df.columns = [c.strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"想定した列が見つかりません: {missing}. 実際の列: {list(df.columns)}")

    df = df[REQUIRED_COLUMNS].copy()

    # 末尾に空行やフッタ(合計行など)が混ざることがあるので、約定日が日付として
    # パースできない行は捨てる。
    df["約定日_dt"] = pd.to_datetime(df["約定日"], format="%Y/%m/%d", errors="coerce")
    df["受渡日_dt"] = pd.to_datetime(df["受渡日"], format="%Y/%m/%d", errors="coerce")
    n_before = len(df)
    df = df[df["約定日_dt"].notna()].copy()
    n_dropped = n_before - len(df)
    if n_dropped:
        logger.info("約定日がパースできない行を除外: %d行", n_dropped)

    df["取引種別"] = df["取引"].map(trade_kind)

    for col in ["約定数量", "約定単価", "手数料/諸経費等", "税額", "受渡金額/決済損益"]:
        df[col] = (
            df[col]
            .astype(str)
            .str.replace(",", "", regex=False)
            .str.replace("　", "", regex=False)
            .str.strip()
        )
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["銘柄コード"] = df["銘柄コード"].astype(str).str.strip()

    df = df.sort_values("約定日_dt").reset_index(drop=True)

    logger.info(
        "読み込み完了: %d行 (%s 〜 %s)",
        len(df),
        df["約定日_dt"].min().date(),
        df["約定日_dt"].max().date(),
    )
    kind_counts = df["取引種別"].value_counts()
    logger.info("取引種別の内訳:\n%s", kind_counts.to_string())

    return df
