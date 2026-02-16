"""ML Analysis panel – LightGBM training, SHAP visualization, individual search."""

from __future__ import annotations

import sqlite3
import json

import numpy as np
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import matplotlib
matplotlib.use("QtAgg")

# 日本語表示用フォント設定（文字化け防止）
_ja_fonts = ["Meiryo", "Yu Gothic UI", "MS Gothic", "Yu Gothic", "MS PGothic"]
matplotlib.rcParams["font.sans-serif"] = _ja_fonts + list(matplotlib.rcParams["font.sans-serif"])
matplotlib.rcParams["axes.unicode_minus"] = False

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from app.db.connection import get_connection
from app.scoring.ml_engine import MLEngine
from app.scoring.ml_features import FEATURE_NAMES_JA


class _TrainWorker(QThread):
    """Background thread for ML training + prediction."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, db_path: str, engine: MLEngine):
        super().__init__()
        self.db_path = db_path
        self.engine = engine

    def run(self):
        try:
            conn = get_connection(self.db_path)
            metrics = self.engine.train(conn, progress_cb=self.progress.emit)
            self.engine.predict_all(conn, progress_cb=self.progress.emit)

            # Compute global SHAP for importance chart
            self.progress.emit("SHAP重要度計算中...")
            names, vals = self.engine.get_global_shap(conn)
            metrics["shap_names"] = list(names)
            metrics["shap_values"] = vals.tolist()

            conn.close()
            self.finished.emit(metrics)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


class MLPanel(QWidget):
    """ML analysis tab with training, SHAP importance, and individual search."""

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.conn = conn
        self.engine = MLEngine()
        self._worker = None

        layout = QVBoxLayout(self)

        # ── Top: Train controls + CV results ──
        top_group = QGroupBox("モデル学習")
        top_layout = QHBoxLayout(top_group)

        self.train_btn = QPushButton("モデル学習")
        self.train_btn.setFixedWidth(120)
        self.train_btn.clicked.connect(self._on_train)
        top_layout.addWidget(self.train_btn)

        self.status_label = QLabel("未学習")
        self.status_label.setWordWrap(True)
        top_layout.addWidget(self.status_label, 1)

        layout.addWidget(top_group)

        # ── Middle: SHAP importance chart ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: SHAP bar chart
        shap_group = QGroupBox("特徴量重要度 (SHAP)")
        shap_layout = QVBoxLayout(shap_group)
        self.shap_figure = Figure(figsize=(6, 5))
        self.shap_canvas = FigureCanvasQTAgg(self.shap_figure)
        shap_layout.addWidget(self.shap_canvas)
        splitter.addWidget(shap_group)

        # Right: Individual search + waterfall
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Search bar
        search_group = QGroupBox("個体検索")
        search_layout = QHBoxLayout(search_group)
        search_layout.addWidget(QLabel("母豚ID:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("例: TBA00123")
        self.search_input.returnPressed.connect(self._on_search)
        search_layout.addWidget(self.search_input)
        self.search_btn = QPushButton("検索")
        self.search_btn.clicked.connect(self._on_search)
        search_layout.addWidget(self.search_btn)
        right_layout.addWidget(search_group)

        # Prediction table
        self.pred_table = QTableWidget()
        self.pred_table.setColumnCount(3)
        self.pred_table.setHorizontalHeaderLabels(["産歴", "優秀確率", "判定"])
        hdr = self.pred_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.pred_table.setMaximumHeight(200)
        self.pred_table.itemSelectionChanged.connect(
            self._on_parity_selected)
        right_layout.addWidget(self.pred_table)

        # Individual SHAP waterfall
        self.ind_figure = Figure(figsize=(6, 4))
        self.ind_canvas = FigureCanvasQTAgg(self.ind_figure)
        right_layout.addWidget(self.ind_canvas)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)

        layout.addWidget(splitter, 1)

    def show_sow(self, individual_id: str) -> None:
        """Navigate to a specific sow's ML predictions."""
        self.search_input.setText(individual_id)
        self._on_search()

    def _on_train(self) -> None:
        self.train_btn.setEnabled(False)
        self.status_label.setText("学習中...")

        from app.db.connection import DB_PATH
        self._worker = _TrainWorker(str(DB_PATH), self.engine)
        self._worker.progress.connect(
            lambda msg: self.status_label.setText(msg))
        self._worker.finished.connect(self._on_train_done)
        self._worker.error.connect(self._on_train_error)
        self._worker.start()

    def _on_train_done(self, metrics: dict) -> None:
        self.train_btn.setEnabled(True)

        auc = metrics["cv_auc"]
        acc = metrics["cv_accuracy"]
        f1 = metrics["cv_f1"]
        n_pos = metrics["n_positive"]
        n_total = metrics["n_total"]

        self.status_label.setText(
            f"学習完了  |  AUC: {auc:.4f}  Accuracy: {acc:.4f}  "
            f"F1: {f1:.4f}  |  優秀: {n_pos}/{n_total} "
            f"({n_pos / n_total * 100:.1f}%)"
        )

        # Draw SHAP importance chart
        self._draw_shap_importance(
            metrics["shap_names"], metrics["shap_values"])

        # Refresh connection for predictions
        self.conn = get_connection()

    def _on_train_error(self, msg: str) -> None:
        self.train_btn.setEnabled(True)
        self.status_label.setText(f"エラー: {msg[:200]}")

    def _draw_shap_importance(self, names: list[str],
                              values: list[float]) -> None:
        """Draw horizontal bar chart of mean |SHAP| values."""
        self.shap_figure.clear()
        ax = self.shap_figure.add_subplot(111)

        # Sort by importance (ascending for horizontal bars)
        indices = np.argsort(values)
        sorted_names = [FEATURE_NAMES_JA.get(names[i], names[i])
                        for i in indices]
        sorted_vals = [values[i] for i in indices]

        colors = ["#1f77b4" if v > np.median(values) else "#aec7e8"
                  for v in sorted_vals]

        ax.barh(range(len(sorted_names)), sorted_vals, color=colors)
        ax.set_yticks(range(len(sorted_names)))
        ax.set_yticklabels(sorted_names, fontsize=8)
        ax.set_xlabel("Mean |SHAP value|", fontsize=9)
        ax.set_title("特徴量重要度", fontsize=11)

        self.shap_figure.tight_layout()
        self.shap_canvas.draw()

    def _on_search(self) -> None:
        """Search for individual sow predictions."""
        individual_id = self.search_input.text().strip()
        if not individual_id:
            return

        rows = self.conn.execute(
            """SELECT parity, pred_excellent_prob, shap_json
               FROM ml_predictions
               WHERE individual_id = ?
               ORDER BY parity""",
            (individual_id,),
        ).fetchall()

        self.pred_table.setRowCount(len(rows))
        self._search_rows = rows

        for i, r in enumerate(rows):
            prob = r["pred_excellent_prob"]
            label = "優秀" if prob >= 0.5 else "普通"

            items = [
                str(r["parity"]),
                f"{prob:.3f}",
                label,
            ]
            for j, v in enumerate(items):
                item = QTableWidgetItem(v)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if j == 2:
                    if label == "優秀":
                        item.setForeground(Qt.GlobalColor.darkGreen)
                    else:
                        item.setForeground(Qt.GlobalColor.gray)
                self.pred_table.setItem(i, j, item)

        # Auto-select first row
        if rows:
            self.pred_table.selectRow(0)

    def _on_parity_selected(self) -> None:
        """Draw SHAP waterfall for selected parity."""
        sel = self.pred_table.selectedItems()
        if not sel or not hasattr(self, "_search_rows"):
            return

        row_idx = sel[0].row()
        if row_idx >= len(self._search_rows):
            return

        r = self._search_rows[row_idx]
        shap_json = r["shap_json"]
        if not shap_json:
            return

        shap_dict = json.loads(shap_json)
        self._draw_waterfall(shap_dict, r["parity"],
                             r["pred_excellent_prob"])

    def _draw_waterfall(self, shap_dict: dict, parity: int,
                        prob: float) -> None:
        """Draw a simplified waterfall chart for one prediction."""
        self.ind_figure.clear()
        ax = self.ind_figure.add_subplot(111)

        # Sort features by absolute SHAP value
        items = sorted(shap_dict.items(), key=lambda x: abs(x[1]))

        # Show top 15 features
        if len(items) > 15:
            items = items[-15:]

        names = [FEATURE_NAMES_JA.get(k, k) for k, _ in items]
        vals = [v for _, v in items]

        colors = ["#ff7f7f" if v > 0 else "#7fbfff" for v in vals]
        ax.barh(range(len(names)), vals, color=colors)
        ax.set_yticks(range(len(names)))
        ax.set_yticklabels(names, fontsize=8)
        ax.set_xlabel("SHAP value", fontsize=9)
        ax.axvline(x=0, color="black", linewidth=0.5)

        label = "優秀" if prob >= 0.5 else "普通"
        ax.set_title(f"産歴{parity} — 確率: {prob:.3f} ({label})",
                     fontsize=11)

        self.ind_figure.tight_layout()
        self.ind_canvas.draw()
