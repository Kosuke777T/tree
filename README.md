# tree — 養豚家系図・系統評価ソフト

母系家系図を可視化し、成績評価スコアで優秀系統を特定するデスクトップアプリケーション。

## 必要環境

- Python >= 3.13
- [uv](https://docs.astral.sh/uv/) パッケージマネージャ

## セットアップ

```bash
# 依存パッケージのインストール
uv sync
```

## データ配置

`data/` ディレクトリに以下の Excel ファイルを配置してください:

```
data/
  種付記録/report.XLS
  分娩記録/report.XLS
  死亡記録/report.XLS
  廃豚記録/report.XLS
  子豚記録/PS.xlsx
```

## 起動

```bash
uv run python -m app
```

初回起動時に Excel から SQLite (`tree.db`) への ETL + 成績評価が自動実行されます。
2回目以降は既存 DB を即座に読み込みます。

DB を再構築したい場合は `tree.db` を削除してから再起動してください。

## 画面構成

### 家系図タブ
- 横型家系図（QGraphicsScene/View）
- パン（ドラッグ）、ホイールズーム
- 色分け: 緑=稼働, 灰=死亡, 橙=廃豚, 赤=上位10%
- 検索（部分一致）、「稼働母豚のみ」フィルタ

### 母豚詳細タブ
- 家系図のノードをクリックで自動表示
- サマリ（ステータス、母/父、TotalScore、3軸スコア、順位）
- 産歴別スコアテーブル（12カラム）
- 子豚一覧

## 成績評価ルール

評価ルールの詳細は `docs/母豚成績評価ルール Ver.1.txt` を参照。

- **OWN_W** (離乳 - 里子) を最重要指標として 5 指標の重み付き z スコア
- Shrinkage 補正 (α=3) で産歴数の少ない母豚のスコアを控えめに
- 3 軸評価: Peak (産歴2-3) × 0.35 + Stability × 0.25 + Sustain × 0.25 + OffspringQuality × 0.15

## プロジェクト構成

```
app/
  __main__.py          # エントリポイント
  db/
    connection.py      # SQLite 接続管理
    schema.py          # DDL 定義
  etl/
    loaders.py         # Excel 読み込み
    pipeline.py        # ETL パイプライン
  scoring/
    engine.py          # 成績評価エンジン
  gui/
    main_window.py     # メインウィンドウ
    pedigree_widget.py # 家系図ビュー
    detail_panel.py    # 母豚詳細パネル
docs/
  SPEC.md              # 確定仕様
  SCHEMA.md            # DB 設計
  TODO.md              # 実装チケット
```
