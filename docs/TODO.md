# TODO.md — 実装チケット（順序付き）

> 各チケットは前のチケットの成果物に依存する。上から順に実装すること。

---

## Phase 1: 基盤 ✅

### T-01: プロジェクト構成とDB初期化 ✅
- [x] pyproject.toml に依存追加（pandas, xlrd, openpyxl, PyQt6）
- [x] `app/` パッケージ構成
  ```
  app/
    __init__.py
    __main__.py
    db/
      schema.py       -- DDL実行
      connection.py    -- SQLite接続管理
    etl/
      loaders.py       -- Excel読み込み（XLS: xlrd, XLSX: openpyxl）
      pipeline.py      -- 統合ETL
    scoring/
      engine.py        -- 全評価ロジック
    gui/
      main_window.py   -- タブUIメインウィンドウ
      pedigree_widget.py -- 家系図ビュー
      detail_panel.py  -- 母豚詳細パネル
  ```
- [x] `db/schema.py` に全CREATE TABLE文（8テーブル）
- [x] DB初期化関数（FK有効化、WALモード）

### T-02: ETL — Excel読み込みパイプライン ✅
- [x] `etl/loaders.py` — 5ファイル各々の読み込み
  - XLS: xlrd (cp932, スパースカラム対応, Excelシリアル日付変換)
  - XLSX: openpyxl (NaT安全処理)
  - カラム位置→DBカラム名マッピング
- [x] `etl/pipeline.py` — 統合ETL
  - sows マスタ構築（個体番号収集 + 子豚W繰り上げ TB+子豚№）
  - 各テーブルへINSERT（冪等: 全削除→全挿入）
  - sows.status 更新（dead/culled）
- [x] 動作確認: breeding=9011, farrowing=7876, deaths=74, culls=1577, piglets=42661, sows=1959

---

## Phase 2: 成績評価エンジン ✅

### T-03: 産歴別基礎指標の計算 ✅
- [x] `scoring/engine.py` — OWN_W, OWN_RATE, LIVE_BORN, TOTAL_BORN, STILLBORN
- [x] W=0 → OWN_RATE=NULL

### T-04: 産歴別zスコア・Shrinkage・ParityScore ✅
- [x] 産歴kごとの平均・SD → zスコア（死産は符号反転）
- [x] Shrinkage α=3
- [x] 重み: OWN_W=0.45, LIVE_BORN=0.25, TOTAL_BORN=0.15, STILLBORN=0.10, OWN_RATE=0.05
- [x] parity_scores テーブルINSERT + 産歴別順位（rank_all / rank_active）

### T-05: 母豚レベル3軸評価 + 順位 ✅
- [x] Peak (産歴2-3平均) ×0.35
- [x] Stability (分散反転) ×0.25
- [x] Sustain (後半-前半) ×0.25
- [x] OffspringQuality (W繰り上げ率×0.6 + PS販売率×0.4) ×0.15
- [x] rank_all / rank_active
- [x] sow_scores テーブルINSERT

---

## Phase 3: 家系図データ構築 ✅

### T-06: 母系ツリー構築ロジック ✅
- [x] sows.dam_id による母系ツリー構築（pedigree_widget内）
- [x] 始祖母豚（dam_id IS NULL）からのBFS展開
- [x] 世代番号算出
- [x] has_active フラグ（bottom-up）

### T-07: 系統（母系ライン）評価 ✅
- [x] 稼働母豚のみフィルタ（系統内に1頭でも稼働がいる枝だけ表示）
- [x] スコア上位10%ハイライト（赤色ノード）

---

## Phase 4: GUI ✅

### T-08: GUI基盤とメインウィンドウ ✅
- [x] PyQt6 Fusion スタイル
- [x] タブ: 家系図 / 母豚詳細
- [x] ステータスバー + プログレスバー
- [x] 起動時ETL（バックグラウンドスレッド）、既存DB時は即表示

### T-09: 家系図ビュー ✅
- [x] QGraphicsScene/View による横型家系図
- [x] パン（ドラッグ移動）、ホイールズーム
- [x] 赤実線=母系接続、青テキスト=♂父親表示
- [x] ノード: 個体番号、産歴、スコア、順位、ステータス/淘汰理由
- [x] 色分け: 緑=稼働, 灰=死亡, 橙=廃豚, 赤=上位10%
- [x] 検索（部分一致）→ フィルタ外なら自動解除してジャンプ
- [x] 「稼働母豚のみ」チェック（デフォルトON）

### T-10: 成績詳細パネル ✅
- [x] ノード選択で自動切替
- [x] 母豚サマリ（ステータス、母/父、TotalScore、3軸、順位）
- [x] 産歴別スコアテーブル（12カラム）
- [x] 子豚一覧テーブル（ランク、乳評価、PS出荷、備考、出荷日齢）

---

## Phase 5: 仕上げ ✅

### T-11: 統合テスト・データ検証 ✅
- [x] 実データ全量ETL + 評価 + GUI表示の通しテスト
- [x] スコア計算の手計算との突合（サンプル数頭）
- [x] エッジケース確認（産歴1回のみ、里子のみ、W=0等）

### T-12: パッケージング・配布 ✅
- [x] pyproject.toml のエントリポイント整備
- [x] README.md に使い方記載
- [x] 実行手順の確認（`uv run python -m app`）
