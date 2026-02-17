"""HTML report export -- generates self-contained HTML with scores and pedigree SVGs."""

from __future__ import annotations

import sqlite3
from datetime import date
from html import escape
from pathlib import Path
from typing import Callable

from app.export.svg_pedigree import (
    build_ancestor_tree,
    layout_ancestor_tree,
    render_svg,
)
from app.export.templates import CSS_TEMPLATE, HTML_TEMPLATE, JS_TEMPLATE


def export_html_report(
    conn: sqlite3.Connection,
    output_dir: Path,
    progress_cb: Callable[[str], None] | None = None,
) -> Path:
    """Export full HTML report to output_dir.

    Returns the path to the generated HTML file.
    """
    def _progress(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    _progress("レポートデータ取得中...")

    # Counts
    total_sows = conn.execute("SELECT count(*) FROM sows").fetchone()[0]
    active_sows = conn.execute(
        "SELECT count(*) FROM sows WHERE status='active'"
    ).fetchone()[0]

    # Top-10% threshold
    top10_threshold = _compute_top10_threshold(conn)

    _progress("順位表生成中...")
    table_rows = _build_ranking_table(conn)

    _progress("家系図SVG生成中...")
    pedigree_cards = _build_pedigree_cards(conn, top10_threshold, _progress)

    _progress("HTML組み立て中...")
    report_date = date.today().isoformat()
    html = HTML_TEMPLATE.format(
        report_date=report_date,
        total_sows=total_sows,
        active_sows=active_sows,
        css=CSS_TEMPLATE,
        js=JS_TEMPLATE,
        table_rows=table_rows,
        pedigree_cards=pedigree_cards,
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"母豚レポート_{report_date}.html"
    output_path.write_text(html, encoding="utf-8")

    _progress(f"レポート出力完了: {output_path.name}")
    return output_path


def _compute_top10_threshold(conn: sqlite3.Connection) -> float:
    rows = conn.execute(
        "SELECT total_score FROM sow_scores "
        "WHERE total_score IS NOT NULL ORDER BY total_score DESC"
    ).fetchall()
    if not rows:
        return float("inf")
    idx = max(0, len(rows) // 10 - 1)
    return rows[idx]["total_score"]


def _build_ranking_table(conn: sqlite3.Connection) -> str:
    """Query sow_scores + sows, return HTML <tr> rows."""
    rows = conn.execute(
        """SELECT s.individual_id, s.dam_id, s.sire_id, s.status,
                  sc.total_score, sc.peak, sc.stability, sc.sustain,
                  sc.offspring_quality, sc.rank_all, sc.rank_active
           FROM sow_scores sc
           JOIN sows s ON sc.individual_id = s.individual_id
           ORDER BY sc.rank_all"""
    ).fetchall()

    # Parity counts
    parity_map: dict[str, int] = {}
    for r in conn.execute(
        "SELECT individual_id, MAX(parity) AS max_p "
        "FROM farrowing_records GROUP BY individual_id"
    ).fetchall():
        parity_map[r["individual_id"]] = r["max_p"] or 0

    parts: list[str] = []
    for r in rows:
        iid = r["individual_id"]
        status = r["status"] or "active"
        score = r["total_score"]
        score_cls = "score-pos" if score and score > 0 else "score-neg" if score and score < 0 else ""

        def _fmt(v: float | None) -> str:
            return f"{v:+.3f}" if v is not None else ""

        status_ja = {"active": "稼働", "dead": "死亡",
                     "culled": "廃豚", "inactive": "未稼働"}.get(status, status)

        parts.append(
            f'      <tr data-id="{escape(iid)}" '
            f'data-status="{escape(status)}" '
            f'class="status-{escape(status)}">'
            f"<td>{r['rank_all'] or ''}</td>"
            f"<td>{r['rank_active'] or ''}</td>"
            f'<td><strong>{escape(iid)}</strong></td>'
            f"<td>{escape(status_ja)}</td>"
            f"<td>{parity_map.get(iid, 0)}</td>"
            f'<td class="{score_cls}">{_fmt(score)}</td>'
            f"<td>{_fmt(r['peak'])}</td>"
            f"<td>{_fmt(r['stability'])}</td>"
            f"<td>{_fmt(r['sustain'])}</td>"
            f"<td>{_fmt(r['offspring_quality'])}</td>"
            f"<td>{escape(r['dam_id'] or '')}</td>"
            f"<td>{escape(r['sire_id'] or '')}</td>"
            f"</tr>"
        )
    return "\n".join(parts)


def _build_pedigree_cards(
    conn: sqlite3.Connection,
    top10_threshold: float,
    progress_cb: Callable[[str], None],
) -> str:
    """Generate collapsible pedigree card HTML for each sow."""
    rows = conn.execute(
        """SELECT s.individual_id, s.status,
                  sc.total_score, sc.rank_all, sc.rank_active,
                  sc.peak, sc.stability, sc.sustain, sc.offspring_quality
           FROM sow_scores sc
           JOIN sows s ON sc.individual_id = s.individual_id
           ORDER BY sc.rank_all"""
    ).fetchall()

    total = len(rows)
    parts: list[str] = []

    for i, r in enumerate(rows):
        if i % 100 == 0:
            progress_cb(f"家系図SVG生成中... {i}/{total}")

        iid = r["individual_id"]
        status = r["status"] or "active"
        score = r["total_score"]

        # Build ancestor SVG
        root = build_ancestor_tree(conn, iid, max_generations=4)
        svg_html = ""
        if root:
            w, h = layout_ancestor_tree(root)
            svg_html = render_svg(root, w, h, top10_threshold)

        # Score summary for card header
        score_str = f"S={score:+.3f}" if score is not None else ""
        rank_str = ""
        if r["rank_all"] is not None:
            rank_str = f"全{r['rank_all']}"
            if r["rank_active"] is not None:
                rank_str += f" 稼{r['rank_active']}"

        # Detail info
        info_parts: list[str] = []
        if r["peak"] is not None:
            info_parts.append(f"Peak={r['peak']:+.3f}")
        if r["stability"] is not None:
            info_parts.append(f"Stab={r['stability']:+.3f}")
        if r["sustain"] is not None:
            info_parts.append(f"Sust={r['sustain']:+.3f}")
        if r["offspring_quality"] is not None:
            info_parts.append(f"OQ={r['offspring_quality']:+.3f}")
        info_line = " | ".join(info_parts)

        parts.append(
            f'  <div class="card" data-id="{escape(iid)}" '
            f'data-status="{escape(status)}">\n'
            f'    <div class="card-header">\n'
            f'      <span>{escape(iid)}  {score_str}  {rank_str}</span>\n'
            f'      <span class="toggle">▼ 開く</span>\n'
            f'    </div>\n'
            f'    <div class="card-body">\n'
            f'      <div class="card-info"><span>{info_line}</span></div>\n'
            f'      <div class="svg-container">{svg_html}</div>\n'
            f'    </div>\n'
            f'  </div>'
        )

    progress_cb(f"家系図SVG生成完了 ({total}頭)")
    return "\n".join(parts)
