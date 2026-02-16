"""Pedigree v3: concentric-circle layout with ranking lane + spotlight."""

from __future__ import annotations

import math
import sys
import sqlite3

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPen
from PyQt6.QtWidgets import QGraphicsItem, QLabel, QLineEdit

from app.gui.pedigree_widget import (
    COL_BG,
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
        self._node_size_scale: dict[str, float] = {}
        self._excellent_line_multiplier = 1.0
        self._excellent_node_multiplier = 1.0
        self._font_cache: dict[int, QFont] = {}
        self._font_metrics_cache: dict[int, QFontMetricsF] = {}

        root_layout = self.layout()
        toolbar = root_layout.itemAt(0).layout()

        self.lbl_line_multiplier = QLabel("優秀線倍率")
        self.edit_line_multiplier = QLineEdit("1.0")
        self.edit_line_multiplier.setFixedWidth(60)
        self.edit_line_multiplier.setPlaceholderText("1.0")
        self.edit_line_multiplier.editingFinished.connect(
            self._on_line_multiplier_changed
        )
        self.lbl_node_multiplier = QLabel("優秀円倍率")
        self.edit_node_multiplier = QLineEdit("1.0")
        self.edit_node_multiplier.setFixedWidth(60)
        self.edit_node_multiplier.setPlaceholderText("1.0")
        self.edit_node_multiplier.editingFinished.connect(
            self._on_node_multiplier_changed
        )

        toolbar.insertWidget(4, self.lbl_line_multiplier)
        toolbar.insertWidget(5, self.edit_line_multiplier)
        toolbar.insertWidget(6, self.lbl_node_multiplier)
        toolbar.insertWidget(7, self.edit_node_multiplier)

    def _on_line_multiplier_changed(self) -> None:
        text = self.edit_line_multiplier.text().strip()
        try:
            val = float(text)
        except ValueError:
            self.edit_line_multiplier.setText(f"{self._excellent_line_multiplier:.2f}")
            return

        val = max(0.1, min(10.0, val))
        self._excellent_line_multiplier = val
        self.edit_line_multiplier.setText(f"{val:.2f}")
        self._render()

    def _on_node_multiplier_changed(self) -> None:
        text = self.edit_node_multiplier.text().strip()
        try:
            val = float(text)
        except ValueError:
            self.edit_node_multiplier.setText(f"{self._excellent_node_multiplier:.2f}")
            return

        val = max(0.1, min(10.0, val))
        self._excellent_node_multiplier = val
        self.edit_node_multiplier.setText(f"{val:.2f}")
        self._render()

    def _font(self, pt: int) -> QFont:
        pt = max(1, int(pt))
        if pt not in self._font_cache:
            self._font_cache[pt] = QFont("Meiryo", pt)
        return self._font_cache[pt]

    def _font_metrics(self, pt: int) -> QFontMetricsF:
        pt = max(1, int(pt))
        if pt not in self._font_metrics_cache:
            self._font_metrics_cache[pt] = QFontMetricsF(self._font(pt))
        return self._font_metrics_cache[pt]

    def _fits_text_block(
        self,
        main_pt: int,
        sub_pt: int,
        all_lines: list[str],
        max_w: float,
        max_h: float,
        line_gap: float,
    ) -> bool:
        fm_main = self._font_metrics(main_pt)
        fm_sub = self._font_metrics(sub_pt)
        widths = [fm_main.horizontalAdvance(all_lines[0])]
        widths.extend(fm_sub.horizontalAdvance(t) for t in all_lines[1:])
        total_h = fm_main.height() + fm_sub.height() * (len(all_lines) - 1)
        total_h += line_gap * (len(all_lines) - 1)
        return max(widths) <= max_w and total_h <= max_h

    def _select_font_sizes(self, diameter: float, all_lines: list[str]) -> tuple[int, int, float]:
        """Fast fit via binary search to avoid per-node brute force loops."""
        line_gap = max(2.0, diameter * 0.015)
        content_margin = max(8.0, diameter * 0.08)
        max_w = diameter - content_margin
        max_h = diameter - content_margin
        if max_w <= 0 or max_h <= 0:
            return 8, 7, line_gap

        low = 8
        high = max(14, int(diameter * 0.24))
        best_main = 8
        best_sub = 7
        while low <= high:
            mid = (low + high) // 2
            sub_pt = max(7, int(round(mid * 0.62)))
            if self._fits_text_block(mid, sub_pt, all_lines, max_w, max_h, line_gap):
                best_main = mid
                best_sub = sub_pt
                low = mid + 1
            else:
                high = mid - 1

        return best_main, best_sub, line_gap

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
    def _next_size_scale(self, parent_scale: float, parent: TreeNode, child: TreeNode) -> float:
        """Propagate node-size emphasis when child outperforms parent."""
        scale = parent_scale
        if parent.total_score is not None and child.total_score is not None:
            delta = child.total_score - parent.total_score
            if delta > 0:
                # Apply multiplier directly: 2.0 means 2x diameter.
                scale = min(10.0, parent_scale * self._excellent_node_multiplier)
        return scale

    def _position_subtree(
        self,
        node: TreeNode,
        sector_start: float,
        sector_end: float,
        inherited_size_scale: float = 1.0,
    ) -> float:
        """Position node and its descendants. Returns the angle of this node."""
        radius = BASE_RADIUS + node.generation * RADIUS_INCREMENT
        self._node_size_scale[node.individual_id] = inherited_size_scale

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
            child_scale = self._next_size_scale(inherited_size_scale, node, child)
            child_angle = self._position_subtree(child, cur, cur + share, child_scale)
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
        """If circles overlap on any ring, scale all radii up to eliminate it."""
        # Collect visible nodes grouped by generation
        gen_groups: dict[int, list[tuple[str, float, float]]] = {}

        def _collect(node: TreeNode) -> None:
            nid = node.individual_id
            if nid in self._node_angles:
                diameter = NODE_W * self._node_size_scale.get(nid, 1.0)
                gen_groups.setdefault(node.generation, []).append(
                    (nid, self._node_angles[nid], diameter)
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
                required = (items[i][2] + items[j][2]) / 2 + 2.0
                if arc < required:
                    needed = required / delta
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

    def _collect_visible_nodes(self, sectors: list[tuple[TreeNode, float, float]]) -> list[TreeNode]:
        """Collect currently visible nodes from roots in sectors."""
        visible_nodes: list[TreeNode] = []
        seen: set[str] = set()

        def _collect(node: TreeNode) -> None:
            if node.individual_id in seen:
                return
            seen.add(node.individual_id)
            if not self._active_only or node.has_active:
                visible_nodes.append(node)
            for c in node.children:
                if not self._active_only or c.has_active:
                    _collect(c)

        for root, _, _ in sectors:
            _collect(root)

        return visible_nodes

    def _compress_toward_center(self, sectors: list[tuple[TreeNode, float, float]]) -> None:
        """Bring layout closer to center while keeping circles non-overlapping."""
        nodes = self._collect_visible_nodes(sectors)
        if len(nodes) < 2:
            return

        required_scale = 0.0
        for i in range(len(nodes)):
            a = nodes[i]
            da = NODE_W * self._node_size_scale.get(a.individual_id, 1.0)
            for j in range(i + 1, len(nodes)):
                b = nodes[j]
                d = math.hypot(a.x - b.x, a.y - b.y)
                if d <= 0:
                    continue
                db = NODE_W * self._node_size_scale.get(b.individual_id, 1.0)
                min_allowed = (da + db) / 2 + 2.0
                ratio = min_allowed / d
                if ratio > required_scale:
                    required_scale = ratio

        if required_scale <= 0:
            return

        # Scale down as much as possible without creating overlap.
        target_scale = min(1.0, required_scale * 1.005)
        if target_scale >= 0.999:
            return

        for nid, angle in self._node_angles.items():
            node = self.all_nodes.get(nid)
            if node is None:
                continue
            base_radius = BASE_RADIUS + node.generation * RADIUS_INCREMENT
            new_radius = base_radius * target_scale
            node.x = new_radius * math.cos(angle)
            node.y = new_radius * math.sin(angle)

    # ------------------------------------------------------------------
    # Render: concentric-circle layout
    # ------------------------------------------------------------------
    def _render(self) -> None:
        self.scene.clear()
        self._node_items.clear()
        self._node_angles.clear()
        self._node_size_scale.clear()

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
                self._position_subtree(root, s_start, s_end, 1.0)
        finally:
            sys.setrecursionlimit(old_limit)

        if self._active_only:
            self._resolve_overlaps(sectors)
        self._compress_toward_center(sectors)

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
    def _draw_subtree(self, node: TreeNode, top10_thr: float, inherited_width: float | None = None) -> int:
        if self._active_only and not node.has_active:
            return 0

        count = 1
        in_focus = self._in_spotlight(node)
        base_color = self._node_color(node, top10_thr)
        color = self._fade(base_color, in_focus)

        visible = [c for c in node.children
                   if not self._active_only or c.has_active]
        for child in visible:
            edge_width = self._draw_mother_line(
                node, child,
                self._in_spotlight(node) and self._in_spotlight(child),
                inherited_width,
            )
            count += self._draw_subtree(child, top10_thr, edge_width)

        # Draw circular card centered on (node.x, node.y)
        diameter = NODE_W * self._node_size_scale.get(node.individual_id, 1.0)
        rx = node.x - diameter / 2
        ry = node.y - diameter / 2

        rect = self.scene.addEllipse(
            QRectF(rx, ry, diameter, diameter),
            QPen(color.darker(130), 1.5),
            QBrush(color),
        )
        rect.setData(0, node.individual_id)
        rect.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self._node_items[node.individual_id] = rect

        alpha_main = 255 if in_focus else 135
        alpha_sub = 220 if in_focus else 120

        line2 = f"産歴{node.parity_count}"
        if node.total_score is not None:
            line2 += f"  S={node.total_score:+.2f}"

        if node.cause:
            line3 = node.cause[:16]
        elif node.rank_all is not None:
            line3 = f"全{node.rank_all}/{self._ranked_all}"
            if node.rank_active is not None:
                line3 += f" 稼{node.rank_active}/{self._ranked_active}"
        else:
            line3 = node.status

        sub_lines: list[str] = [line2, line3]

        if node.sire_id:
            sub_lines.append(f"♂{node.sire_id}")

        # Scale all text lines to the largest readable size that fits inside the circle.
        all_lines = [node.individual_id, *sub_lines]
        best_main_pt, best_sub_pt, line_gap = self._select_font_sizes(diameter, all_lines)
        font = self._font(best_main_pt)
        font_s = self._font(best_sub_pt)

        lines: list[tuple[str, QFont, QColor]] = [
            (node.individual_id, font, QColor(0, 0, 0, alpha_main)),
            (line2, font_s, QColor(0, 0, 0, alpha_sub)),
            (line3, font_s, QColor(0, 0, 0, alpha_sub)),
        ]
        if node.sire_id:
            lines.append((f"♂{node.sire_id}", font_s, QColor(0, 0, 0, alpha_sub)))

        # Center the whole text block inside the circle.
        line_heights = [QFontMetricsF(ln_font).height() for _text, ln_font, _color in lines]

        total_h = sum(line_heights) + line_gap * (len(lines) - 1)
        y = node.y - total_h / 2
        for i, (text, ln_font, ln_color) in enumerate(lines):
            item = self.scene.addSimpleText(text, ln_font)
            w = item.boundingRect().width()
            h = line_heights[i]
            item.setPos(node.x - w / 2, y)
            item.setBrush(QBrush(ln_color))
            item.setData(0, node.individual_id)
            y += h + line_gap

        return count

    # ------------------------------------------------------------------
    # Draw edge: simple straight line between node centres
    # ------------------------------------------------------------------
    def _draw_mother_line(
        self,
        parent: TreeNode,
        child: TreeNode,
        in_focus: bool = True,
        inherited_width: float | None = None,
    ) -> float:
        line_color = QColor(COL_MOTHER_LINE if in_focus else COL_BG.darker(120))
        if not in_focus:
            line_color.setAlpha(85)

        # Inherited multiplicative model:
        # with multiplier=2.0 and continuous improvement -> 2 -> 4 -> 8 ...
        base_width = 1.0 if in_focus else 0.8
        width = inherited_width if inherited_width is not None else base_width
        if parent.total_score is not None and child.total_score is not None:
            delta = child.total_score - parent.total_score
            if delta > 0:
                width *= self._excellent_line_multiplier
            elif delta < 0:
                # Penalize decline so improvement/decline contrast remains visible.
                width = max(base_width * 0.8, width / max(1.1, self._excellent_line_multiplier))

        width = max(0.6, min(80.0, width))

        pen = QPen(line_color, width)
        self.scene.addLine(parent.x, parent.y, child.x, child.y, pen)
        return width
