"""Pedigree v3: concentric-circle layout with ranking lane + spotlight."""

from __future__ import annotations

import math
import sys
import sqlite3

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPen
from PyQt6.QtWidgets import QGraphicsItem

from app.gui.pedigree_widget import (
    COL_BG,
    COL_FATHER_TAG,
    COL_MOTHER_LINE,
    NODE_H,
    NODE_W,
    TreeNode,
)
from app.gui.pedigree_widget2 import PedigreeWidget2

# Layout constants for concentric circles
BASE_RADIUS = 200
RADIUS_INCREMENT = 250
MIN_SECTOR = math.radians(15)


class PedigreeWidget3(PedigreeWidget2):
    """Pedigree with concentric-circle layout, ranking lane and spotlight."""

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(conn, parent)
        self._node_angles: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Helper: count visible leaves under a node
    # ------------------------------------------------------------------
    def _count_visible_leaves(self, node: TreeNode) -> int:
        visible = [c for c in node.children
                   if not self._active_only or c.has_active]
        if not visible:
            return 1
        return sum(self._count_visible_leaves(c) for c in visible)

    # ------------------------------------------------------------------
    # Compute angle sectors for root lineages
    # ------------------------------------------------------------------
    def _compute_sectors(self, roots: list[TreeNode]) -> list[tuple[TreeNode, float, float]]:
        """Return [(root, sector_start, sector_end), ...] for each root."""
        leaf_counts = []
        for root in roots:
            leaf_counts.append(self._count_visible_leaves(root))

        total_leaves = sum(leaf_counts)
        if total_leaves == 0:
            return []

        # First pass: proportional angles with minimum guarantee
        raw_angles = [(count / total_leaves) * 2 * math.pi for count in leaf_counts]

        # Enforce minimum sector
        clamped = [max(a, MIN_SECTOR) for a in raw_angles]
        total_clamped = sum(clamped)

        # Normalise to 2π
        if total_clamped > 0:
            scale = 2 * math.pi / total_clamped
            clamped = [a * scale for a in clamped]

        # Build sectors
        sectors: list[tuple[TreeNode, float, float]] = []
        start = 0.0
        for root, angle in zip(roots, clamped):
            sectors.append((root, start, start + angle))
            start += angle

        return sectors

    # ------------------------------------------------------------------
    # Recursively position subtree within an angle sector
    # ------------------------------------------------------------------
    def _position_subtree(self, node: TreeNode, sector_start: float, sector_end: float) -> float:
        """Position node and its descendants. Returns the angle of this node."""
        radius = BASE_RADIUS + node.generation * RADIUS_INCREMENT

        visible = [c for c in node.children
                   if not self._active_only or c.has_active]

        if not visible:
            # Leaf: place at sector centre
            angle = (sector_start + sector_end) / 2
            self._node_angles[node.individual_id] = angle
            node.x = radius * math.cos(angle)
            node.y = radius * math.sin(angle)
            return angle

        # Distribute children proportionally within this sector
        child_leaves = [self._count_visible_leaves(c) for c in visible]
        total = sum(child_leaves)
        if total == 0:
            total = 1

        child_angles: list[float] = []
        cur = sector_start
        for child, leaves in zip(visible, child_leaves):
            share = (leaves / total) * (sector_end - sector_start)
            child_angle = self._position_subtree(child, cur, cur + share)
            child_angles.append(child_angle)
            cur += share

        # Internal node: place at average angle of children
        avg_angle = sum(child_angles) / len(child_angles)
        self._node_angles[node.individual_id] = avg_angle
        node.x = radius * math.cos(avg_angle)
        node.y = radius * math.sin(avg_angle)
        return avg_angle

    # ------------------------------------------------------------------
    # Resolve card overlaps by scaling radii (active-only mode)
    # ------------------------------------------------------------------
    def _resolve_overlaps(self, sectors: list[tuple[TreeNode, float, float]]) -> None:
        """If cards overlap on any ring, scale all radii up to eliminate it."""
        min_dist = math.sqrt(NODE_W ** 2 + NODE_H ** 2)

        # Collect visible nodes grouped by generation
        gen_groups: dict[int, list[tuple[str, float]]] = {}

        def _collect(node: TreeNode) -> None:
            nid = node.individual_id
            if nid in self._node_angles:
                gen_groups.setdefault(node.generation, []).append(
                    (nid, self._node_angles[nid])
                )
            for c in node.children:
                if not self._active_only or c.has_active:
                    _collect(c)

        for root, _, _ in sectors:
            _collect(root)

        max_scale = 1.0
        for gen, items in gen_groups.items():
            if len(items) < 2:
                continue
            items.sort(key=lambda t: t[1])
            radius = BASE_RADIUS + gen * RADIUS_INCREMENT
            if radius <= 0:
                continue
            for i in range(len(items)):
                j = (i + 1) % len(items)
                delta = items[j][1] - items[i][1]
                if j == 0:
                    delta += 2 * math.pi
                if delta <= 0:
                    continue
                arc = radius * delta
                if arc < min_dist:
                    needed = min_dist / delta
                    scale = needed / radius
                    if scale > max_scale:
                        max_scale = scale

        if max_scale <= 1.0:
            return

        # Reposition all nodes with scaled radii
        for nid, angle in self._node_angles.items():
            node = self.all_nodes.get(nid)
            if node is None:
                continue
            new_radius = (BASE_RADIUS + node.generation * RADIUS_INCREMENT) * max_scale
            node.x = new_radius * math.cos(angle)
            node.y = new_radius * math.sin(angle)

    # ------------------------------------------------------------------
    # Render: concentric-circle layout
    # ------------------------------------------------------------------
    def _render(self) -> None:
        self.scene.clear()
        self._node_items.clear()
        self._node_angles.clear()

        roots = self.root_nodes
        if self._active_only:
            roots = [r for r in roots if r.has_active]

        if not roots:
            self.info_label.setText("表示: 0頭")
            return

        # Compute sector assignments and positions
        sectors = self._compute_sectors(roots)

        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, 5000))
        try:
            for root, s_start, s_end in sectors:
                self._position_subtree(root, s_start, s_end)
        finally:
            sys.setrecursionlimit(old_limit)

        if self._active_only:
            self._resolve_overlaps(sectors)

        top10_threshold = self._compute_top10_threshold()

        drawn = 0
        for root, _s, _e in sectors:
            drawn += self._draw_subtree(root, top10_threshold)

        spot = self._spotlight_root if self._spotlight_root else "-"
        self.info_label.setText(
            f"表示: {drawn}頭 / 全{len(self.all_nodes)}頭  "
            f"(稼働: {sum(1 for n in self.all_nodes.values() if n.status == 'active')})  "
            f"Spot: {spot}"
        )

    # ------------------------------------------------------------------
    # Draw subtree: node centred on (node.x, node.y)
    # ------------------------------------------------------------------
    def _draw_subtree(self, node: TreeNode, top10_thr: float) -> int:
        if self._active_only and not node.has_active:
            return 0

        count = 1
        in_focus = self._in_spotlight(node)
        base_color = self._node_color(node, top10_thr)
        color = self._fade(base_color, in_focus)

        # Centre the rectangle on (node.x, node.y)
        rx = node.x - NODE_W / 2
        ry = node.y - NODE_H / 2

        rect = self.scene.addRect(
            QRectF(rx, ry, NODE_W, NODE_H),
            QPen(color.darker(130), 1.5),
            QBrush(color),
        )
        rect.setData(0, node.individual_id)
        rect.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self._node_items[node.individual_id] = rect

        font = QFont("Meiryo", 8)
        font_s = QFont("Meiryo", 7)
        alpha_main = 255 if in_focus else 85
        alpha_sub = 200 if in_focus else 70

        t = self.scene.addSimpleText(node.individual_id, font)
        t.setPos(rx + 4, ry + 2)
        t.setBrush(QBrush(QColor(255, 255, 255, alpha_main)))

        line2 = f"産歴{node.parity_count}"
        if node.total_score is not None:
            line2 += f"  S={node.total_score:+.2f}"
        t2 = self.scene.addSimpleText(line2, font_s)
        t2.setPos(rx + 4, ry + 18)
        t2.setBrush(QBrush(QColor(255, 255, 255, alpha_sub)))

        if node.cause:
            line3 = node.cause[:16]
        elif node.rank_all is not None:
            line3 = f"全{node.rank_all}/{self._ranked_all}"
            if node.rank_active is not None:
                line3 += f" 稼{node.rank_active}/{self._ranked_active}"
        else:
            line3 = node.status
        t3 = self.scene.addSimpleText(line3, font_s)
        t3.setPos(rx + 4, ry + 34)
        t3.setBrush(QBrush(QColor(255, 255, 255, alpha_sub)))

        if node.sire_id:
            sire_color = QColor(COL_FATHER_TAG)
            sire_color.setAlpha(255 if in_focus else 80)
            ts = self.scene.addSimpleText(f"♂{node.sire_id}", font_s)
            ts.setPos(rx + 4, ry + 46)
            ts.setBrush(QBrush(sire_color))

        visible = [c for c in node.children
                   if not self._active_only or c.has_active]
        for child in visible:
            self._draw_mother_line(
                node, child,
                self._in_spotlight(node) and self._in_spotlight(child),
            )
            count += self._draw_subtree(child, top10_thr)

        return count

    # ------------------------------------------------------------------
    # Draw edge: simple straight line between node centres
    # ------------------------------------------------------------------
    def _draw_mother_line(self, parent: TreeNode, child: TreeNode, in_focus: bool = True) -> None:
        line_color = QColor(COL_MOTHER_LINE if in_focus else COL_BG.darker(120))
        if not in_focus:
            line_color.setAlpha(85)
        pen = QPen(line_color, 1.5)
        self.scene.addLine(parent.x, parent.y, child.x, child.y, pen)
