"""Scoring engine – Ver.1 evaluation rules.

Implements:
  1. Per-parity base indicators (OWN_W, OWN_RATE, LIVE_BORN, TOTAL_BORN, STILLBORN)
  2. Parity-wise z-score standardisation
  3. Shrinkage correction (α=3)
  4. Weighted ParityScore
  5. Three-axis sow-level score (Peak / Stability / Sustain)
  6. Offspring quality (W_RATE / PS_RATE)
  7. Rankings (all sows + active only), both parity-level and sow-level
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

ALPHA = 3  # shrinkage parameter

# ParityScore weights (z_own_weaned excluded; proportionally redistributed)
W_LIVE_BORN = 0.45
W_TOTAL_BORN = 0.27
W_STILLBORN = 0.18
W_OWN_RATE = 0.10

# TotalScore axis weights
W_PEAK = 0.35
W_STABILITY = 0.25
W_SUSTAIN = 0.25
W_OFFSPRING = 0.15

# Offspring quality sub-weights
W_W_RATE = 0.60
W_PS_RATE = 0.40


@dataclass
class ParityRow:
    individual_id: str
    parity: int
    weaned: int | None
    foster: int | None
    born_alive: int | None
    total_born: int | None
    stillborn: int | None
    # computed
    own_w: float | None = None
    own_rate: float | None = None


def _mean_sd(values: list[float]) -> tuple[float, float]:
    if len(values) < 2:
        return (values[0] if values else 0.0), 0.0
    m = sum(values) / len(values)
    var = sum((v - m) ** 2 for v in values) / len(values)
    return m, math.sqrt(var)


def _zscore(value: float | None, mean: float, sd: float,
            invert: bool = False) -> float | None:
    if value is None or sd == 0:
        return 0.0
    z = (value - mean) / sd
    return -z if invert else z


def run_scoring(conn: sqlite3.Connection, progress_cb=None) -> None:
    """Compute all scores and write to parity_scores / sow_scores."""

    def _progress(msg: str):
        if progress_cb:
            progress_cb(msg)

    conn.execute("DELETE FROM parity_scores")
    conn.execute("DELETE FROM sow_scores")
    conn.commit()

    # ── Step 1: Load farrowing data ──
    _progress("基礎指標計算中...")
    rows_raw = conn.execute(
        """SELECT individual_id, parity, weaned, foster,
                  born_alive, total_born, stillborn
           FROM farrowing_records
           ORDER BY individual_id, parity"""
    ).fetchall()

    parity_data: list[ParityRow] = []
    for r in rows_raw:
        pr = ParityRow(
            individual_id=r["individual_id"],
            parity=r["parity"],
            weaned=r["weaned"],
            foster=r["foster"],
            born_alive=r["born_alive"],
            total_born=r["total_born"],
            stillborn=r["stillborn"],
        )
        # OWN_W = W - F
        if pr.weaned is not None:
            f = pr.foster if pr.foster is not None else 0
            pr.own_w = pr.weaned - f
            pr.own_rate = pr.own_w / pr.weaned if pr.weaned > 0 else None
        parity_data.append(pr)

    # ── Step 2: Parity-wise z-scores ──
    _progress("zスコア算出中...")
    # Group by parity
    by_parity: dict[int, list[ParityRow]] = {}
    for pr in parity_data:
        by_parity.setdefault(pr.parity, []).append(pr)

    # Count parities per sow for shrinkage
    sow_n: dict[str, int] = {}
    for pr in parity_data:
        sow_n[pr.individual_id] = sow_n.get(pr.individual_id, 0) + 1

    # Get active sow set
    active_sows = {
        r[0] for r in conn.execute(
            "SELECT individual_id FROM sows WHERE status='active'"
        ).fetchall()
    }

    # Compute z-scores per parity group
    parity_results: list[dict] = []
    for k, group in by_parity.items():
        vals_ow = [p.own_w for p in group if p.own_w is not None]
        vals_lb = [p.born_alive for p in group if p.born_alive is not None]
        vals_tb = [p.total_born for p in group if p.total_born is not None]
        vals_sb = [p.stillborn for p in group if p.stillborn is not None]
        vals_or = [p.own_rate for p in group if p.own_rate is not None]

        m_ow, s_ow = _mean_sd(vals_ow) if vals_ow else (0, 0)
        m_lb, s_lb = _mean_sd(vals_lb) if vals_lb else (0, 0)
        m_tb, s_tb = _mean_sd(vals_tb) if vals_tb else (0, 0)
        m_sb, s_sb = _mean_sd(vals_sb) if vals_sb else (0, 0)
        m_or, s_or = _mean_sd(vals_or) if vals_or else (0, 0)

        for pr in group:
            n = sow_n.get(pr.individual_id, 1)
            shrink = n / (n + ALPHA)

            z_ow = _zscore(pr.own_w, m_ow, s_ow) * shrink
            z_lb = _zscore(pr.born_alive, m_lb, s_lb) * shrink
            z_tb = _zscore(pr.total_born, m_tb, s_tb) * shrink
            z_sb = _zscore(pr.stillborn, m_sb, s_sb, invert=True) * shrink
            z_or = _zscore(pr.own_rate, m_or, s_or) * shrink

            ps = (W_LIVE_BORN * z_lb + W_TOTAL_BORN * z_tb +
                  W_STILLBORN * z_sb + W_OWN_RATE * z_or)

            parity_results.append({
                "individual_id": pr.individual_id,
                "parity": pr.parity,
                "own_weaned": pr.own_w,
                "own_rate": pr.own_rate,
                "z_own_weaned": z_ow,
                "z_live_born": z_lb,
                "z_total_born": z_tb,
                "z_stillborn": z_sb,
                "z_own_rate": z_or,
                "parity_score": ps,
            })

    # ── Step 3: Parity-level ranking ──
    _progress("産歴別順位計算中...")
    for k in by_parity:
        group_results = [r for r in parity_results if r["parity"] == k]
        group_results.sort(key=lambda x: x["parity_score"], reverse=True)
        for rank, r in enumerate(group_results, 1):
            r["rank_all"] = rank
        active_group = [r for r in group_results
                        if r["individual_id"] in active_sows]
        for rank, r in enumerate(active_group, 1):
            r["rank_active"] = rank
        # Fill None for non-active
        active_ids = {r["individual_id"] for r in active_group}
        for r in group_results:
            if r["individual_id"] not in active_ids:
                r.setdefault("rank_active", None)

    # Insert parity scores
    for r in parity_results:
        conn.execute(
            """INSERT INTO parity_scores
               (individual_id, parity, own_weaned, own_rate,
                z_own_weaned, z_live_born, z_total_born, z_stillborn,
                z_own_rate, parity_score, rank_all, rank_active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r["individual_id"], r["parity"], r["own_weaned"], r["own_rate"],
             r["z_own_weaned"], r["z_live_born"], r["z_total_born"],
             r["z_stillborn"], r["z_own_rate"], r["parity_score"],
             r["rank_all"], r.get("rank_active")),
        )

    # ── Step 4: Sow-level 3-axis evaluation ──
    _progress("母豚レベル3軸評価中...")
    sow_parity: dict[str, list[dict]] = {}
    for r in parity_results:
        sow_parity.setdefault(r["individual_id"], []).append(r)

    sow_scores: list[dict] = []
    for sid, recs in sow_parity.items():
        recs.sort(key=lambda x: x["parity"])
        scores = [r["parity_score"] for r in recs]
        parities = [r["parity"] for r in recs]

        # Peak: average of parity 2-3
        peak_scores = [r["parity_score"] for r in recs
                       if r["parity"] in (2, 3)]
        peak = (sum(peak_scores) / len(peak_scores)) if peak_scores else (
            sum(scores) / len(scores) if scores else 0.0
        )

        # Stability: variance of parity scores (inverted)
        if len(scores) >= 2:
            m = sum(scores) / len(scores)
            var = sum((s - m) ** 2 for s in scores) / len(scores)
            stability = -var
        else:
            stability = 0.0

        # Sustain: second half avg - first half avg
        if len(scores) >= 2:
            mid = len(scores) // 2
            first_half = scores[:mid]
            second_half = scores[mid:]
            sustain = ((sum(second_half) / len(second_half)) -
                       (sum(first_half) / len(first_half)))
        else:
            sustain = 0.0

        sow_scores.append({
            "individual_id": sid,
            "peak": peak,
            "stability": stability,
            "sustain": sustain,
            "offspring_quality": None,  # computed below
            "total_score": None,        # computed below
        })

    # ── Step 5: Offspring quality (W_RATE, PS_RATE) ──
    _progress("繰り上げ率/PS率計算中...")
    w_rates: dict[str, float] = {}
    ps_rates: dict[str, float] = {}

    piglet_rows = conn.execute(
        "SELECT dam_id, rank, ps_shipment FROM piglets WHERE dam_id IS NOT NULL"
    ).fetchall()

    # Aggregate per dam
    dam_w_total: dict[str, int] = {}     # W-rank piglets
    dam_w_promoted: dict[str, int] = {}  # ps_shipment='W'
    dam_l_total: dict[str, int] = {}     # A/B/C-rank piglets
    dam_ps_sold: dict[str, int] = {}     # ps_shipment='○'

    for pr in piglet_rows:
        dam = pr["dam_id"]
        rank = pr["rank"]
        ps = pr["ps_shipment"]
        if rank == "W":
            dam_w_total[dam] = dam_w_total.get(dam, 0) + 1
            if ps == "W":
                dam_w_promoted[dam] = dam_w_promoted.get(dam, 0) + 1
        elif rank in ("A", "B", "C"):
            dam_l_total[dam] = dam_l_total.get(dam, 0) + 1
            if ps == "○":
                dam_ps_sold[dam] = dam_ps_sold.get(dam, 0) + 1

    for dam in dam_w_total:
        total = dam_w_total[dam]
        if total > 0:
            w_rates[dam] = dam_w_promoted.get(dam, 0) / total

    for dam in dam_l_total:
        total = dam_l_total[dam]
        if total > 0:
            ps_rates[dam] = dam_ps_sold.get(dam, 0) / total

    # Z-score for offspring quality
    wr_vals = list(w_rates.values())
    pr_vals = list(ps_rates.values())
    m_wr, s_wr = _mean_sd(wr_vals) if wr_vals else (0, 0)
    m_pr, s_pr = _mean_sd(pr_vals) if pr_vals else (0, 0)

    for ss in sow_scores:
        sid = ss["individual_id"]
        z_wr = _zscore(w_rates.get(sid), m_wr, s_wr) if sid in w_rates else 0.0
        z_pr = _zscore(ps_rates.get(sid), m_pr, s_pr) if sid in ps_rates else 0.0
        oq = W_W_RATE * z_wr + W_PS_RATE * z_pr
        ss["offspring_quality"] = oq
        ss["total_score"] = (W_PEAK * ss["peak"] +
                             W_STABILITY * ss["stability"] +
                             W_SUSTAIN * ss["sustain"] +
                             W_OFFSPRING * oq)

    # ── Step 6: Sow-level rankings ──
    _progress("母豚順位計算中...")
    sow_scores.sort(key=lambda x: x["total_score"] or 0, reverse=True)
    for rank, ss in enumerate(sow_scores, 1):
        ss["rank_all"] = rank

    active_scores = [ss for ss in sow_scores
                     if ss["individual_id"] in active_sows]
    for rank, ss in enumerate(active_scores, 1):
        ss["rank_active"] = rank

    active_scored_ids = {ss["individual_id"] for ss in active_scores}
    for ss in sow_scores:
        if ss["individual_id"] not in active_scored_ids:
            ss.setdefault("rank_active", None)

    # Insert sow scores
    for ss in sow_scores:
        conn.execute(
            """INSERT INTO sow_scores
               (individual_id, peak, stability, sustain,
                offspring_quality, total_score, rank_all, rank_active)
               VALUES (?,?,?,?,?,?,?,?)""",
            (ss["individual_id"], ss["peak"], ss["stability"],
             ss["sustain"], ss["offspring_quality"], ss["total_score"],
             ss["rank_all"], ss.get("rank_active")),
        )

    conn.commit()
    _progress("成績評価完了")
