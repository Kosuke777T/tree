"""Sow detail panel – shows per-parity scores and offspring details."""

from __future__ import annotations

import sqlite3

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class DetailPanel(QWidget):
    """Tab showing selected sow's full scoring breakdown."""

    def __init__(self, conn: sqlite3.Connection, parent=None):
        super().__init__(parent)
        self.conn = conn

        layout = QVBoxLayout(self)

        self.header_label = QLabel("母豚を選択してください")
        self.header_label.setFont(QFont("Meiryo", 12, QFont.Weight.Bold))
        layout.addWidget(self.header_label)

        # Sow summary
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        # Parity scores table
        layout.addWidget(QLabel("産歴別スコア"))
        self.parity_table = QTableWidget()
        self.parity_table.setColumnCount(19)
        self.parity_table.setHorizontalHeaderLabels([
            "産歴", "総産子", "生存産子", "死産", "黒子", "里子",
            "離乳", "事故率",
            "z(生存)", "z(総産)", "z(死産)", "z(自己率)",
            "産歴スコア", "全頭順位", "稼働順位",
            "子豚数", "PS出荷", "繰上げ", "PS/W率",
        ])
        hdr = self.parity_table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.parity_table)

        # Offspring table
        layout.addWidget(QLabel("子豚一覧"))
        self.piglet_table = QTableWidget()
        self.piglet_table.setColumnCount(6)
        self.piglet_table.setHorizontalHeaderLabels([
            "子豚№", "生年月日", "ランク", "乳評価",
            "PS出荷", "備考",
        ])
        hdr2 = self.piglet_table.horizontalHeader()
        hdr2.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.piglet_table)

    def show_sow(self, individual_id: str) -> None:
        """Populate panels for the selected sow."""
        self.header_label.setText(f"母豚: {individual_id}")

        # Sow summary
        sow = self.conn.execute(
            """SELECT s.*, sc.peak, sc.stability, sc.sustain,
                      sc.offspring_quality, sc.total_score,
                      sc.rank_all, sc.rank_active
               FROM sows s
               LEFT JOIN sow_scores sc ON s.individual_id = sc.individual_id
               WHERE s.individual_id = ?""",
            (individual_id,),
        ).fetchone()

        if sow:
            parts = [f"ステータス: {sow['status']}"]
            if sow["dam_id"]:
                parts.append(f"母: {sow['dam_id']}")
            if sow["sire_id"]:
                parts.append(f"父: {sow['sire_id']}")
            if sow["total_score"] is not None:
                parts.append(
                    f"TotalScore: {sow['total_score']:.3f}  "
                    f"(Peak={sow['peak']:.3f}  Stab={sow['stability']:.3f}  "
                    f"Sust={sow['sustain']:.3f}  OQ={sow['offspring_quality']:.3f})"
                )
            if sow["rank_all"] is not None:
                # Get totals for ranking context
                totals = self.conn.execute(
                    """SELECT count(*) AS total,
                              count(CASE WHEN s.status='active' THEN 1 END) AS active
                       FROM sow_scores sc
                       JOIN sows s ON sc.individual_id = s.individual_id"""
                ).fetchone()
                rank_str = f"全頭順位: {sow['rank_all']}/{totals['total']}"
                if sow["rank_active"] is not None:
                    rank_str += f"  稼働順位: {sow['rank_active']}/{totals['active']}"
                parts.append(rank_str)
            self.summary_label.setText("\n".join(parts))
        else:
            self.summary_label.setText("データなし")

        # Parity scores
        p_rows = self.conn.execute(
            """SELECT ps.*,
                      fr.total_born, fr.born_alive, fr.stillborn,
                      fr.mummified, fr.foster, fr.weaned
               FROM parity_scores ps
               LEFT JOIN farrowing_records fr
                 ON ps.individual_id = fr.individual_id
                 AND ps.parity = fr.parity
               WHERE ps.individual_id = ?
               ORDER BY ps.parity""",
            (individual_id,),
        ).fetchall()

        # Per-parity totals for ranking context
        parity_totals = {}
        for row in self.conn.execute(
            """SELECT ps.parity, count(*) AS total,
                      count(CASE WHEN s.status='active' THEN 1 END) AS active
               FROM parity_scores ps
               JOIN sows s ON ps.individual_id = s.individual_id
               GROUP BY ps.parity"""
        ).fetchall():
            parity_totals[row["parity"]] = (row["total"], row["active"])

        # Per-parity piglet stats (PS shipment, W promotion, total)
        piglet_stats = {}
        for row in self.conn.execute(
            """SELECT fr.parity,
                      COUNT(*) AS total,
                      SUM(CASE WHEN p.ps_shipment = '○' THEN 1 ELSE 0 END) AS ps,
                      SUM(CASE WHEN p.ps_shipment = 'W' THEN 1 ELSE 0 END) AS w
               FROM piglets p
               JOIN farrowing_records fr
                 ON p.dam_id = fr.individual_id
                 AND p.birth_date = fr.farrowing_date
               WHERE p.dam_id = ?
               GROUP BY fr.parity""",
            (individual_id,),
        ).fetchall():
            piglet_stats[row["parity"]] = (
                row["total"], row["ps"], row["w"])

        self.parity_table.setRowCount(len(p_rows))
        for i, r in enumerate(p_rows):
            pt = parity_totals.get(r["parity"], (0, 0))
            rank_all_str = (f"{r['rank_all']}/{pt[0]}"
                            if r["rank_all"] is not None else "")
            rank_active_str = (f"{r['rank_active']}/{pt[1]}"
                               if r["rank_active"] is not None else "")
            ps = piglet_stats.get(r["parity"], (0, 0, 0))
            pig_total, pig_ps, pig_w = ps
            pw_rate = ((pig_ps + pig_w) / pig_total * 100
                       if pig_total > 0 else 0)
            vals = [
                str(r["parity"]),
                str(r["total_born"]) if r["total_born"] is not None else "",
                str(r["born_alive"]) if r["born_alive"] is not None else "",
                str(r["stillborn"]) if r["stillborn"] is not None else "",
                str(r["mummified"]) if r["mummified"] is not None else "",
                str(r["foster"]) if r["foster"] is not None else "",
                str(r["weaned"]) if r["weaned"] is not None else "",
                f"{r['own_rate']:.2f}" if r["own_rate"] is not None else "",
                f"{r['z_live_born']:.3f}" if r["z_live_born"] is not None else "",
                f"{r['z_total_born']:.3f}" if r["z_total_born"] is not None else "",
                f"{r['z_stillborn']:.3f}" if r["z_stillborn"] is not None else "",
                f"{r['z_own_rate']:.3f}" if r["z_own_rate"] is not None else "",
                f"{r['parity_score']:.3f}" if r["parity_score"] is not None else "",
                rank_all_str,
                rank_active_str,
                str(pig_total) if pig_total > 0 else "",
                str(pig_ps) if pig_total > 0 else "",
                str(pig_w) if pig_total > 0 else "",
                f"{pw_rate:.0f}%" if pig_total > 0 else "",
            ]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.parity_table.setItem(i, j, item)

        # Piglets
        pig_rows = self.conn.execute(
            """SELECT piglet_no, birth_date, rank, teat_score,
                      ps_shipment, remarks
               FROM piglets WHERE dam_id = ?
               ORDER BY piglet_no""",
            (individual_id,),
        ).fetchall()

        self.piglet_table.setRowCount(len(pig_rows))
        for i, r in enumerate(pig_rows):
            vals = [
                r["piglet_no"] or "",
                r["birth_date"] or "",
                r["rank"] or "",
                str(r["teat_score"]) if r["teat_score"] is not None else "",
                r["ps_shipment"] or "",
                r["remarks"] or "",
            ]
            for j, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.piglet_table.setItem(i, j, item)
