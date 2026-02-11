"""Main window – tab-based UI with pedigree and detail panels."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QStatusBar,
    QTabWidget,
)

from app.db.connection import DB_PATH, get_connection
from app.db.schema import init_db
from app.etl.pipeline import run_etl
from app.gui.detail_panel import DetailPanel
from app.gui.pedigree_widget import PedigreeWidget
from app.scoring.engine import run_scoring


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

        # Central tabs
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.pedigree = PedigreeWidget(self.conn)
        self.tabs.addTab(self.pedigree, "家系図")

        self.detail = DetailPanel(self.conn)
        self.tabs.addTab(self.detail, "母豚詳細")

        # Connect pedigree selection → detail
        self.pedigree.scene.selectionChanged.connect(self._on_pedigree_select)

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
            self.status_bar.showMessage(
                f"既存DB読み込み — 母豚{sow_count}頭")
            self.pedigree.load_data()
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
        self.detail.conn = self.conn

        summary = ", ".join(f"{k}: {v}" for k, v in counts.items())
        self.status_bar.showMessage(f"読み込み完了 — {summary}")
        self.pedigree.load_data()

    def _on_etl_error(self, msg: str) -> None:
        self.progress_bar.hide()

        # Reopen main connection
        self.conn = get_connection()
        self.pedigree.conn = self.conn
        self.detail.conn = self.conn

        self.status_bar.showMessage("ETLエラー")
        QMessageBox.critical(self, "ETLエラー", msg)

    def _on_pedigree_select(self) -> None:
        items = self.pedigree.scene.selectedItems()
        if not items:
            return
        item = items[0]
        sid = item.data(0)
        if sid:
            self.detail.show_sow(sid)
            self.tabs.setCurrentWidget(self.detail)
