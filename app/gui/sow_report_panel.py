"""SowReportPanel — 母豚成績一覧タブ。

成績順にソートされた母豚一覧を表示し、
ダブルクリックで家系図検索シグナルを emit する。
"""

from __future__ import annotations

import sqlite3

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

_SQL_ALL = """
SELECT
    sc.rank_all,
    sc.rank_active,
    s.individual_id,
    s.status,
    COALESCE(MAX(fr.parity), 0) AS parity_count,
    sc.total_score,
    COALESCE(s.dam_id, '') AS dam_id,
    COALESCE(s.sire_id, '') AS sire_id
FROM sows s
LEFT JOIN sow_scores sc ON s.individual_id = sc.individual_id
LEFT JOIN farrowing_records fr ON s.individual_id = fr.individual_id
GROUP BY s.individual_id
ORDER BY sc.rank_all NULLS LAST
"""

_SQL_ACTIVE = """
SELECT
    sc.rank_all,
    sc.rank_active,
    s.individual_id,
    s.status,
    COALESCE(MAX(fr.parity), 0) AS parity_count,
    sc.total_score,
    COALESCE(s.dam_id, '') AS dam_id,
    COALESCE(s.sire_id, '') AS sire_id
FROM sows s
LEFT JOIN sow_scores sc ON s.individual_id = sc.individual_id
LEFT JOIN farrowing_records fr ON s.individual_id = fr.individual_id
WHERE s.status = 'active'
GROUP BY s.individual_id
ORDER BY sc.rank_active NULLS LAST
"""

_STATUS_LABEL = {
    "active": "稼働",
    "dead": "死亡",
    "culled": "廃豚",
}

_HEADERS = ["全頭順位", "稼働順位", "個体番号", "ステータス", "産歴", "総合スコア", "母番号", "父番号"]


class SowReportPanel(QWidget):
    """母豚成績一覧パネル。"""

    search_requested = pyqtSignal(str)  # 個体番号を emit

    def __init__(self, conn: sqlite3.Connection | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.conn = conn
        self._show_active = False
        self._all_rows: list[tuple] = []

        # ── toolbar ──
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)

        self.btn_all = QPushButton("全頭表示")
        self.btn_all.setCheckable(True)
        self.btn_all.setChecked(True)

        self.btn_active = QPushButton("稼働表示")
        self.btn_active.setCheckable(True)

        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.btn_all)
        group.addButton(self.btn_active)

        self.btn_all.clicked.connect(self._on_btn_all)
        self.btn_active.clicked.connect(self._on_btn_active)

        toolbar.addWidget(self.btn_all)
        toolbar.addWidget(self.btn_active)
        toolbar.addStretch()

        # ── table ──
        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.itemDoubleClicked.connect(self._on_double_click)

        # ── layout ──
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(toolbar)
        layout.addWidget(self.table)

    # ── public ──

    def refresh(self) -> None:
        """DB から全件取得してテーブルを再描画する。"""
        if self.conn is None:
            return
        try:
            rows = self.conn.execute(_SQL_ALL).fetchall()
        except Exception:
            rows = []
        self._all_rows = rows
        self._apply_filter()

    # ── private ──

    def _on_btn_all(self) -> None:
        self._show_active = False
        self._apply_filter()

    def _on_btn_active(self) -> None:
        self._show_active = True
        self._apply_filter()

    def _apply_filter(self) -> None:
        if self._show_active:
            try:
                rows = self.conn.execute(_SQL_ACTIVE).fetchall() if self.conn else []
            except Exception:
                rows = []
        else:
            rows = self._all_rows

        self.table.setRowCount(0)
        for row_data in rows:
            rank_all, rank_active, individual_id, status, parity, score, dam_id, sire_id = row_data

            row_idx = self.table.rowCount()
            self.table.insertRow(row_idx)

            def _item(text: str, align=Qt.AlignmentFlag.AlignCenter) -> QTableWidgetItem:
                it = QTableWidgetItem(text)
                it.setTextAlignment(align)
                return it

            self.table.setItem(row_idx, 0, _item("" if rank_all is None else str(rank_all)))
            self.table.setItem(row_idx, 1, _item("" if rank_active is None else str(rank_active)))
            self.table.setItem(row_idx, 2, _item(individual_id or ""))
            self.table.setItem(row_idx, 3, _item(_STATUS_LABEL.get(status or "", status or "")))
            self.table.setItem(row_idx, 4, _item(str(parity) if parity else "0"))
            self.table.setItem(
                row_idx, 5,
                _item(f"{score:.3f}" if score is not None else ""),
            )
            self.table.setItem(row_idx, 6, _item(dam_id or "", Qt.AlignmentFlag.AlignLeft))
            self.table.setItem(row_idx, 7, _item(sire_id or "", Qt.AlignmentFlag.AlignLeft))

        self.table.resizeColumnsToContents()

    def _on_double_click(self, item: QTableWidgetItem) -> None:
        row = item.row()
        id_item = self.table.item(row, 2)
        if id_item is None:
            return
        individual_id = id_item.text().strip()
        if individual_id:
            self.search_requested.emit(individual_id)
