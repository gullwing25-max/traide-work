"""results/summary.md, results/roundtrips.csv, N vs 総損益のPNGチャートを出力する。"""
from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from .fifo_matcher import MatchStats

logger = logging.getLogger(__name__)

HOLD_DAY_BUCKETS = [
    ("当日", 0, 0),
    ("1日", 1, 1),
    ("2-3日", 2, 3),
    ("4-7日", 4, 7),
    ("8-14日", 8, 14),
    ("15-30日", 15, 30),
    ("31日+", 31, None),
]


def _bucket_label(hold_days: float) -> str:
    for label, lo, hi in HOLD_DAY_BUCKETS:
        if hi is None:
            if hold_days >= lo:
                return label
        elif lo <= hold_days <= hi:
            return label
    return "不明"


def write_roundtrips_csv(df: pd.DataFrame, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    logger.info("往復明細を出力: %s (%d件)", path, len(df))


def write_pl_chart(df: pd.DataFrame, ns: list[int], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    totals = [df[f"pl_n{n}"].sum(skipna=True) for n in ns]
    actual_total_full = df["actual_pl"].sum()

    # 環境にCJKフォントが無い場合の文字欠けを避けるため、チャート内は英語表記にする
    # (詳細な注記・日本語の解説はsummary.md側に記載)。
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(ns, totals, marker="o", label="Counterfactual (exit at N business days)")
    ax.axhline(actual_total_full, color="gray", linestyle="--", label="Actual (all roundtrips)")
    ax.set_xlabel("N (business days)")
    ax.set_ylabel("Total P&L (JPY)")
    ax.set_title("Total P&L if exited mechanically at N business days")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    logger.info("チャートを出力: %s", path)


def write_summary_md(
    df: pd.DataFrame,
    ns: list[int],
    match_stats: MatchStats,
    annual_rate: float,
    commission_per_roundtrip: float,
    total_roundtrips_before_filter: int,
    path: str | Path,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# 時間切れ決済の反実仮想バックテスト(PoC)サマリー\n")

    lines.append("## 前提・パラメータ\n")
    lines.append(f"- 信用金利(年率、パラメータ): {annual_rate * 100:.2f}%")
    lines.append(f"- 往復あたり手数料(定額パラメータ): {commission_per_roundtrip:.0f}円")
    lines.append(f"- 試したN(営業日): {', '.join(str(n) for n in ns)}")
    lines.append(f"- FIFO突合で得られた往復件数(未突合除外後): {total_roundtrips_before_filter}件\n")

    lines.append("## 未突合・除外\n")
    lines.append(
        f"- 期首持ち越し等で対応する新規買が見つからず除外した返済/現引: "
        f"{match_stats.unmatched_close_count}件, {match_stats.unmatched_close_qty:,.0f}株"
    )
    lines.append(
        f"- 現引(現金引取り、市場売却を伴わないため往復として計上せず除外): "
        f"{match_stats.genbiki_count}件, {match_stats.genbiki_qty:,.0f}株"
    )
    if match_stats.short_open_count:
        lines.append(f"- 信用新規売(空売り、集計対象外): {match_stats.short_open_count}件")
    lines.append("")

    lines.append("## N別の結果\n")
    lines.append(
        "| N | 評価対象件数 | 評価対象割合 | 総損益(評価対象) | 同じ集合での実績損益 | 差分 | 勝率 | 平均保有日数(反実仮想) |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")

    actual_total_all = df["actual_pl"].sum()
    actual_win_rate_all = (df["actual_pl"] > 0).mean() * 100 if len(df) else float("nan")
    actual_avg_hold_all = df["hold_days"].mean() if len(df) else float("nan")
    lines_summary_rows = []
    for n in ns:
        eval_mask = df[f"evaluable_n{n}"]
        n_eval = int(eval_mask.sum())
        pct = 100.0 * n_eval / len(df) if len(df) else 0.0
        cf_total = df.loc[eval_mask, f"pl_n{n}"].sum()
        actual_same_set = df.loc[eval_mask, "actual_pl"].sum()
        diff = cf_total - actual_same_set
        win_rate = (df.loc[eval_mask, f"pl_n{n}"] > 0).mean() * 100 if n_eval else float("nan")
        avg_hold = df.loc[eval_mask, f"hold_days_n{n}"].mean() if n_eval else float("nan")
        lines.append(
            f"| {n} | {n_eval}/{len(df)} | {pct:.1f}% | {cf_total:,.0f} | "
            f"{actual_same_set:,.0f} | {diff:,.0f} | {win_rate:.1f}% | {avg_hold:.1f} |"
        )
        lines_summary_rows.append((n, cf_total))
    lines.append("")
    lines.append(
        f"参考: 全往復({len(df)}件)の実績損益合計={actual_total_all:,.0f}円, "
        f"実績勝率={actual_win_rate_all:.1f}%, 実績平均保有日数={actual_avg_hold_all:.1f}日\n"
    )

    lines.append("## 保有日数バケット別の実績損益\n")
    df_b = df.copy()
    df_b["bucket"] = df_b["hold_days"].map(_bucket_label)
    order = [b[0] for b in HOLD_DAY_BUCKETS]
    grp = df_b.groupby("bucket")["actual_pl"].agg(["count", "sum", "mean"]).reindex(order)
    lines.append("| 保有日数 | 件数 | 損益合計 | 平均損益 |")
    lines.append("|---|---|---|---|")
    for bucket, row in grp.iterrows():
        if pd.isna(row["count"]) or row["count"] == 0:
            lines.append(f"| {bucket} | 0 | - | - |")
        else:
            lines.append(
                f"| {bucket} | {int(row['count'])} | {row['sum']:,.0f} | {row['mean']:,.0f} |"
            )
    lines.append("")

    lines.append("## 単調性 / 凸性の傾向(参考)\n")
    vals = [v for _, v in lines_summary_rows]
    diffs = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
    monotonic_inc = all(d >= 0 for d in diffs)
    monotonic_dec = all(d <= 0 for d in diffs)
    if monotonic_inc:
        shape = "Nに対して単調増加"
    elif monotonic_dec:
        shape = "Nに対して単調減少"
    else:
        shape = "単調ではない(増減が入れ替わる)"
    lines.append(f"- 総損益(評価対象のみ)のN間の変化: {shape}")
    lines.append(
        "- 注意: これは6通りのNを試した上での観察であり、多重比較の問題がある。"
        "「最良のNを選ぶ」ためのものではなく、傾向(単調/凸)を見るための参考情報。\n"
    )

    lines.append("## 計算上の前提・限界(必読)\n")
    lines.append(
        "- **PTS実行 vs 東証終値**: 実際の決済の約8割はPTS(私設取引システム)で行われているが、"
        "反実仮想の決済価格には東証(取引所)の終値を使っている。PTSと東証で価格が異なることは"
        "普通にあり、この差は無視できない可能性がある。"
    )
    lines.append(
        "- **J-Quants Freeプランのデータ範囲制約**: 取得できた日足は2025-10-01頃〜2026-06-14頃"
        "(実際の範囲はログ参照)に限られる。この範囲を超えてN営業日後の価格が必要な往復は"
        "「評価対象外」としており、上表の評価対象割合を参照すること。"
    )
    lines.append(
        "- **信用金利の近似**: 実績の手数料/諸経費等をそのまま使わず、年利"
        f"{annual_rate * 100:.2f}%(パラメータ)× 建玉金額 × 保有日数(カレンダー日数)/365 で近似している。"
        "実際の金利計算方式(日割り計算の基準日、税金等)とは一致しない。"
    )
    lines.append(
        "- **現引の扱い**: 現引(信用の現金引取り)は市場での売却を伴わないため決済損益が"
        "報告されない。ここではFIFOキューからは除去するが、経済的な決済ではないため往復"
        "レコードとしては出力していない(件数・株数は上記「未突合・除外」を参照)。"
    )
    lines.append(
        "- **保有日数の営業日カウント**: 実績決済がN営業日より前かどうかの判定には、"
        "土日のみを除く簡易カウント(pandasのbusiness day)を使っており、日本の祝日は"
        "考慮していない。数営業日のずれが生じる可能性がある。"
    )
    lines.append(
        "- **サンプルサイズと多重比較**: 往復件数が数千件規模でNを6通り試しているため、"
        "個々のN間の差分を過度に解釈しないこと。"
    )
    lines.append(
        "- これはPoCであり、投資判断のための結論を出すものではない。データ範囲を広げた"
        "上での再検証が必要。"
    )

    path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("サマリーを出力: %s", path)
