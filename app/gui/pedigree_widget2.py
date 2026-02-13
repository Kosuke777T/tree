"""Pedigree v2: ranking lane + spotlight for fast lineage discovery."""

from __future__ import annotations

import math
import sqlite3

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
)

from app.gui.pedigree_widget import (
    COL_ACTIVE,
    COL_BG,
    COL_CULLED,
    COL_DEAD,
    COL_FATHER_TAG,
    COL_MOTHER_LINE,
    COL_TOP10,
    H_SPACING,
    NODE_H,
    NODE_W,
    PedigreeWidget,
    TreeNode,
    V_SPACING,
)


class PedigreeWidget2(PedigreeWidget):
    """Pedigree with lineage ranking lane and spotlight mode."""

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(conn, parent)
        self._node_root: dict[str, str] = {}
        self._spotlight_root: str | None = None

        root_layout = self.layout()
        toolbar = root_layout.itemAt(0).layout()

        self.btn_clear_spotlight = QPushButton("スポットライト解除")
        self.btn_clear_spotlight.clicked.connect(self._clear_spotlight)
        toolbar.insertWidget(3, self.btn_clear_spotlight)

        self.rank_list = QListWidget()
        self.rank_list.currentItemChanged.connect(self._on_rank_selected)

        lane = QWidget()
        lane_layout = QVBoxLayout(lane)
        lane_layout.setContentsMargins(0, 0, 0, 0)
        lane_layout.addWidget(QLabel("系統ランキング・レーン"))
        lane_layout.addWidget(self.rank_list, 1)
        lane_layout.addWidget(QLabel("系統を選ぶとスポットライト表示"))
        lane.setMinimumWidth(320)
        lane.setMaximumWidth(420)

        root_layout.removeWidget(self.view)
        body = QHBoxLayout()
        body.addWidget(lane)
        body.addWidget(self.view, 1)
        root_layout.addLayout(body)

    def load_data(self) -> None:
        super().load_data()
        self._rebuild_node_root_map()
        self._refresh_ranking_lane()
        self._render()

    def _on_active_filter(self, state: int) -> None:
        self._active_only = state == Qt.CheckState.Checked.value
        self._refresh_ranking_lane()
        self._render()

    def _clear_spotlight(self) -> None:
        self._spotlight_root = None
        self.rank_list.blockSignals(True)
        self.rank_list.clearSelection()
        self.rank_list.blockSignals(False)
        self._render()

    def _on_rank_selected(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        del previous
        if not current:
            return
        self._spotlight_root = current.data(Qt.ItemDataRole.UserRole)
        self._render()
        if self._spotlight_root and self._spotlight_root in self._node_items:
            self.view.centerOn(self._node_items[self._spotlight_root])

    def _rebuild_node_root_map(self) -> None:
        self._node_root.clear()
        for root in self.root_nodes:
            stack = [root]
            while stack:
                node = stack.pop()
                self._node_root[node.individual_id] = root.individual_id
                for child in node.children:
                    stack.append(child)

    def _visible_descendants(self, root: TreeNode) -> list[TreeNode]:
        out: list[TreeNode] = []
        stack = [root]
        while stack:
            node = stack.pop()
            if self._active_only and not node.has_active:
                continue
            out.append(node)
            for child in node.children:
                stack.append(child)
        return out

    def _compute_top10_threshold(self) -> float:
        scored = [
            n for n in self.all_nodes.values()
            if n.total_score is not None and n.total_score != 0
        ]
        if not scored:
            return float("inf")
        scored.sort(key=lambda n: n.total_score, reverse=True)
        idx = max(0, len(scored) // 10 - 1)
        return scored[idx].total_score

    def _refresh_ranking_lane(self) -> None:
        top10_thr = self._compute_top10_threshold()
        rows: list[dict] = []

        for root in self.root_nodes:
            if self._active_only and not root.has_active:
                continue

            members = self._visible_descendants(root)
            if not members:
                continue

            active_count = sum(1 for n in members if n.status == "active")
            top_count = sum(
                1
                for n in members
                if (n.total_score is not None and n.total_score >= top10_thr and
                    top10_thr != float("inf"))
            )
            scored = [
                n.total_score for n in members
                if n.total_score is not None and n.total_score != 0
            ]
            avg_score = (sum(scored) / len(scored)) if scored else 0.0
            lineage_score = avg_score * math.log(active_count + 1.0)

            rows.append(
                {
                    "root_id": root.individual_id,
                    "lineage_score": lineage_score,
                    "active_count": active_count,
                    "top_count": top_count,
                    "members": len(members),
                }
            )

        rows.sort(
            key=lambda r: (
                r["lineage_score"], r["top_count"], r["active_count"], r["members"]
            ),
            reverse=True,
        )

        self.rank_list.blockSignals(True)
        self.rank_list.clear()
        keep_row = -1
        for i, row in enumerate(rows, start=1):
            text = (
                f"{i:>2}. {row['root_id']}  "
                f"L={row['lineage_score']:+.2f}  "
                f"稼{row['active_count']}  Top{row['top_count']}"
            )
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, row["root_id"])
            self.rank_list.addItem(item)
            if self._spotlight_root == row["root_id"]:
                keep_row = i - 1

        if self._spotlight_root and keep_row == -1:
            self._spotlight_root = None
        elif keep_row >= 0:
            self.rank_list.setCurrentRow(keep_row)

        self.rank_list.blockSignals(False)

    def _in_spotlight(self, node: TreeNode) -> bool:
        if self._spotlight_root is None:
            return True
        return self._node_root.get(node.individual_id) == self._spotlight_root

    def _fade(self, color: QColor, visible: bool) -> QColor:
        out = QColor(color)
        if not visible:
            out.setAlpha(55)
        return out

    def _render(self) -> None:
        self.scene.clear()
        self._node_items.clear()

        roots = self.root_nodes
        if self._active_only:
            roots = [r for r in roots if r.has_active]

        y_counter = 0.0

        def _layout(node: TreeNode) -> float:
            nonlocal y_counter
            if self._active_only and not node.has_active:
                return 0.0

            visible = [c for c in node.children
                       if not self._active_only or c.has_active]

            if not visible:
                node.x = node.generation * H_SPACING
                node.y = y_counter
                y_counter += V_SPACING
                return node.y

            for child in visible:
                _layout(child)

            ys = [c.y for c in visible]
            node.x = node.generation * H_SPACING
            node.y = (min(ys) + max(ys)) / 2
            return node.y

        import sys
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, 5000))
        try:
            for root in roots:
                _layout(root)
        finally:
            sys.setrecursionlimit(old_limit)

        top10_threshold = self._compute_top10_threshold()

        drawn = 0
        for root in roots:
            drawn += self._draw_subtree(root, top10_threshold)

        spot = self._spotlight_root if self._spotlight_root else "-"
        self.info_label.setText(
            f"表示: {drawn}頭 / 全{len(self.all_nodes)}頭  "
            f"(稼働: {sum(1 for n in self.all_nodes.values() if n.status == 'active')})  "
            f"Spot: {spot}"
        )

    def _draw_subtree(self, node: TreeNode, top10_thr: float) -> int:
        if self._active_only and not node.has_active:
            return 0

        count = 1
        in_focus = self._in_spotlight(node)
        base_color = self._node_color(node, top10_thr)
        color = self._fade(base_color, in_focus)

        rect = self.scene.addRect(
            QRectF(node.x, node.y, NODE_W, NODE_H),
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
        t.setPos(node.x + 4, node.y + 2)
        t.setBrush(QBrush(QColor(255, 255, 255, alpha_main)))

        line2 = f"産歴{node.parity_count}"
        if node.total_score is not None:
            line2 += f"  S={node.total_score:+.2f}"
        t2 = self.scene.addSimpleText(line2, font_s)
        t2.setPos(node.x + 4, node.y + 18)
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
        t3.setPos(node.x + 4, node.y + 34)
        t3.setBrush(QBrush(QColor(255, 255, 255, alpha_sub)))

        if node.sire_id:
            sire_color = QColor(COL_FATHER_TAG)
            sire_color.setAlpha(255 if in_focus else 80)
            ts = self.scene.addSimpleText(f"♂{node.sire_id}", font_s)
            ts.setPos(node.x + 4, node.y + 46)
            ts.setBrush(QBrush(sire_color))

        visible = [c for c in node.children
                   if not self._active_only or c.has_active]
        for child in visible:
            self._draw_mother_line(node, child, self._in_spotlight(node) and
                                   self._in_spotlight(child))
            count += self._draw_subtree(child, top10_thr)

        return count

    def _draw_mother_line(self, parent: TreeNode, child: TreeNode, in_focus: bool = True) -> None:
        line_color = QColor(COL_MOTHER_LINE if in_focus else COL_BG.darker(120))
        if not in_focus:
            line_color.setAlpha(85)
        pen = QPen(line_color, 1.5)
        x1 = parent.x + NODE_W
        y1 = parent.y + NODE_H / 2
        x2 = child.x
        y2 = child.y + NODE_H / 2
        path = QPainterPath(QPointF(x1, y1))
        mx = (x1 + x2) / 2
        path.cubicTo(QPointF(mx, y1), QPointF(mx, y2), QPointF(x2, y2))
        self.scene.addPath(path, pen)

    def _node_color(self, node: TreeNode, top10_thr: float) -> QColor:
        if (node.total_score is not None and
                node.total_score >= top10_thr and top10_thr != float("inf")):
            return COL_TOP10
        if node.status == "dead":
            return COL_DEAD
        if node.status in {"culled", "inactive"}:
            return COL_CULLED
        return COL_ACTIVE
