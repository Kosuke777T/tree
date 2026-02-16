"""DDL definitions and database initialisation."""

from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS sows (
    individual_id   TEXT PRIMARY KEY,
    source_piglet_no TEXT,
    dam_id          TEXT REFERENCES sows(individual_id),
    sire_id         TEXT,
    birth_date      TEXT,
    rank            TEXT,
    teat_score      INTEGER,
    remarks         TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_sows_dam    ON sows(dam_id);
CREATE INDEX IF NOT EXISTS idx_sows_status ON sows(status);

CREATE TABLE IF NOT EXISTS piglets (
    piglet_no       TEXT PRIMARY KEY,
    birth_date      TEXT,
    rank            TEXT,
    teat_score      INTEGER,
    remarks         TEXT,
    shipment_dest   TEXT,
    ps_shipment     TEXT,
    shipment_date   TEXT,
    dam_id          TEXT REFERENCES sows(individual_id),
    sire_id         TEXT,
    shipment_age    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_piglets_dam ON piglets(dam_id);

CREATE TABLE IF NOT EXISTS breeding_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    individual_id   TEXT NOT NULL REFERENCES sows(individual_id),
    parity          INTEGER NOT NULL,
    breeding_date   TEXT,
    breeding_type   TEXT,
    sire_first      TEXT,
    sire_second     TEXT,
    return_to_estrus TEXT,
    age_days        INTEGER,
    status          TEXT,
    UNIQUE(individual_id, parity)
);
CREATE INDEX IF NOT EXISTS idx_breed_sow ON breeding_records(individual_id);

CREATE TABLE IF NOT EXISTS farrowing_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    individual_id   TEXT NOT NULL REFERENCES sows(individual_id),
    parity          INTEGER NOT NULL,
    farrowing_date  TEXT,
    total_born      INTEGER,
    born_alive      INTEGER,
    stillborn       INTEGER,
    mummified       INTEGER,
    foster          INTEGER,
    weaning_date    TEXT,
    weaned          INTEGER,
    deaths          INTEGER,
    mortality_rate  REAL,
    nursing_days    INTEGER,
    farrowing_interval INTEGER,
    UNIQUE(individual_id, parity)
);
CREATE INDEX IF NOT EXISTS idx_farrow_sow ON farrowing_records(individual_id);

CREATE TABLE IF NOT EXISTS death_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    individual_id   TEXT NOT NULL REFERENCES sows(individual_id),
    event_date      TEXT,
    cause           TEXT,
    age_days        INTEGER,
    parity          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_death_sow ON death_records(individual_id);

CREATE TABLE IF NOT EXISTS cull_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    individual_id   TEXT NOT NULL REFERENCES sows(individual_id),
    event_date      TEXT,
    cause           TEXT,
    non_productive_days INTEGER,
    parity          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_cull_sow ON cull_records(individual_id);

CREATE TABLE IF NOT EXISTS parity_scores (
    individual_id   TEXT NOT NULL REFERENCES sows(individual_id),
    parity          INTEGER NOT NULL,
    own_weaned      REAL,
    own_rate        REAL,
    z_own_weaned    REAL,
    z_live_born     REAL,
    z_total_born    REAL,
    z_stillborn     REAL,
    z_own_rate      REAL,
    parity_score    REAL,
    rank_all        INTEGER,
    rank_active     INTEGER,
    PRIMARY KEY (individual_id, parity)
);

CREATE TABLE IF NOT EXISTS sow_scores (
    individual_id   TEXT PRIMARY KEY REFERENCES sows(individual_id),
    peak            REAL,
    stability       REAL,
    sustain         REAL,
    offspring_quality REAL,
    total_score     REAL,
    rank_all        INTEGER,
    rank_active     INTEGER
);

CREATE TABLE IF NOT EXISTS ml_predictions (
    individual_id   TEXT NOT NULL,
    parity          INTEGER NOT NULL,
    pred_excellent_prob REAL,
    shap_json       TEXT,
    model_version   TEXT,
    predicted_at    TEXT,
    PRIMARY KEY (individual_id, parity)
);
"""


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)


def reset_data_tables(conn: sqlite3.Connection) -> None:
    """Truncate all data tables for idempotent ETL."""
    tables = [
        "sow_scores", "parity_scores",
        "cull_records", "death_records",
        "farrowing_records", "breeding_records",
        "piglets", "sows",
    ]
    for t in tables:
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
