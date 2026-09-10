# 時間切れ決済の反実仮想バックテスト(PoC)

自分の信用取引(買い建てのみ)の約定履歴に対して、「N営業日で機械的に決済していたら
どうだったか」を実データで再計算するPoCパイプライン。

## セットアップ

```bash
pip install -r requirements.txt
export JQUANTS_API_KEY=xxxxx  # J-Quants API V2 (Freeプラン)
```

APIキー・口座情報はコード・ログに含めない。

## 実行手順

### 1. 日足の取得(日付ごと、キャッシュ再開可能)

```bash
python scripts/fetch_bars.py --start 2025-10-01 --end 2026-06-14
```

- `data/bars/YYYYMMDD.parquet` にキャッシュ。既にある日付はスキップされるので再実行して良い。
- Freeプランのレート制限(5req/分)に合わせて12秒間隔でリクエストする。
- 実際に取得できたデータの範囲はログに出る(Freeプランの範囲は変動するため決め打ちしない)。
- `--probe-only` で開始日・終了日だけ確認できる。

### 2. FIFO突合 + 反実仮想計算 + レポート出力

```bash
python scripts/run_backtest.py --csv SaveFile_000001_004203.csv
```

主なオプション:
- `--ns 3,5,10,15,20,25` 試すN(営業日)
- `--annual-rate 0.028` 信用金利の年率パラメータ
- `--commission-per-roundtrip 0` 往復あたり手数料の定額パラメータ

出力:
- `results/summary.md` — N別の総損益・勝率・平均保有日数、保有日数バケット別実績、
  未突合件数、評価対象割合、計算上の前提・限界
- `results/roundtrips.csv` — 往復ごとの実績+各Nの反実仮想明細
- `results/pl_vs_n.png` — N vs 総損益の折れ線グラフ

## 自己検証(合成データ、実データ・APIキー不要)

```bash
python3 tests/test_pipeline.py
```

CSVパース、FIFO突合(全量/部分決済/現引/未突合)、反実仮想の計算(評価対象/評価不能の
境界含む)、レポート出力までを合成データで検証する。

## 設計上の注意

- 取得は銘柄ごとではなく日付ごと(`date`指定)。生存バイアス除去の将来検証にそのまま使える。
- 信用返済売の決済損益(手数料・金利込み)と現物取引の受渡金額を区別して扱う。
- 現引は市場売却を伴わないため決済損益が報告されない。FIFOキューからは除去するが、
  経済的な決済ではないため往復としては計上しない(件数・株数はログ・summary.mdに記録)。
- 期首持ち越し建玉(対応する新規買が履歴にない返済/現引)は除外し、件数・株数をログと
  summary.mdに出す。
- 実際の決済の約8割はPTSだが、反実仮想の決済価格には東証終値を使う。この差は
  無視できない可能性があるため、summary.mdに必ず明記する。
- J-Quants Freeプランのデータ範囲外になる往復は評価対象外とし、割合を明記する。
- Nを6通り試す多重比較の問題があるため、「最良のN」ではなく損益がNに対して単調か
  凸かを見る。

## やらないこと

- 発注・注文APIには触れない(分析専用)
- 結果に対する投資判断のコメントはしない

## 日次資金流入量トラッキング(J-Quants不使用)

J-Quantsを使わず、yfinance(Yahoo Finance、APIキー不要)経由で個別銘柄の
日次資金流入量をトラックするツール。上記のバックテストとは独立した機能。

### 定義

日次売買代金(終値×出来高)に、前日比で値上がり日は`+`、値下がり日は`-`の符号を
付けたもの(Chaikin Money Flow的な近似)。実際の買い方・売り方を約定単位で
特定しているわけではない推定値であることに注意。

### 実行手順

```bash
pip install -r requirements.txt

# 約定履歴CSVに出てくる銘柄を自動抽出してトラック
python scripts/fetch_money_flow.py --csv SaveFile_000001_004203.csv \
    --start 2025-10-01 --end 2026-06-14

# 銘柄コードを直接指定することも可能
python scripts/fetch_money_flow.py --codes 7203,9984,6758 \
    --start 2025-10-01 --end 2026-06-14
```

- 取得結果は銘柄ごとに `data/money_flow/{code}.parquet` にキャッシュされ、再実行時は
  キャッシュを読むだけになる(再取得したい場合はキャッシュファイルを削除する)。
- 出力: `results/money_flow_daily.csv`(日次明細)、
  `results/money_flow_summary.md`(銘柄別サマリー・直近移動合計ランキング)、
  `results/money_flow_top.png`(累積資金流入額 上位銘柄の推移チャート)。

### 自己検証(合成データ、実データ・ネットワーク不要)

```bash
python3 tests/test_money_flow.py
```
