"""Generate inline SVG pedigree trees (ancestor view, left-to-right)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from html import escape

# Layout constants (scaled down from GUI)
NODE_W = 140
NODE_H = 52
H_SPACING = 170
V_SPACING = 64
PADDING = 20

# Colours (same as GUI)
COL_ACTIVE = "#4CAF50"
COL_DEAD = "#9E9E9E"
COL_CULLED = "#FF9800"
COL_TOP10 = "#E53935"
COL_MOTHER_LINE = "#D32F2F"
COL_FATHER_TAG = "#1565C0"


@dataclass
class AncestorNode:
    individual_id: str
    dam_id: str | None = None
    sire_id: str | None = None
    status: str = "active"
    parity_count: int = 0
    total_score: float | None = None
    rank_all: int | None = None
    rank_active: int | None = None
    # layout
    x: float = 0.0
    y: float = 0.0
    generation: int = 0
    # ancestor link
    dam_node: AncestorNode | None = None


def build_ancestor_tree(
    conn: sqlite3.Connection,
    individual_id: str,
    max_generations: int = 4,
) -> AncestorNode | None:
    """Build ancestor tree (dam lineage) up to max_generations."""
    # Pre-fetch all sow data for efficiency
    cache: dict[str, dict] = {}

    def _fetch(iid: str) -> dict | None:
        if iid in cache:
            return cache[iid]
        row = conn.execute(
            """SELECT s.individual_id, s.dam_id, s.sire_id, s.status,
                      sc.total_score, sc.rank_all, sc.rank_active
               FROM sows s
               LEFT JOIN sow_scores sc ON s.individual_id = sc.individual_id
               WHERE s.individual_id = ?""",
            (iid,),
        ).fetchone()
        if row:
            cache[iid] = dict(row)
        return cache.get(iid)

    def _build(iid: str, gen: int) -> AncestorNode | None:
        data = _fetch(iid)
        if not data:
            return None
        # Parity count
        prow = conn.execute(
            "SELECT MAX(parity) AS max_p FROM farrowing_records "
            "WHERE individual_id = ?", (iid,)
        ).fetchone()
        parity = prow["max_p"] if prow and prow["max_p"] else 0

        node = AncestorNode(
            individual_id=data["individual_id"],
            dam_id=data["dam_id"],
            sire_id=data["sire_id"],
            status=data["status"] or "active",
            parity_count=parity,
            total_score=data["total_score"],
            rank_all=data["rank_all"],
            rank_active=data["rank_active"],
            generation=gen,
        )
        # Recurse to dam (ancestor)
        if gen < max_generations and data["dam_id"]:
            node.dam_node = _build(data["dam_id"], gen + 1)
        return node

    return _build(individual_id, 0)


def layout_ancestor_tree(root: AncestorNode) -> tuple[float, float]:
    """Assign x,y coordinates. Gen 0 (self) on left, ancestors to right.

    Returns (total_width, total_height).
    """
    # Collect all nodes and find max generation
    nodes: list[AncestorNode] = []
    stack = [root]
    max_gen = 0
    while stack:
        n = stack.pop()
        nodes.append(n)
        max_gen = max(max_gen, n.generation)
        if n.dam_node:
            stack.append(n.dam_node)

    # Simple layout: each generation is a column
    # Within each column, nodes are stacked vertically
    gen_slots: dict[int, list[AncestorNode]] = {}
    for n in nodes:
        gen_slots.setdefault(n.generation, []).append(n)

    # Assign positions
    for gen, gen_nodes in gen_slots.items():
        for i, n in enumerate(gen_nodes):
            n.x = PADDING + gen * H_SPACING
            n.y = PADDING + i * V_SPACING

    # Center parent vertically relative to child
    # Process from leaves to root (highest gen first)
    for gen in range(max_gen, -1, -1):
        for n in gen_slots.get(gen, []):
            if n.dam_node:
                # Vertically center dam relative to this node
                n.dam_node.y = n.y  # simple: same y for linear chain

    width = PADDING * 2 + (max_gen + 1) * H_SPACING - (H_SPACING - NODE_W)
    height = PADDING * 2 + NODE_H
    return width, height


def render_svg(
    root: AncestorNode,
    width: float,
    height: float,
    top10_threshold: float,
) -> str:
    """Render the ancestor tree as an SVG string."""
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}" '
        f'style="font-family:-apple-system,Meiryo,sans-serif;background:#FAFAFA">'
    )

    # Collect all nodes
    nodes: list[AncestorNode] = []
    stack = [root]
    while stack:
        n = stack.pop()
        nodes.append(n)
        if n.dam_node:
            stack.append(n.dam_node)

    # Draw mother lines first (behind nodes)
    for n in nodes:
        if n.dam_node:
            parts.append(_svg_mother_line(n, n.dam_node))

    # Draw nodes
    for n in nodes:
        parts.append(_svg_node(n, top10_threshold))

    parts.append("</svg>")
    return "\n".join(parts)


def _node_color(node: AncestorNode, top10_thr: float) -> str:
    if (node.total_score is not None
            and node.total_score >= top10_thr
            and top10_thr != float("inf")):
        return COL_TOP10
    if node.status == "dead":
        return COL_DEAD
    if node.status in ("culled", "inactive"):
        return COL_CULLED
    return COL_ACTIVE


def _svg_node(node: AncestorNode, top10_thr: float) -> str:
    color = _node_color(node, top10_thr)
    x, y = node.x, node.y
    lines: list[str] = []

    # Rounded rectangle
    lines.append(
        f'<rect x="{x}" y="{y}" width="{NODE_W}" height="{NODE_H}" '
        f'rx="4" fill="{color}" stroke="{color}" stroke-opacity="0.6"/>'
    )

    # Parity number (left of card)
    if node.parity_count > 0:
        lines.append(
            f'<text x="{x - 6}" y="{y + NODE_H / 2 + 5}" '
            f'text-anchor="end" font-size="14" fill="#333">'
            f'{node.parity_count}</text>'
        )

    # Line 1: individual_id
    lines.append(
        f'<text x="{x + 4}" y="{y + 14}" '
        f'font-size="10" font-weight="bold" fill="white">'
        f'{escape(node.individual_id)}</text>'
    )

    # Line 2: parity + score
    line2 = f"産歴{node.parity_count}"
    if node.total_score is not None:
        line2 += f"  S={node.total_score:+.2f}"
    lines.append(
        f'<text x="{x + 4}" y="{y + 28}" '
        f'font-size="8" fill="rgba(255,255,255,0.85)">'
        f'{escape(line2)}</text>'
    )

    # Line 3: rank
    if node.rank_all is not None:
        line3 = f"全{node.rank_all}"
        if node.rank_active is not None:
            line3 += f" 稼{node.rank_active}"
    else:
        line3 = node.status
    lines.append(
        f'<text x="{x + 4}" y="{y + 40}" '
        f'font-size="8" fill="rgba(255,255,255,0.7)">'
        f'{escape(line3)}</text>'
    )

    # Sire tag (blue, right side)
    if node.sire_id:
        lines.append(
            f'<text x="{x + NODE_W - 4}" y="{y + 48}" '
            f'text-anchor="end" font-size="7" fill="{COL_FATHER_TAG}">'
            f'♂{escape(node.sire_id)}</text>'
        )

    return "\n".join(lines)


def _svg_mother_line(child: AncestorNode, dam: AncestorNode) -> str:
    """Cubic bezier from child's right edge to dam's left edge."""
    x1 = child.x + NODE_W
    y1 = child.y + NODE_H / 2
    x2 = dam.x
    y2 = dam.y + NODE_H / 2
    mx = (x1 + x2) / 2
    return (
        f'<path d="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}" '
        f'fill="none" stroke="{COL_MOTHER_LINE}" stroke-width="1.5" '
        f'opacity="0.7"/>'
    )
