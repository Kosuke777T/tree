"""Pedigree v4: ML-enhanced lineage discovery (LightGBM-aware view)."""

from __future__ import annotations

import math
import sqlite3

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPen
from PyQt6.QtWidgets import QGraphicsItem, QLabel, QLineEdit, QListWidgetItem

from app.gui.pedigree_widget import COL_BG, COL_CULLED, COL_DEAD, COL_MOTHER_LINE, NODE_W, TreeNode
from app.gui.pedigree_widget3 import PedigreeWidget3


class PedigreeWidget4(PedigreeWidget3):
    """Pedigree view tuned for ML-based excellent-lineage spotting."""

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(conn, parent)
        self._ml_prob_threshold = 0.70
        self._ml_avg_prob: dict[str, float] = {}
        self._ml_best_prob: dict[str, float] = {}
        self._ml_high_count: dict[str, int] = {}
        self._ml_count: dict[str, int] = {}

        root_layout = self.layout()
        toolbar = root_layout.itemAt(0).layout()

        self.lbl_ml_threshold = QLabel("ML閾値")
        self.edit_ml_threshold = QLineEdit(f"{self._ml_prob_threshold:.2f}")
        self.edit_ml_threshold.setFixedWidth(60)
        self.edit_ml_threshold.setPlaceholderText("0.70")
        self.edit_ml_threshold.editingFinished.connect(self._on_ml_threshold_changed)
        toolbar.insertWidget(8, self.lbl_ml_threshold)
        toolbar.insertWidget(9, self.edit_ml_threshold)

    def _on_ml_threshold_changed(self) -> None:
        text = self.edit_ml_threshold.text().strip()
        try:
            val = float(text)
        except ValueError:
            self.edit_ml_threshold.setText(f"{self._ml_prob_threshold:.2f}")
            return

        val = max(0.50, min(0.95, val))
        self._ml_prob_threshold = val
        self.edit_ml_threshold.setText(f"{val:.2f}")
        self._refresh_ranking_lane()
        self._render()

    def load_data(self) -> None:
        super().load_data()
        self._load_ml_summary()
        self._refresh_ranking_lane()
        self._render()

    def _load_ml_summary(self) -> None:
        self._ml_avg_prob.clear()
        self._ml_best_prob.clear()
        self._ml_high_count.clear()
        self._ml_count.clear()

        rows = self.conn.execute(
            """
            SELECT individual_id,
                   AVG(pred_excellent_prob) AS avg_prob,
                   MAX(pred_excellent_prob) AS best_prob,
                   SUM(CASE WHEN pred_excellent_prob >= ? THEN 1 ELSE 0 END) AS high_count,
                   COUNT(*) AS total_count
            FROM ml_predictions
            GROUP BY individual_id
            """,
            (self._ml_prob_threshold,),
        ).fetchall()
        for r in rows:
            sid = r["individual_id"]
            if r["avg_prob"] is None:
                continue
            self._ml_avg_prob[sid] = float(r["avg_prob"])
            self._ml_best_prob[sid] = float(r["best_prob"] or 0.0)
            self._ml_high_count[sid] = int(r["high_count"] or 0)
            self._ml_count[sid] = int(r["total_count"] or 0)

    def _prob_color(self, p: float) -> QColor:
        """Map ML probability (0..1) to blue→red color."""
        p = max(0.0, min(1.0, p))
        low = QColor("#90CAF9")
        high = QColor("#E53935")
        r = int(low.red() + (high.red() - low.red()) * p)
        g = int(low.green() + (high.green() - low.green()) * p)
        b = int(low.blue() + (high.blue() - low.blue()) * p)
        return QColor(r, g, b)

    def _blend_color(self, a: QColor, b: QColor, ratio: float) -> QColor:
        ratio = max(0.0, min(1.0, ratio))
        r = int(a.red() * (1 - ratio) + b.red() * ratio)
        g = int(a.green() * (1 - ratio) + b.green() * ratio)
        bb = int(a.blue() * (1 - ratio) + b.blue() * ratio)
        return QColor(r, g, bb)

    def _node_color(self, node: TreeNode, top10_thr: float) -> QColor:
        # Keep dead/culled semantics intact.
        if node.status == "dead":
            return COL_DEAD
        if node.status in {"culled", "inactive"}:
            return COL_CULLED

        base = super()._node_color(node, top10_thr)
        p = self._ml_avg_prob.get(node.individual_id)
        if p is None:
            return base
        return self._blend_color(base, self._prob_color(p), 0.55)

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

            probs = [
                self._ml_avg_prob[n.individual_id]
                for n in members
                if n.individual_id in self._ml_avg_prob
            ]
            ml_avg = (sum(probs) / len(probs)) if probs else 0.0
            ml_high = sum(1 for p in probs if p >= self._ml_prob_threshold)

            # Keep the same score axis by converting probability to centered score.
            ml_component = (ml_avg - 0.5) * 4.0
            lineage_score = (avg_score + ml_component) * math.log(active_count + 1.0)
            lineage_score += ml_high * 0.12

            rows.append(
                {
                    "root_id": root.individual_id,
                    "lineage_score": lineage_score,
                    "active_count": active_count,
                    "top_count": top_count,
                    "members": len(members),
                    "ml_avg": ml_avg,
                    "ml_high": ml_high,
                }
            )

        rows.sort(
            key=lambda r: (
                r["lineage_score"], r["ml_high"], r["top_count"], r["active_count"], r["members"]
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
                f"ML={row['ml_avg']:.3f}  "
                f"高確率{row['ml_high']}  稼{row['active_count']}"
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

        diameter = NODE_W * self._node_size_scale.get(node.individual_id, 1.0)
        rx = node.x - diameter / 2
        ry = node.y - diameter / 2

        avg_prob = self._ml_avg_prob.get(node.individual_id)
        pen_width = 1.5
        pen_color = color.darker(130)
        if avg_prob is not None and avg_prob >= self._ml_prob_threshold:
            pen_width = 3.0
            pen_color = QColor("#B71C1C")

        rect = self.scene.addEllipse(
            QRectF(rx, ry, diameter, diameter),
            QPen(pen_color, pen_width),
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

        if avg_prob is None:
            ml_line = "ML=---"
        else:
            high = self._ml_high_count.get(node.individual_id, 0)
            cnt = self._ml_count.get(node.individual_id, 0)
            mark = "★" if avg_prob >= self._ml_prob_threshold else ""
            ml_line = f"ML={avg_prob:.3f}{mark} {high}/{cnt}"

        if node.cause:
            line3 = node.cause[:16]
        elif node.rank_all is not None:
            line3 = f"全{node.rank_all}/{self._ranked_all}"
            if node.rank_active is not None:
                line3 += f" 稼{node.rank_active}/{self._ranked_active}"
        else:
            line3 = node.status

        sub_lines: list[str] = [line2, ml_line, line3]
        if node.sire_id:
            sub_lines.append(f"♂{node.sire_id}")

        all_lines = [node.individual_id, *sub_lines]
        best_main_pt, best_sub_pt, line_gap = self._select_font_sizes(diameter, all_lines)
        font = self._font(best_main_pt)
        font_s = self._font(best_sub_pt)

        ml_text_color = QColor(0, 0, 0, alpha_sub)
        if avg_prob is not None:
            if avg_prob >= self._ml_prob_threshold:
                ml_text_color = QColor(183, 28, 28, alpha_sub)
            elif avg_prob >= 0.5:
                ml_text_color = QColor(90, 40, 40, alpha_sub)

        lines = [
            (node.individual_id, font, QColor(0, 0, 0, alpha_main)),
            (line2, font_s, QColor(0, 0, 0, alpha_sub)),
            (ml_line, font_s, ml_text_color),
            (line3, font_s, QColor(0, 0, 0, alpha_sub)),
        ]
        if node.sire_id:
            lines.append((f"♂{node.sire_id}", font_s, QColor(0, 0, 0, alpha_sub)))

        line_heights = [self._font_metrics(ln_font.pointSize()).height() for _t, ln_font, _c in lines]
        total_h = sum(line_heights) + line_gap * (len(lines) - 1)
        y = node.y - total_h / 2
        for i, (text, ln_font, ln_color) in enumerate(lines):
            item = self.scene.addSimpleText(text, ln_font)
            w = item.boundingRect().width()
            h = line_heights[i]
            item.setPos(node.x - w / 2, y)
            item.setBrush(QBrush(ln_color))
            y += h + line_gap

        return count

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

        base_width = 1.0 if in_focus else 0.8
        width = inherited_width if inherited_width is not None else base_width

        if parent.total_score is not None and child.total_score is not None:
            delta = child.total_score - parent.total_score
            if delta > 0:
                width *= self._excellent_line_multiplier
            elif delta < 0:
                width = max(base_width * 0.8, width / max(1.1, self._excellent_line_multiplier))

        parent_p = self._ml_avg_prob.get(parent.individual_id)
        child_p = self._ml_avg_prob.get(child.individual_id)
        if child_p is not None:
            if child_p >= self._ml_prob_threshold:
                width *= 1.25
            if parent_p is not None and child_p > parent_p:
                width *= 1.15
            if child_p < 0.35:
                width *= 0.90
            if child_p >= self._ml_prob_threshold:
                line_color = QColor("#B71C1C") if in_focus else QColor("#C97A7A")

        width = max(0.6, min(80.0, width))
        pen = QPen(line_color, width)
        self.scene.addLine(parent.x, parent.y, child.x, child.y, pen)
        return width
