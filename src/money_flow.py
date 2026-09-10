"""J-Quantsを使わず、yfinance(Yahoo Finance)経由で個別銘柄の日次資金流入量をトラックする。

定義: 日次の売買代金(終値×出来高)を、その日が前日比で値上がりなら「流入」、
値下がりなら「流出」とみなして符号を付ける(Chaikin Money Flow的な近似)。
実際の売り方・買い方の別を約定単位で特定しているわけではない、あくまで
株価×出来高から推定した近似値であることに注意。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

logger = logging.getLogger(__name__)

_YF_SUFFIX = ".T"  # 東証銘柄のyfinanceティッカーサフィックス


def to_yf_ticker(code: str) -> str:
    """SBI CSVの銘柄コード(4桁 or 5桁)をyfinanceティッカー(4桁+.T)に変換する。"""
    code = str(code).strip()
    if len(code) == 5 and code.endswith("0"):
        code = code[:4]
    return f"{code}{_YF_SUFFIX}"


def extract_codes_from_trades(trades: pd.DataFrame) -> list[str]:
    """約定履歴DataFrameから一意な銘柄コード(4桁数字)を抽出する。"""
    codes = trades["銘柄コード"].astype(str).str.strip()
    codes = codes[codes.str.fullmatch(r"\d{4,5}")]
    codes = codes.map(lambda c: c[:4] if len(c) == 5 else c)
    uniq = sorted(codes.unique())
    logger.info("約定履歴から%d銘柄を抽出しました", len(uniq))
    return uniq


def fetch_history_yf(ticker: str, start: str, end: str) -> pd.DataFrame:
    """yfinanceで日足OHLCVを取得する。0件なら空DataFrameを返す。"""
    import yfinance as yf

    df = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=False)
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = df.reset_index()
    df.columns = [str(c).lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df[["date", "open", "high", "low", "close", "volume"]]


def get_history_cached(
    code: str,
    start: str,
    end: str,
    cache_dir: str | Path,
    fetch_fn: Callable[[str, str, str], pd.DataFrame] = fetch_history_yf,
    rate_limit_seconds: float = 0.0,
) -> pd.DataFrame:
    """キャッシュ(data/money_flow/{code}.parquet)があればそれを使い、無ければ取得して保存する。

    キャッシュは銘柄ごとに取得できた全期間を保持し、要求範囲でフィルタして返す。
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{code}.parquet"

    if path.exists():
        df = pd.read_parquet(path)
    else:
        ticker = to_yf_ticker(code)
        if rate_limit_seconds > 0:
            time.sleep(rate_limit_seconds)
        df = fetch_fn(ticker, start, end)
        df.to_parquet(path, index=False)
        if df.empty:
            logger.info("%s (%s): データなし", code, ticker)
        else:
            logger.info("%s (%s): %d日分を取得・キャッシュ", code, ticker, len(df))

    if df.empty:
        return df
    mask = (df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))
    return df.loc[mask].reset_index(drop=True)


def compute_money_flow(df: pd.DataFrame, code: str, ma_window: int = 20) -> pd.DataFrame:
    """日足OHLCVから符号付き日次資金流入額・累積・移動合計を計算する。

    - turnover_yen: 終値×出来高(売買代金)
    - sign: 前日比 値上がり=+1 / 値下がり=-1 / 変化なし・初日=0
    - money_flow_yen: sign × turnover_yen
    - cum_money_flow_yen: 期間内の累積
    - ma{N}_money_flow_yen: 直近N営業日の移動合計
    """
    out = df.sort_values("date").reset_index(drop=True).copy()
    out["code"] = code
    out["turnover_yen"] = out["close"] * out["volume"]

    prev_close = out["close"].shift(1)
    sign = pd.Series(0, index=out.index, dtype="int64")
    sign[out["close"] > prev_close] = 1
    sign[out["close"] < prev_close] = -1
    out["sign"] = sign

    out["money_flow_yen"] = out["sign"] * out["turnover_yen"]
    out["cum_money_flow_yen"] = out["money_flow_yen"].cumsum()
    out[f"ma{ma_window}_money_flow_yen"] = (
        out["money_flow_yen"].rolling(window=ma_window, min_periods=1).sum()
    )

    cols = [
        "code", "date", "close", "volume", "turnover_yen", "sign",
        "money_flow_yen", "cum_money_flow_yen", f"ma{ma_window}_money_flow_yen",
    ]
    return out[cols]


def write_money_flow_csv(df: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    logger.info("日次資金流入量を出力: %s (%d行)", path, len(df))


def write_money_flow_chart(df: pd.DataFrame, path: str | Path, top_n: int = 5) -> None:
    """累積資金流入額の絶対値が大きい上位top_n銘柄について、推移を折れ線で出力する。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    ranking = df.groupby("code")["money_flow_yen"].sum().abs().sort_values(ascending=False)
    top_codes = list(ranking.head(top_n).index)

    fig, ax = plt.subplots(figsize=(8, 5))
    for code in top_codes:
        sub = df[df["code"] == code].sort_values("date")
        ax.plot(sub["date"], sub["cum_money_flow_yen"], marker="", label=code)
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_xlabel("Date")
    ax.set_ylabel("Cumulative money flow (JPY)")
    ax.set_title(f"Cumulative money flow (top {len(top_codes)} by |total flow|)")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    logger.info("チャートを出力: %s", path)


def write_money_flow_summary_md(
    df: pd.DataFrame,
    path: str | Path,
    start: str,
    end: str,
    ma_window: int = 20,
    no_data_codes: list[str] | None = None,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# 日次資金流入量トラッキング サマリー\n")
    lines.append("## 前提\n")
    lines.append(
        "- データ取得元: yfinance (Yahoo Finance)。J-Quantsは使用していない。"
    )
    lines.append(
        "- 資金流入量の定義: 日次売買代金(終値×出来高)に、前日比 値上がり日は+、"
        "値下がり日は-の符号を付けたもの(Chaikin Money Flow的な近似)。"
        "実際の買い方・売り方を約定単位で特定しているわけではない推定値。"
    )
    lines.append(f"- 対象期間: {start} 〜 {end}\n")

    if not len(df):
        lines.append("対象データがありませんでした。\n")
        path.write_text("\n".join(lines), encoding="utf-8")
        return

    lines.append("## 銘柄別サマリー(期間合計の資金流入額が大きい順)\n")
    grp = df.groupby("code").agg(
        営業日数=("date", "count"),
        期間合計流入額=("money_flow_yen", "sum"),
        平均売買代金=("turnover_yen", "mean"),
        直近終値=("close", "last"),
    )
    grp = grp.sort_values("期間合計流入額", key=lambda s: s.abs(), ascending=False)
    lines.append("| 銘柄コード | 営業日数 | 期間合計流入額(円) | 平均売買代金(円) | 直近終値 |")
    lines.append("|---|---|---|---|---|")
    for code, row in grp.iterrows():
        lines.append(
            f"| {code} | {int(row['営業日数'])} | {row['期間合計流入額']:,.0f} | "
            f"{row['平均売買代金']:,.0f} | {row['直近終値']:,.1f} |"
        )
    lines.append("")

    lines.append(f"## 直近{ma_window}営業日の資金流入額(移動合計、最新日時点)\n")
    latest = df.sort_values("date").groupby("code").tail(1)
    latest = latest.sort_values(f"ma{ma_window}_money_flow_yen", key=lambda s: s.abs(), ascending=False)
    lines.append(f"| 銘柄コード | 最新日 | 直近{ma_window}営業日流入額(円) | 累積流入額(円) |")
    lines.append("|---|---|---|---|")
    for _, row in latest.iterrows():
        date_str = pd.Timestamp(row["date"]).date().isoformat()
        lines.append(
            f"| {row['code']} | {date_str} | {row[f'ma{ma_window}_money_flow_yen']:,.0f} | "
            f"{row['cum_money_flow_yen']:,.0f} |"
        )
    lines.append("")

    if no_data_codes:
        lines.append("## データが取得できなかった銘柄\n")
        lines.append(
            "yfinanceで日足が取得できなかった(上場廃止・コード変更・期間外等の可能性): "
            + ", ".join(no_data_codes)
        )
        lines.append("")

    lines.append("## 計算上の前提・限界\n")
    lines.append(
        "- あくまで株価×出来高から推定した近似値であり、実際の投資家別売買動向"
        "(個人/外国人/機関 等)や信用取引の需給とは別物。"
    )
    lines.append(
        "- yfinance経由のデータは無調整の出来高・終値をそのまま使用しており、"
        "配当落ち・株式分割等の調整方法はJ-Quantsと一致しない場合がある。"
    )
    lines.append("- 投資判断のための結論を示すものではない。")

    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("サマリーを出力: %s", path)
