"""Feature engineering for ML-based sow excellence classification.

Builds a per-parity feature matrix from the database, with:
  Tier 1: Raw farrowing data
  Tier 2: Rolling cumulative stats from prior parities
  Tier 3: Piglet quality metrics (A-rank, W-promotion, defects)
  Tier 4: Dam (mother) genetic proxy features

Also generates the binary `is_excellent` target label.
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd


def _load_farrowing(conn: sqlite3.Connection) -> pd.DataFrame:
    """Load farrowing records as DataFrame."""
    df = pd.read_sql_query(
        """SELECT individual_id, parity, total_born, born_alive,
                  stillborn, mummified, foster, weaned, deaths,
                  mortality_rate, nursing_days, farrowing_interval
           FROM farrowing_records
           ORDER BY individual_id, parity""",
        conn,
    )
    return df


def _build_tier2_rolling(df: pd.DataFrame) -> pd.DataFrame:
    """Tier 2: cumulative stats from *prior* parities (no data leakage)."""
    df = df.sort_values(["individual_id", "parity"]).copy()

    # Expanding (cumulative) stats shifted by 1 so current row is excluded
    grp = df.groupby("individual_id")

    df["avg_born_alive_prev"] = grp["born_alive"].apply(
        lambda s: s.expanding().mean().shift(1)
    ).values
    df["avg_weaned_prev"] = grp["weaned"].apply(
        lambda s: s.expanding().mean().shift(1)
    ).values
    df["avg_stillborn_prev"] = grp["stillborn"].apply(
        lambda s: s.expanding().mean().shift(1)
    ).values
    df["max_born_alive_prev"] = grp["born_alive"].apply(
        lambda s: s.expanding().max().shift(1)
    ).values
    df["prev_parity_count"] = grp.cumcount()  # 0-based count of prior parities

    # Trend: linear regression slope over all prior born_alive values
    def _trend(series: pd.Series) -> pd.Series:
        result = pd.Series(np.nan, index=series.index)
        vals = series.values
        for i in range(len(vals)):
            if i < 2:
                result.iloc[i] = np.nan
                continue
            y = vals[:i].astype(float)
            mask = ~np.isnan(y)
            if mask.sum() < 2:
                result.iloc[i] = np.nan
                continue
            x = np.arange(len(y))[mask]
            y = y[mask]
            slope = np.polyfit(x, y, 1)[0]
            result.iloc[i] = slope
        return result

    df["trend_born_alive"] = grp["born_alive"].transform(_trend)

    return df


def _build_tier3_piglet_quality(conn: sqlite3.Connection,
                                df: pd.DataFrame) -> pd.DataFrame:
    """Tier 3: per-parity piglet quality from piglets table."""
    pig = pd.read_sql_query(
        """SELECT p.dam_id AS individual_id,
                  fr.parity,
                  p.rank, p.teat_score, p.ps_shipment, p.remarks
           FROM piglets p
           JOIN farrowing_records fr
             ON p.dam_id = fr.individual_id
             AND p.birth_date = fr.farrowing_date
           WHERE p.dam_id IS NOT NULL""",
        conn,
    )

    if pig.empty:
        for col in ["a_rank_shipped_count", "a_rank_shipped_ratio",
                     "avg_teat_score_a", "w_promoted_count",
                     "w_promotion_rate", "defect_piglet_count",
                     "defect_piglet_ratio"]:
            df[col] = np.nan
        return df

    # A-rank shipped
    pig["is_a_rank"] = pig["rank"].isin(["A", "B", "C"]).astype(int)
    pig["is_a_shipped"] = ((pig["rank"].isin(["A", "B", "C"])) &
                           (pig["ps_shipment"] == "○")).astype(int)

    # W promotion
    pig["is_w_rank"] = (pig["rank"] == "W").astype(int)
    pig["is_w_promoted"] = ((pig["rank"] == "W") &
                            (pig["ps_shipment"] == "W")).astype(int)

    # Defects (remarks containing 陰部小 or 後足爪)
    pig["is_defect"] = pig["remarks"].fillna("").str.contains(
        "陰部小|後足爪", regex=True
    ).astype(int)

    # Teat score for A-rank piglets
    pig["teat_a"] = pig["teat_score"].where(pig["is_a_rank"] == 1)

    agg = pig.groupby(["individual_id", "parity"]).agg(
        a_rank_shipped_count=("is_a_shipped", "sum"),
        a_rank_total=("is_a_rank", "sum"),
        avg_teat_score_a=("teat_a", "mean"),
        w_promoted_count=("is_w_promoted", "sum"),
        w_rank_total=("is_w_rank", "sum"),
        defect_piglet_count=("is_defect", "sum"),
        piglet_total=("is_a_rank", "count"),
    ).reset_index()

    agg["a_rank_shipped_ratio"] = np.where(
        agg["a_rank_total"] > 0,
        agg["a_rank_shipped_count"] / agg["a_rank_total"],
        np.nan,
    )
    agg["w_promotion_rate"] = np.where(
        agg["w_rank_total"] > 0,
        agg["w_promoted_count"] / agg["w_rank_total"],
        np.nan,
    )
    agg["defect_piglet_ratio"] = np.where(
        agg["piglet_total"] > 0,
        agg["defect_piglet_count"] / agg["piglet_total"],
        np.nan,
    )

    keep_cols = ["individual_id", "parity",
                 "a_rank_shipped_count", "a_rank_shipped_ratio",
                 "avg_teat_score_a",
                 "w_promoted_count", "w_promotion_rate",
                 "defect_piglet_count", "defect_piglet_ratio"]
    agg = agg[keep_cols]

    df = df.merge(agg, on=["individual_id", "parity"], how="left")
    return df


def _build_tier4_dam_genetics(conn: sqlite3.Connection,
                              df: pd.DataFrame) -> pd.DataFrame:
    """Tier 4: dam (mother) performance features."""
    dam_scores = pd.read_sql_query(
        """SELECT s.individual_id AS child_id,
                  sc.total_score AS dam_total_score,
                  sc.peak AS dam_peak
           FROM sows s
           JOIN sow_scores sc ON s.dam_id = sc.individual_id
           WHERE s.dam_id IS NOT NULL""",
        conn,
    )

    # Dam's W promotion rate and avg born_alive
    dam_stats = pd.read_sql_query(
        """SELECT s.individual_id AS child_id,
                  AVG(fr.born_alive) AS dam_avg_born_alive
           FROM sows s
           JOIN farrowing_records fr ON s.dam_id = fr.individual_id
           WHERE s.dam_id IS NOT NULL
           GROUP BY s.individual_id""",
        conn,
    )

    # Dam W promotion rate
    dam_w = pd.read_sql_query(
        """SELECT s2.individual_id AS child_id,
                  CAST(SUM(CASE WHEN p.ps_shipment='W' THEN 1 ELSE 0 END) AS REAL)
                    / NULLIF(SUM(CASE WHEN p.rank='W' THEN 1 ELSE 0 END), 0)
                    AS dam_w_promotion_rate
           FROM sows s2
           JOIN piglets p ON s2.dam_id = p.dam_id
           WHERE s2.dam_id IS NOT NULL
           GROUP BY s2.individual_id""",
        conn,
    )

    # Merge all dam features
    if not dam_scores.empty:
        df = df.merge(
            dam_scores, left_on="individual_id", right_on="child_id",
            how="left"
        ).drop(columns=["child_id"], errors="ignore")

    if not dam_stats.empty:
        df = df.merge(
            dam_stats, left_on="individual_id", right_on="child_id",
            how="left"
        ).drop(columns=["child_id"], errors="ignore")

    if not dam_w.empty:
        df = df.merge(
            dam_w, left_on="individual_id", right_on="child_id",
            how="left"
        ).drop(columns=["child_id"], errors="ignore")

    # Ensure columns exist
    for col in ["dam_total_score", "dam_peak",
                "dam_avg_born_alive", "dam_w_promotion_rate"]:
        if col not in df.columns:
            df[col] = np.nan

    return df


def _build_label(df: pd.DataFrame) -> pd.DataFrame:
    """Generate is_excellent binary label based on 7 criteria within each parity."""
    df = df.copy()

    # Per-parity percentiles (within same parity group)
    grp = df.groupby("parity")

    # Criteria 1-3: higher is better → ≥ 70th percentile
    for col in ["total_born", "born_alive", "weaned"]:
        p70 = grp[col].transform(lambda s: s.quantile(0.7))
        df[f"_crit_{col}"] = (df[col] >= p70).astype(int)

    # Criteria 4-5: lower is better → ≤ 30th percentile
    for col in ["stillborn", "mummified"]:
        p30 = grp[col].transform(lambda s: s.quantile(0.3))
        df[f"_crit_{col}"] = (df[col] <= p30).astype(int)

    # Criteria 6-7: ratios (higher is better)
    for col in ["a_rank_shipped_ratio", "w_promotion_rate"]:
        if col in df.columns:
            p70 = grp[col].transform(lambda s: s.quantile(0.7))
            df[f"_crit_{col}"] = (df[col] >= p70).astype(int)
        else:
            df[f"_crit_{col}"] = 0

    crit_cols = [c for c in df.columns if c.startswith("_crit_")]
    df["_crit_sum"] = df[crit_cols].sum(axis=1)
    df["is_excellent"] = (df["_crit_sum"] >= 4).astype(int)

    # Drop temp columns
    df.drop(columns=crit_cols + ["_crit_sum"], inplace=True)

    return df


# Feature columns used by the model (order matters for SHAP)
FEATURE_COLS = [
    # Tier 1
    "parity", "total_born", "born_alive", "stillborn", "mummified",
    "foster", "weaned", "deaths", "mortality_rate", "nursing_days",
    "farrowing_interval",
    # Tier 2
    "avg_born_alive_prev", "avg_weaned_prev", "avg_stillborn_prev",
    "max_born_alive_prev", "trend_born_alive", "prev_parity_count",
    # Tier 3
    "a_rank_shipped_count", "a_rank_shipped_ratio", "avg_teat_score_a",
    "w_promoted_count", "w_promotion_rate",
    "defect_piglet_count", "defect_piglet_ratio",
    # Tier 4
    "dam_total_score", "dam_peak", "dam_avg_born_alive",
    "dam_w_promotion_rate",
]

# Japanese display names for SHAP plots
FEATURE_NAMES_JA = {
    "parity": "産歴",
    "total_born": "総産子",
    "born_alive": "生存産子",
    "stillborn": "死産",
    "mummified": "黒子",
    "foster": "里子",
    "weaned": "離乳",
    "deaths": "事故頭数",
    "mortality_rate": "事故率",
    "nursing_days": "哺乳日数",
    "farrowing_interval": "分娩間隔",
    "avg_born_alive_prev": "過去平均生存産子",
    "avg_weaned_prev": "過去平均離乳",
    "avg_stillborn_prev": "過去平均死産",
    "max_born_alive_prev": "過去最大生存産子",
    "trend_born_alive": "生存産子トレンド",
    "prev_parity_count": "経験産歴数",
    "a_rank_shipped_count": "A出荷数",
    "a_rank_shipped_ratio": "A出荷率",
    "avg_teat_score_a": "A平均乳評価",
    "w_promoted_count": "W繰上数",
    "w_promotion_rate": "W繰上率",
    "defect_piglet_count": "欠陥子豚数",
    "defect_piglet_ratio": "欠陥子豚率",
    "dam_total_score": "母スコア",
    "dam_peak": "母Peak",
    "dam_avg_born_alive": "母平均生存産子",
    "dam_w_promotion_rate": "母W繰上率",
}


def build_feature_matrix(conn: sqlite3.Connection) -> pd.DataFrame:
    """Build full feature matrix with labels from DB.

    Returns DataFrame with columns:
        individual_id, parity, <FEATURE_COLS>, is_excellent
    """
    df = _load_farrowing(conn)
    if df.empty:
        return df

    df = _build_tier2_rolling(df)
    df = _build_tier3_piglet_quality(conn, df)
    df = _build_tier4_dam_genetics(conn, df)
    df = _build_label(df)

    # Ensure all feature columns exist
    for col in FEATURE_COLS:
        if col not in df.columns:
            df[col] = np.nan

    return df
