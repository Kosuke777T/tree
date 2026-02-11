"""ETL pipeline – Excel → SQLite (idempotent: truncate + insert)."""

from __future__ import annotations

import sqlite3

from app.db.schema import init_db, reset_data_tables
from app.etl.loaders import (
    load_breeding,
    load_culls,
    load_deaths,
    load_farrowing,
    load_piglets,
)


def _collect_sow_ids(*record_lists: list[dict]) -> set[str]:
    """Gather all unique individual_id values from record lists."""
    ids: set[str] = set()
    for records in record_lists:
        for r in records:
            sid = r.get("individual_id")
            if sid:
                ids.add(sid)
    return ids


def _insert_sows_from_piglets(conn: sqlite3.Connection,
                              piglets: list[dict]) -> None:
    """Promote piglets with ps_shipment='W' to sows and set dam/sire links."""
    for p in piglets:
        if p["ps_shipment"] == "W":
            sow_id = "TB" + p["piglet_no"]
            conn.execute(
                """INSERT OR IGNORE INTO sows
                   (individual_id, source_piglet_no, dam_id, sire_id,
                    birth_date, rank, teat_score, remarks, status)
                   VALUES (?,?,?,?,?,?,?,?, 'active')""",
                (sow_id, p["piglet_no"], p["dam_id"], p["sire_id"],
                 p["birth_date"], p["rank"], p["teat_score"], p["remarks"]),
            )
        # Also ensure every dam_id exists in sows
        if p["dam_id"]:
            conn.execute(
                "INSERT OR IGNORE INTO sows (individual_id) VALUES (?)",
                (p["dam_id"],),
            )


def _update_sow_status(conn: sqlite3.Connection,
                       deaths: list[dict], culls: list[dict]) -> None:
    for d in deaths:
        conn.execute(
            "UPDATE sows SET status='dead' WHERE individual_id=?",
            (d["individual_id"],),
        )
    for c in culls:
        conn.execute(
            "UPDATE sows SET status='culled' WHERE individual_id=?",
            (c["individual_id"],),
        )


def _enrich_sow_parents(conn: sqlite3.Connection,
                        piglets: list[dict]) -> None:
    """For sows that were promoted from piglets, set dam/sire from piglet record."""
    for p in piglets:
        if p["ps_shipment"] == "W":
            sow_id = "TB" + p["piglet_no"]
            conn.execute(
                """UPDATE sows SET dam_id=?, sire_id=?, birth_date=?
                   WHERE individual_id=? AND dam_id IS NULL""",
                (p["dam_id"], p["sire_id"], p["birth_date"], sow_id),
            )


def _bulk_insert(conn: sqlite3.Connection, table: str,
                 rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0].keys())
    placeholders = ",".join(["?"] * len(cols))
    col_names = ",".join(cols)
    sql = f"INSERT OR IGNORE INTO {table} ({col_names}) VALUES ({placeholders})"
    count = 0
    for r in rows:
        try:
            conn.execute(sql, tuple(r[c] for c in cols))
            count += 1
        except sqlite3.IntegrityError:
            pass
    return count


def run_etl(conn: sqlite3.Connection,
            progress_cb=None) -> dict[str, int]:
    """Execute full ETL pipeline. Returns row counts per table."""
    init_db(conn)
    reset_data_tables(conn)

    def _progress(msg: str):
        if progress_cb:
            progress_cb(msg)

    _progress("Excel読み込み: 種付記録...")
    breeding = load_breeding()
    _progress("Excel読み込み: 分娩記録...")
    farrowing = load_farrowing()
    _progress("Excel読み込み: 死亡記録...")
    deaths = load_deaths()
    _progress("Excel読み込み: 廃豚記録...")
    culls = load_culls()
    _progress("Excel読み込み: 子豚記録...")
    piglets = load_piglets()

    _progress("母豚マスタ構築...")
    sow_ids = _collect_sow_ids(breeding, farrowing, deaths, culls)
    for sid in sow_ids:
        conn.execute(
            "INSERT OR IGNORE INTO sows (individual_id) VALUES (?)",
            (sid,),
        )
    _insert_sows_from_piglets(conn, piglets)

    _progress("レコードINSERT...")
    counts = {}
    counts["breeding"] = _bulk_insert(conn, "breeding_records", breeding)
    counts["farrowing"] = _bulk_insert(conn, "farrowing_records", farrowing)
    counts["deaths"] = _bulk_insert(conn, "death_records", deaths)
    counts["culls"] = _bulk_insert(conn, "cull_records", culls)
    counts["piglets"] = _bulk_insert(conn, "piglets", piglets)

    _progress("ステータス更新...")
    _update_sow_status(conn, deaths, culls)
    _enrich_sow_parents(conn, piglets)
    conn.commit()

    counts["sows"] = conn.execute("SELECT count(*) FROM sows").fetchone()[0]
    _progress("ETL完了")
    return counts
