"""Pedigree tree widget – QGraphicsScene/View with pan, zoom, search."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QMouseEvent, QPainter, QPainterPath, QPen, QWheelEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Layout constants
NODE_W = 160
NODE_H = 60
H_SPACING = 210
V_SPACING = 74
ZOOM_FACTOR = 1.15

# Colours
COL_ACTIVE = QColor("#4CAF50")
COL_DEAD = QColor("#9E9E9E")
COL_CULLED = QColor("#FF9800")
COL_TOP10 = QColor("#E53935")
COL_MOTHER_LINE = QColor("#D32F2F")
COL_FATHER_TAG = QColor("#1565C0")
COL_BG = QColor("#FAFAFA")


@dataclass
class TreeNode:
    individual_id: str
    dam_id: str | None = None
    sire_id: str | None = None
    status: str = "active"
    parity_count: int = 0
    total_score: float | None = None
    rank_all: int | None = None
    rank_active: int | None = None
    cause: str | None = None
    children: list[TreeNode] = field(default_factory=list)
    # layout
    x: float = 0.0
    y: float = 0.0
    generation: int = 0
    has_active: bool = False


class PedigreeView(QGraphicsView):
    """Zoomable, pannable graphics view."""

    node_double_clicked = pyqtSignal(str)  # individual_id

    def __init__(self, scene: QGraphicsScene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(
            QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(COL_BG))

    def wheelEvent(self, event: QWheelEvent):
        factor = ZOOM_FACTOR if event.angleDelta().y() > 0 else 1 / ZOOM_FACTOR
        self.scale(factor, factor)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        item = self.itemAt(event.pos())
        if item:
            sid = item.data(0)
            if not sid and item.parentItem():
                sid = item.parentItem().data(0)
            if sid:
                self.node_double_clicked.emit(sid)
        super().mouseDoubleClickEvent(event)


class PedigreeWidget(QWidget):
    """Full pedigree panel: toolbar + graphics view."""

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.all_nodes: dict[str, TreeNode] = {}
        self.root_nodes: list[TreeNode] = []
        self._node_items: dict[str, QGraphicsRectItem] = {}
        self._active_only = True  # default: show active branches only

        # ── Toolbar ──
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 0)

        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("個体番号で検索...")
        self.search_edit.returnPressed.connect(self._on_search)
        toolbar.addWidget(self.search_edit)

        btn_search = QPushButton("検索")
        btn_search.clicked.connect(self._on_search)
        toolbar.addWidget(btn_search)

        self.chk_active = QCheckBox("稼働母豚のみ")
        self.chk_active.setChecked(True)
        self.chk_active.stateChanged.connect(self._on_active_filter)
        toolbar.addWidget(self.chk_active)

        toolbar.addStretch()
        self.info_label = QLabel("")
        toolbar.addWidget(self.info_label)
        layout.addLayout(toolbar)

        # ── Scene / View ──
        self.scene = QGraphicsScene()
        self.view = PedigreeView(self.scene, self)
        layout.addWidget(self.view)

    # ── Data loading ──

    def load_data(self) -> None:
        """Build tree structure from DB."""
        self.all_nodes.clear()
        self.root_nodes.clear()

        rows = self.conn.execute(
            """SELECT s.individual_id, s.dam_id, s.sire_id, s.status,
                      sc.total_score, sc.rank_all, sc.rank_active
               FROM sows s
               LEFT JOIN sow_scores sc ON s.individual_id = sc.individual_id"""
        ).fetchall()

        for r in rows:
            node = TreeNode(
                individual_id=r["individual_id"],
                dam_id=r["dam_id"],
                sire_id=r["sire_id"],
                status=r["status"] or "active",
                total_score=r["total_score"],
                rank_all=r["rank_all"],
                rank_active=r["rank_active"],
            )
            self.all_nodes[node.individual_id] = node

        # Total counts for ranking display (scored sows only)
        rank_counts = self.conn.execute(
            """SELECT count(*) AS total,
                      count(CASE WHEN s.status='active' THEN 1 END) AS active
               FROM sow_scores sc
               JOIN sows s ON sc.individual_id = s.individual_id"""
        ).fetchone()
        self._ranked_all = rank_counts["total"] if rank_counts else 0
        self._ranked_active = rank_counts["active"] if rank_counts else 0

        # Parity count
        for r in self.conn.execute(
            "SELECT individual_id, MAX(parity) AS max_p "
            "FROM farrowing_records GROUP BY individual_id"
        ).fetchall():
            if r["individual_id"] in self.all_nodes:
                self.all_nodes[r["individual_id"]].parity_count = r["max_p"] or 0

        # Cull/death cause
        for r in self.conn.execute(
                "SELECT individual_id, cause FROM death_records").fetchall():
            if r["individual_id"] in self.all_nodes:
                self.all_nodes[r["individual_id"]].cause = r["cause"]
        for r in self.conn.execute(
                "SELECT individual_id, cause FROM cull_records").fetchall():
            if r["individual_id"] in self.all_nodes:
                self.all_nodes[r["individual_id"]].cause = r["cause"]

        # Build parent→child relationships (maternal)
        for node in self.all_nodes.values():
            node.children.clear()
        for node in self.all_nodes.values():
            if node.dam_id and node.dam_id in self.all_nodes:
                self.all_nodes[node.dam_id].children.append(node)

        # Find roots
        self.root_nodes = sorted(
            [n for n in self.all_nodes.values()
             if not n.dam_id or n.dam_id not in self.all_nodes],
            key=lambda n: n.individual_id,
        )

        # Compute generations and has_active (iterative to avoid stack overflow)
        self._compute_generations()
        self._compute_has_active()

        self._render()

    def _compute_generations(self) -> None:
        """Assign generation numbers using BFS from roots."""
        for root in self.root_nodes:
            stack = [(root, 0)]
            while stack:
                node, gen = stack.pop()
                node.generation = gen
                for child in node.children:
                    stack.append((child, gen + 1))

    def _compute_has_active(self) -> None:
        """Mark nodes with active descendants (bottom-up via post-order)."""
        for node in self.all_nodes.values():
            node.has_active = False

        # Topological order (parents before children) via BFS, then reverse
        order: list[TreeNode] = []
        for root in self.root_nodes:
            stack = [root]
            while stack:
                n = stack.pop()
                order.append(n)
                for c in n.children:
                    stack.append(c)

        # Bottom-up
        for node in reversed(order):
            if node.status == "active":
                node.has_active = True
            for child in node.children:
                if child.has_active:
                    node.has_active = True

    # ── Rendering ──

    def _render(self) -> None:
        self.scene.clear()
        self._node_items.clear()

        roots = self.root_nodes
        if self._active_only:
            roots = [r for r in roots if r.has_active]

        # Layout: assign y positions (DFS, iterative)
        y_counter = 0.0
        layout_stack: list[tuple[TreeNode, bool]] = []
        for root in reversed(roots):
            layout_stack.append((root, False))

        # Two-pass: first lay out leaves, then parents
        # Use simple recursive approach with iteration
        y_positions: dict[str, float] = {}

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

        # Compute top-10% threshold
        scored = [n for n in self.all_nodes.values()
                  if n.total_score is not None and n.total_score != 0]
        if scored:
            scored.sort(key=lambda n: n.total_score, reverse=True)
            idx = max(0, len(scored) // 10 - 1)
            top10_threshold = scored[idx].total_score
        else:
            top10_threshold = float("inf")

        # Draw all visible nodes and mother-line edges
        drawn = 0
        for root in roots:
            drawn += self._draw_subtree(root, top10_threshold)

        self.info_label.setText(
            f"表示: {drawn}頭 / 全{len(self.all_nodes)}頭  "
            f"(稼働: {sum(1 for n in self.all_nodes.values() if n.status == 'active')})"
        )

    def _draw_subtree(self, node: TreeNode, top10_thr: float) -> int:
        if self._active_only and not node.has_active:
            return 0

        count = 1
        color = self._node_color(node, top10_thr)

        # Node box
        rect = self.scene.addRect(
            QRectF(node.x, node.y, NODE_W, NODE_H),
            QPen(color.darker(130), 1.5),
            QBrush(color),
        )
        rect.setData(0, node.individual_id)
        rect.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self._node_items[node.individual_id] = rect

        # ── Text labels ──
        font = QFont("Meiryo", 8)
        font_s = QFont("Meiryo", 7)

        # Line 1: individual_id
        t = self.scene.addSimpleText(node.individual_id, font)
        t.setPos(node.x + 4, node.y + 2)
        t.setBrush(QBrush(Qt.GlobalColor.white))

        # Line 2: parity + score
        line2 = f"産歴{node.parity_count}"
        if node.total_score is not None:
            line2 += f"  S={node.total_score:+.2f}"
        t2 = self.scene.addSimpleText(line2, font_s)
        t2.setPos(node.x + 4, node.y + 18)
        t2.setBrush(QBrush(QColor(255, 255, 255, 200)))

        # Line 3: rank or cause
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
        t3.setBrush(QBrush(QColor(255, 255, 255, 170)))

        # Line 4: sire (father) in blue
        if node.sire_id:
            ts = self.scene.addSimpleText(f"♂{node.sire_id}", font_s)
            ts.setPos(node.x + 4, node.y + 46)
            ts.setBrush(QBrush(COL_FATHER_TAG))

        # Mother lines + recurse children
        visible = [c for c in node.children
                   if not self._active_only or c.has_active]
        for child in visible:
            self._draw_mother_line(node, child)
            count += self._draw_subtree(child, top10_thr)

        return count

    def _node_color(self, node: TreeNode, top10_thr: float) -> QColor:
        if (node.total_score is not None and
                node.total_score >= top10_thr and top10_thr != float("inf")):
            return COL_TOP10
        if node.status == "dead":
            return COL_DEAD
        if node.status == "culled":
            return COL_CULLED
        if node.status == "inactive":
            return COL_CULLED  # 未生産18ヶ月超：廃豚と同じオレンジ
        return COL_ACTIVE

    def _draw_mother_line(self, parent: TreeNode, child: TreeNode) -> None:
        pen = QPen(COL_MOTHER_LINE, 1.5)
        x1 = parent.x + NODE_W
        y1 = parent.y + NODE_H / 2
        x2 = child.x
        y2 = child.y + NODE_H / 2
        path = QPainterPath(QPointF(x1, y1))
        mx = (x1 + x2) / 2
        path.cubicTo(QPointF(mx, y1), QPointF(mx, y2), QPointF(x2, y2))
        self.scene.addPath(path, pen)

    # ── Toolbar actions ──

    def _on_search(self) -> None:
        query = self.search_edit.text().strip().upper()
        if not query:
            return

        # Find match (exact, then contains)
        target = None
        if query in self._node_items:
            target = query
        else:
            for sid in self._node_items:
                if query in sid.upper():
                    target = sid
                    break

        if not target:
            # Node might be filtered out – turn off filter and re-render
            if self._active_only:
                self._active_only = False
                self.chk_active.setChecked(False)
                self._render()
                # Try again
                for sid in self._node_items:
                    if query in sid.upper():
                        target = sid
                        break

        if target and target in self._node_items:
            item = self._node_items[target]
            self.view.centerOn(item)
            self.scene.clearSelection()
            item.setSelected(True)

    def _on_active_filter(self, state: int) -> None:
        self._active_only = state == Qt.CheckState.Checked.value
        self._render()
