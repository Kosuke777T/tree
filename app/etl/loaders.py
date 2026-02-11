"""Excel file readers – handles sparse XLS layouts and XLSX with formulas."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import xlrd

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Excel epoch (1900-01-01, with the Lotus 123 leap year bug)
_EXCEL_EPOCH = datetime(1899, 12, 30)


def _xldate(value: float) -> str | None:
    """Convert Excel serial date float to ISO date string."""
    if not value or value < 1:
        return None
    try:
        dt = _EXCEL_EPOCH + timedelta(days=int(value))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        return None


def _safe_int(v) -> int | None:
    if v == "" or v is None:
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _safe_float(v) -> float | None:
    if v == "" or v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_date_str(v) -> str | None:
    """Convert various date types to ISO string, safely handling NaT/None."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return v.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def _safe_str(v) -> str | None:
    if v is None or v == "":
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    s = str(v).strip()
    return s if s and s.lower() != "nan" else None


# ── Column maps: (excel_col_index, db_column_name, converter) ──

_BREEDING_HEADER_ROW = 7
_BREEDING_COLS: list[tuple[int, str, callable]] = [
    (2,  "individual_id",    _safe_str),
    (9,  "parity",           _safe_int),
    (16, "breeding_date",    _xldate),
    (23, "breeding_type",    _safe_str),
    (25, "sire_first",       _safe_str),
    (40, "sire_second",      _safe_str),
    (50, "return_to_estrus", _safe_str),
    (54, "age_days",         _safe_int),
    (57, "status",           _safe_str),
]

_FARROWING_HEADER_ROW = 5
_FARROWING_COLS: list[tuple[int, str, callable]] = [
    (6,  "individual_id",       _safe_str),
    (17, "parity",              _safe_int),
    (28, "farrowing_date",      _xldate),
    (35, "total_born",          _safe_int),
    (39, "born_alive",          _safe_int),
    (41, "stillborn",           _safe_int),
    (44, "mummified",           _safe_int),
    (49, "foster",              _safe_int),
    (57, "weaning_date",        _xldate),
    (62, "weaned",              _safe_int),
    (70, "deaths",              _safe_int),
    (76, "mortality_rate",      _safe_float),
    (81, "nursing_days",        _safe_int),
    (84, "farrowing_interval",  _safe_int),
]

_DEATH_HEADER_ROW = 7
_DEATH_COLS: list[tuple[int, str, callable]] = [
    (3,  "individual_id", _safe_str),
    (8,  "event_date",    _xldate),
    (11, "cause",         _safe_str),
    (28, "age_days",      _safe_int),
    (30, "parity",        _safe_int),
]

_CULL_HEADER_ROW = 7
_CULL_COLS: list[tuple[int, str, callable]] = [
    (3,  "individual_id",       _safe_str),
    (14, "event_date",          _xldate),
    (20, "cause",               _safe_str),
    (59, "non_productive_days", _safe_int),
    (62, "parity",              _safe_int),
]


def _read_xls(path: Path, header_row: int,
              col_map: list[tuple[int, str, callable]]) -> list[dict]:
    """Read an XLS file with sparse column layout."""
    wb = xlrd.open_workbook(str(path), encoding_override="cp932")
    ws = wb.sheet_by_index(0)
    id_col = col_map[0][0]  # first column is always the ID
    rows: list[dict] = []
    for r in range(header_row + 1, ws.nrows):
        # Skip empty rows (check the ID column)
        id_val = ws.cell_value(r, id_col)
        if id_val == "" or id_val is None:
            continue
        record: dict = {}
        for ci, name, conv in col_map:
            if ci < ws.ncols:
                record[name] = conv(ws.cell_value(r, ci))
            else:
                record[name] = None
        if record.get("individual_id"):
            rows.append(record)
    return rows


def load_breeding() -> list[dict]:
    return _read_xls(DATA_DIR / "種付記録" / "report.XLS",
                     _BREEDING_HEADER_ROW, _BREEDING_COLS)


def load_farrowing() -> list[dict]:
    return _read_xls(DATA_DIR / "分娩記録" / "report.XLS",
                     _FARROWING_HEADER_ROW, _FARROWING_COLS)


def load_deaths() -> list[dict]:
    return _read_xls(DATA_DIR / "死亡記録" / "report.XLS",
                     _DEATH_HEADER_ROW, _DEATH_COLS)


def load_culls() -> list[dict]:
    return _read_xls(DATA_DIR / "廃豚記録" / "report.XLS",
                     _CULL_HEADER_ROW, _CULL_COLS)


def load_piglets() -> list[dict]:
    """Read the piglet XLSX (header row 0, standard layout)."""
    path = DATA_DIR / "子豚記録" / "PS.xlsx"
    df = pd.read_excel(path, engine="openpyxl", header=0)
    cols = list(df.columns)
    rows: list[dict] = []
    for _, row in df.iterrows():
        piglet_no = _safe_str(row.iloc[0])
        if not piglet_no:
            continue
        bd = row.iloc[1]
        birth_date = _to_date_str(bd)
        sd = row.iloc[13]
        shipment_date = _to_date_str(sd)
        # shipment_age: column 18 may contain formulas – compute from dates
        shipment_age = None
        if shipment_date and birth_date:
            try:
                d1 = datetime.strptime(birth_date, "%Y-%m-%d")
                d2 = datetime.strptime(shipment_date, "%Y-%m-%d")
                shipment_age = (d2 - d1).days
            except ValueError:
                pass
        rows.append({
            "piglet_no":     piglet_no,
            "birth_date":    birth_date,
            "rank":          _safe_str(row.iloc[3]),
            "teat_score":    _safe_int(row.iloc[4]),
            "remarks":       _safe_str(row.iloc[8]),
            "shipment_dest": _safe_str(row.iloc[9]),
            "ps_shipment":   _safe_str(row.iloc[12]),
            "shipment_date": shipment_date,
            "dam_id":        _safe_str(row.iloc[16]),
            "sire_id":       _safe_str(row.iloc[17]),
            "shipment_age":  shipment_age,
        })
    return rows
