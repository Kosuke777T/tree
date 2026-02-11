# SCHEMA.md — DB設計（SQLite）& ETL方針

---

## 1. ER概要

```
sows (母豚マスタ)
  ├──< breeding_records (種付記録)    ... 1:N (産歴ごと)
  ├──< farrowing_records (分娩記録)   ... 1:N (産歴ごと)
  ├──< piglets (子豚記録)             ... 1:N (母親タトゥー)
  ├──< death_records (死亡記録)       ... 1:0..1
  └──< cull_records (廃豚記録)        ... 1:0..1

sows.dam_id  ──> sows.individual_id   (母系自己参照)
sows.sire_id ──> TEXT                  (父は文字列のみ)
piglets.dam_id ──> sows.individual_id
```

---

## 2. テーブル定義

### 2.1 sows（母豚マスタ）

母豚の基本情報。子豚記録の W 繰り上げ分 + 種付/分娩記録に登場する全個体。

| カラム | 型 | 制約 | 説明 |
|--------|-----|------|------|
| individual_id | TEXT | PK | 個体番号（例: TBA00002, 5390EGVL） |
| source_piglet_no | TEXT | NULLABLE | 元の子豚№（繰り上げ元。外部導入母豚はNULL） |
| dam_id | TEXT | FK → sows, NULLABLE | 母親の個体番号 |
| sire_id | TEXT | NULLABLE | 父親識別子（精液名、マスタ管理しない） |
| birth_date | DATE | NULLABLE | 生年月日 |
| rank | TEXT | NULLABLE | W/A/B/C |
| teat_score | INTEGER | NULLABLE | 乳評価（1/2/3、ランクAのみ有効） |
| remarks | TEXT | NULLABLE | 備考（遺伝的欠点メモ） |
| status | TEXT | NOT NULL DEFAULT 'active' | active / dead / culled |

**インデックス**: `dam_id`, `status`

### 2.2 piglets（子豚記録）

全子豚の記録。繰り上げ母豚の原情報もここに残す。

| カラム | 型 | 制約 | 説明 |
|--------|-----|------|------|
| piglet_no | TEXT | PK | 子豚№（例: A00001） |
| birth_date | DATE | NULLABLE | 生年月日 |
| rank | TEXT | NULLABLE | W/A/B/C |
| teat_score | INTEGER | NULLABLE | 乳評価 |
| remarks | TEXT | NULLABLE | 備考 |
| shipment_dest | TEXT | NULLABLE | 出荷先 |
| ps_shipment | TEXT | NULLABLE | PS出荷（○/肉/W） |
| shipment_date | DATE | NULLABLE | 出荷日 |
| dam_id | TEXT | FK → sows, NULLABLE | 母親タトゥー |
| sire_id | TEXT | NULLABLE | 父親タトゥー |
| shipment_age | INTEGER | NULLABLE | 出荷日齢 |

**インデックス**: `dam_id`, `ps_shipment`

### 2.3 breeding_records（種付記録）

| カラム | 型 | 制約 | 説明 |
|--------|-----|------|------|
| id | INTEGER | PK AUTOINCREMENT | |
| individual_id | TEXT | FK → sows, NOT NULL | 個体番号 |
| parity | INTEGER | NOT NULL | 産歴 |
| breeding_date | DATE | NULLABLE | 種付日 |
| breeding_type | TEXT | NULLABLE | 種付（方法等） |
| sire_first | TEXT | NULLABLE | ♂1回目 |
| return_to_estrus | TEXT | NULLABLE | 再帰 |
| age_days | INTEGER | NULLABLE | 日齢 |
| status | TEXT | NULLABLE | 状態/妊娠豚 |

**UNIQUE**: `(individual_id, parity)`
**インデックス**: `individual_id`

### 2.4 farrowing_records（分娩記録）

成績評価の主データソース。

| カラム | 型 | 制約 | 説明 |
|--------|-----|------|------|
| id | INTEGER | PK AUTOINCREMENT | |
| individual_id | TEXT | FK → sows, NOT NULL | 個体番号 |
| parity | INTEGER | NOT NULL | 産歴 |
| farrowing_date | DATE | NULLABLE | 分娩日 |
| total_born | INTEGER | NULLABLE | 総産子 |
| born_alive | INTEGER | NULLABLE | 生存産子数 |
| stillborn | INTEGER | NULLABLE | 死産 |
| mummified | INTEGER | NULLABLE | 黒子 |
| foster | INTEGER | NULLABLE | +/-（里子数、±で記録） |
| weaning_date | DATE | NULLABLE | 離乳日 |
| weaned | INTEGER | NULLABLE | 離乳頭数(W) |
| deaths | INTEGER | NULLABLE | 死亡数 |
| mortality_rate | REAL | NULLABLE | 死亡率 |
| nursing_days | INTEGER | NULLABLE | 哺乳期間 |
| farrowing_interval | INTEGER | NULLABLE | 分娩間隔 |

**UNIQUE**: `(individual_id, parity)`
**インデックス**: `individual_id`

### 2.5 death_records（死亡記録）

| カラム | 型 | 制約 | 説明 |
|--------|-----|------|------|
| id | INTEGER | PK AUTOINCREMENT | |
| individual_id | TEXT | FK → sows, NOT NULL | 個体番号 |
| event_date | DATE | NULLABLE | 淘汰日 |
| cause | TEXT | NULLABLE | 死亡原因1 |
| age_days | INTEGER | NULLABLE | 日齢 |
| parity | INTEGER | NULLABLE | 産歴 |

**インデックス**: `individual_id`

### 2.6 cull_records（廃豚記録）

| カラム | 型 | 制約 | 説明 |
|--------|-----|------|------|
| id | INTEGER | PK AUTOINCREMENT | |
| individual_id | TEXT | FK → sows, NOT NULL | 個体番号 |
| event_date | DATE | NULLABLE | 淘汰日 |
| cause | TEXT | NULLABLE | 淘汰原因1 |
| non_productive_days | INTEGER | NULLABLE | 非生産日数 |
| parity | INTEGER | NULLABLE | 産歴 |

**インデックス**: `individual_id`

### 2.7 parity_scores（産歴別スコア ─ 計算テーブル）

ETL/評価エンジンが書き込む。再計算時は TRUNCATE → INSERT。

| カラム | 型 | 制約 | 説明 |
|--------|-----|------|------|
| individual_id | TEXT | FK → sows, NOT NULL | |
| parity | INTEGER | NOT NULL | |
| own_weaned | REAL | | OWN_W = W − F |
| own_rate | REAL | | OWN_RATE = OWN_W / W |
| z_own_weaned | REAL | | zスコア（Shrinkage後） |
| z_live_born | REAL | | |
| z_total_born | REAL | | |
| z_stillborn | REAL | | 符号反転済 |
| z_own_rate | REAL | | |
| parity_score | REAL | | 重み付き合計 |

**PK**: `(individual_id, parity)`

### 2.8 sow_scores（母豚総合スコア ─ 計算テーブル）

| カラム | 型 | 制約 | 説明 |
|--------|-----|------|------|
| individual_id | TEXT | PK, FK → sows | |
| peak | REAL | | 産歴2〜3の ParityScore 平均 |
| stability | REAL | | 分散の符号反転 |
| sustain | REAL | | 後半平均 − 前半平均 |
| total_score | REAL | | Peak×0.4 + Stability×0.3 + Sustain×0.3 |
| rank_all | INTEGER | NULLABLE | 全頭順位 |
| rank_active | INTEGER | NULLABLE | 稼働母豚順位 |

---

## 3. 母系ツリー構築用ビュー

```sql
-- 母系の再帰CTE
WITH RECURSIVE lineage AS (
    SELECT individual_id, dam_id, sire_id, 0 AS generation
    FROM sows
    WHERE individual_id = :root_id
  UNION ALL
    SELECT s.individual_id, s.dam_id, s.sire_id, l.generation + 1
    FROM sows s
    JOIN lineage l ON s.dam_id = l.individual_id
)
SELECT * FROM lineage;
```

---

## 4. ETL方針

### 4.1 全体フロー

```
Excel 5ファイル
    │
    ▼
[1] pandas.read_excel() でDataFrame化
    │
    ▼
[2] カラム名の正規化（日本語→英語マッピング）
    │
    ▼
[3] 型変換・バリデーション
    │  - 日付列 → date型
    │  - 数値列 → int/float（空欄→NULL）
    │  - 文字列トリム
    │
    ▼
[4] sows マスタ構築（以下の順序で UPSERT）
    │  4a. 種付/分娩/死亡/廃豚から individual_id を収集 → sows INSERT
    │  4b. 子豚記録の ps_shipment='W' を検出 → 'TB'+子豚№ で sows INSERT
    │  4c. 子豚記録の 母親タトゥー/父親タトゥー → sows.dam_id/sire_id UPDATE
    │
    ▼
[5] 各レコードテーブルへ INSERT
    │  - breeding_records ← 種付記録
    │  - farrowing_records ← 分娩記録
    │  - death_records ← 死亡記録  → sows.status='dead' UPDATE
    │  - cull_records ← 廃豚記録   → sows.status='culled' UPDATE
    │  - piglets ← 子豚記録
    │
    ▼
[6] 成績評価エンジン実行
    │  - farrowing_records から基礎指標計算
    │  - 産歴別 zスコア → Shrinkage → parity_scores INSERT
    │  - 3軸集約 → sow_scores INSERT
    │  - rank_all / rank_active 算出
    │
    ▼
[7] DB完成 → GUI表示
```

### 4.2 カラム名マッピング（日本語 → DB）

| Excel日本語名 | DBカラム名 |
|---------------|-----------|
| 個体番号 | individual_id |
| 産歴 | parity |
| 種付日 | breeding_date |
| 種付 | breeding_type |
| ♂　1回目 | sire_first |
| 再帰 | return_to_estrus |
| 日齢 | age_days |
| 状態 / 妊娠豚 | status |
| 分娩日 | farrowing_date |
| 総産子 | total_born |
| 生存 | born_alive |
| 死産 | stillborn |
| 黒子 | mummified |
| +/- | foster |
| 離乳日 | weaning_date |
| 離乳 | weaned |
| 死亡 | deaths |
| 死亡率 | mortality_rate |
| 哺乳期間 | nursing_days |
| 分娩間隔 | farrowing_interval |
| 淘汰日 | event_date |
| 死亡原因 1 | cause |
| 淘汰原因1 | cause |
| 非生産日数 | non_productive_days |
| 子豚№ | piglet_no |
| 生年月日 | birth_date |
| ランク | rank |
| 乳評価 | teat_score |
| 備考 | remarks |
| 出荷先 | shipment_dest |
| PS出荷 | ps_shipment |
| 出荷日 | shipment_date |
| 母親タトゥー | dam_id |
| 父親タトゥー | sire_id |
| 出荷日齢 | shipment_age |

### 4.3 べき等性

- ETLは**全削除→全挿入**（SQLite TRUNCATE相当）で冪等に
- 計算テーブル（parity_scores, sow_scores）は毎回再計算
- 理由: データ量が数千行規模で、差分更新の複雑さに見合わないため

### 4.4 ライブラリ

| 用途 | ライブラリ |
|------|-----------|
| Excel読み込み (.xls) | `xlrd` |
| Excel読み込み (.xlsx) | `openpyxl` |
| DataFrame操作 | `pandas` |
| DB | `sqlite3`（標準ライブラリ） |
