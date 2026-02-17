"""Main window – tab-based UI with pedigree and detail panels."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.db.connection import DB_PATH, get_connection
from app.db.schema import init_db
from app.etl.pipeline import run_etl
from app.gui.detail_panel import DetailPanel
from app.gui.ml_panel import MLPanel
from app.gui.pedigree_widget import PedigreeWidget
from app.gui.pedigree_widget2 import PedigreeWidget2
from app.gui.pedigree_widget3 import PedigreeWidget3
from app.gui.pedigree_widget4 import PedigreeWidget4
from app.export.html_report import export_html_report
from app.scoring.engine import run_scoring


class ExportWorker(QThread):
    """Background thread for HTML report export."""
    progress = pyqtSignal(str)
    finished = pyqtSignal(str)  # output file path
    error = pyqtSignal(str)

    def __init__(self, db_path: str, output_dir: str):
        super().__init__()
        self.db_path = db_path
        self.output_dir = output_dir

    def run(self):
        try:
            conn = get_connection(self.db_path)
            path = export_html_report(
                conn, Path(self.output_dir),
                progress_cb=self.progress.emit,
            )
            conn.close()
            self.finished.emit(str(path))
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


class ETLWorker(QThread):
    """Background thread for ETL + scoring.

    Creates its own SQLite connection inside run() for thread safety.
    """
    progress = pyqtSignal(str)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path

    def run(self):
        try:
            conn = get_connection(self.db_path)
            counts = run_etl(conn, progress_cb=self.progress.emit)
            run_scoring(conn, progress_cb=self.progress.emit)
            conn.close()
            self.finished.emit(counts)
        except Exception as e:
            import traceback
            self.error.emit(traceback.format_exc())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("tree — 養豚家系図・系統評価")
        self.resize(1400, 900)

        self.conn = get_connection()
        init_db(self.conn)

        # Menu bar
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("ファイル")
        export_action = QAction("HTMLレポート出力...", self)
        export_action.triggered.connect(self._on_export_html)
        file_menu.addAction(export_action)

        # Central widget: shared toolbar + tabs
        self.tabs = QTabWidget()

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(4, 4, 4, 0)
        central_layout.setSpacing(2)

        # Shared toolbar (search + remark filter)
        shared_toolbar = QHBoxLayout()

        self.shared_search_edit = QLineEdit()
        self.shared_search_edit.setPlaceholderText("個体番号で検索...")
        self.shared_search_edit.returnPressed.connect(self._on_shared_search)
        shared_toolbar.addWidget(self.shared_search_edit)

        btn_search = QPushButton("検索")
        btn_search.clicked.connect(self._on_shared_search)
        shared_toolbar.addWidget(btn_search)

        self.shared_remark_edit = QLineEdit()
        self.shared_remark_edit.setPlaceholderText("備考キーワード...")
        self.shared_remark_edit.setFixedWidth(120)
        self.shared_remark_edit.textChanged.connect(self._on_shared_remark_changed)
        shared_toolbar.addWidget(self.shared_remark_edit)

        self.shared_remark_slider = QSlider(Qt.Orientation.Horizontal)
        self.shared_remark_slider.setRange(0, 100)
        self.shared_remark_slider.setValue(0)
        self.shared_remark_slider.setFixedWidth(120)
        self.shared_remark_slider.valueChanged.connect(self._on_shared_remark_changed)
        shared_toolbar.addWidget(self.shared_remark_slider)

        self.shared_remark_label = QLabel("0%")
        shared_toolbar.addWidget(self.shared_remark_label)

        btn_up = QPushButton("▲")
        btn_up.setFixedWidth(24)
        btn_up.clicked.connect(lambda: self.shared_remark_slider.setValue(
            min(100, self.shared_remark_slider.value() + 1)))
        shared_toolbar.addWidget(btn_up)

        btn_down = QPushButton("▼")
        btn_down.setFixedWidth(24)
        btn_down.clicked.connect(lambda: self.shared_remark_slider.setValue(
            max(0, self.shared_remark_slider.value() - 1)))
        shared_toolbar.addWidget(btn_down)

        shared_toolbar.addStretch()
        central_layout.addLayout(shared_toolbar)
        central_layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        self.pedigree = PedigreeWidget(self.conn)
        self.tabs.addTab(self.pedigree, "家系図")

        self.pedigree2 = PedigreeWidget2(self.conn)
        self.tabs.addTab(self.pedigree2, "家系図2")

        self.pedigree3 = PedigreeWidget3(self.conn)
        self.tabs.addTab(self.pedigree3, "家系図3")

        self.pedigree4 = PedigreeWidget4(self.conn)
        self.tabs.addTab(self.pedigree4, "家系図4")

        self.detail = DetailPanel(self.conn)
        self.tabs.addTab(self.detail, "母豚詳細")

        self.ml_panel = MLPanel(self.conn)
        self.tabs.addTab(self.ml_panel, "ML分析")

        # Connect pedigree double-click → detail
        self.pedigree.view.node_double_clicked.connect(self._on_pedigree_dblclick)
        self.pedigree2.view.node_double_clicked.connect(self._on_pedigree_dblclick)
        self.pedigree3.view.node_double_clicked.connect(self._on_pedigree_dblclick)
        self.pedigree4.view.node_double_clicked.connect(self._on_pedigree_dblclick)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(300)
        self.progress_bar.setRange(0, 0)  # indeterminate
        self.progress_bar.hide()
        self.status_bar.addPermanentWidget(self.progress_bar)

        # Check if DB already has data
        sow_count = self.conn.execute(
            "SELECT count(*) FROM sows").fetchone()[0]
        if sow_count > 0:
            # Ensure scoring tables are populated
            score_count = self.conn.execute(
                "SELECT count(*) FROM sow_scores").fetchone()[0]
            if score_count == 0:
                self.status_bar.showMessage("スコア再計算中...")
                run_scoring(self.conn, progress_cb=lambda m:
                            self.status_bar.showMessage(m))
            self.status_bar.showMessage(
                f"既存DB読み込み — 母豚{sow_count}頭")
            self.pedigree.load_data()
            self.pedigree2.load_data()
            self.pedigree3.load_data()
            self.pedigree4.load_data()
        else:
            self._start_etl()

    def _start_etl(self) -> None:
        self.progress_bar.show()
        self.status_bar.showMessage("データ読み込み中...")

        # Close main connection during ETL to avoid locks
        self.conn.close()

        self.worker = ETLWorker(str(DB_PATH))
        self.worker.progress.connect(
            lambda msg: self.status_bar.showMessage(msg))
        self.worker.finished.connect(self._on_etl_done)
        self.worker.error.connect(self._on_etl_error)
        self.worker.start()

    def _on_etl_done(self, counts: dict) -> None:
        self.progress_bar.hide()

        # Reopen main connection
        self.conn = get_connection()
        self.pedigree.conn = self.conn
        self.pedigree2.conn = self.conn
        self.pedigree3.conn = self.conn
        self.pedigree4.conn = self.conn
        self.detail.conn = self.conn
        self.ml_panel.conn = self.conn

        summary = ", ".join(f"{k}: {v}" for k, v in counts.items())
        self.status_bar.showMessage(f"読み込み完了 — {summary}")
        self.pedigree.load_data()
        self.pedigree2.load_data()
        self.pedigree3.load_data()
        self.pedigree4.load_data()

    def _on_etl_error(self, msg: str) -> None:
        self.progress_bar.hide()

        # Reopen main connection
        self.conn = get_connection()
        self.pedigree.conn = self.conn
        self.pedigree2.conn = self.conn
        self.pedigree3.conn = self.conn
        self.pedigree4.conn = self.conn
        self.detail.conn = self.conn
        self.ml_panel.conn = self.conn

        self.status_bar.showMessage("ETLエラー")
        QMessageBox.critical(self, "ETLエラー", msg)

    def _on_shared_search(self) -> None:
        query = self.shared_search_edit.text().strip()
        if not query:
            return
        for w in [self.pedigree, self.pedigree2, self.pedigree3, self.pedigree4]:
            w._on_search(query)

    def _on_shared_remark_changed(self) -> None:
        keyword = self.shared_remark_edit.text().strip()
        threshold = self.shared_remark_slider.value()
        self.shared_remark_label.setText(f"{threshold}%")
        for w in [self.pedigree, self.pedigree2, self.pedigree3, self.pedigree4]:
            w.set_remark_filter(keyword, threshold)

    def _on_pedigree_dblclick(self, individual_id: str) -> None:
        self.detail.show_sow(individual_id)
        self.ml_panel.show_sow(individual_id)
        self.tabs.setCurrentWidget(self.detail)

    # ── HTML Export ──

    def _on_export_html(self) -> None:
        default_dir = str(Path.home() / "OneDrive")
        if not Path(default_dir).exists():
            default_dir = str(Path.home())
        output_dir = QFileDialog.getExistingDirectory(
            self, "レポート出力先を選択", default_dir)
        if not output_dir:
            return

        self.progress_bar.show()
        self.status_bar.showMessage("HTMLレポート生成中...")

        self._export_worker = ExportWorker(str(DB_PATH), output_dir)
        self._export_worker.progress.connect(
            lambda msg: self.status_bar.showMessage(msg))
        self._export_worker.finished.connect(self._on_export_done)
        self._export_worker.error.connect(self._on_export_error)
        self._export_worker.start()

    def _on_export_done(self, file_path: str) -> None:
        self.progress_bar.hide()
        self.status_bar.showMessage(f"レポート出力完了: {file_path}")
        QMessageBox.information(
            self, "エクスポート完了",
            f"HTMLレポートを出力しました:\n{file_path}")

    def _on_export_error(self, msg: str) -> None:
        self.progress_bar.hide()
        self.status_bar.showMessage("エクスポートエラー")
        QMessageBox.critical(self, "エクスポートエラー", msg)


if __name__ == "__main__":
    import sys
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
