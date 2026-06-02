from __future__ import annotations

import json
from datetime import date
from typing import Any

import pandas as pd
import structlog
from psycopg import sql

from market_analysis.db import get_conn, get_source_conn
from market_analysis.models import IndicatorSnapshot

log = structlog.get_logger(__name__)

_FETCH_OHLCV = """
SELECT date, open, high, low, close, volume
FROM daily_bars_split_adjusted
WHERE symbol = %s AND source = %s
ORDER BY date
"""

_UPSERT_SIGNAL = """
INSERT INTO signals (signal_id, symbol, date, strategy, signal_type, detail_json, created_at)
VALUES (%s, %s, %s, %s, %s, %s, now())
ON CONFLICT (symbol, date, strategy) DO UPDATE
    SET signal_type  = EXCLUDED.signal_type,
        detail_json  = EXCLUDED.detail_json,
        created_at   = now()
"""

_FETCH_SIGNALS = """
SELECT signal_id, symbol, date, strategy, signal_type, detail_json, created_at
FROM signals
WHERE date = %s
ORDER BY symbol, strategy
"""

_FETCH_SIGNALS_RANGE = """
SELECT signal_id, symbol, date, strategy, signal_type, detail_json, created_at
FROM signals
WHERE date BETWEEN %s AND %s
ORDER BY date DESC, symbol, strategy
"""

_FETCH_SIGNALS_SYMBOL_RANGE = """
SELECT signal_id, symbol, date, strategy, signal_type, detail_json, created_at
FROM signals
WHERE symbol = %s AND date BETWEEN %s AND %s
ORDER BY date DESC, strategy
"""

_UPSERT_SNAPSHOT = """
INSERT INTO indicator_snapshots (symbol, date, indicator, value)
VALUES (%s, %s, %s, %s)
ON CONFLICT (symbol, date, indicator) DO UPDATE
    SET value = EXCLUDED.value
"""

_FETCH_LATEST_SNAPSHOT_DATE = """
SELECT MAX(date) FROM indicator_snapshots
"""

_FETCH_SNAPSHOTS_DATE = """
SELECT symbol, indicator, value
FROM indicator_snapshots
WHERE date = %s
ORDER BY symbol, indicator
"""

_FETCH_SNAPSHOTS_SYMBOL_RANGE = """
SELECT date, indicator, value
FROM indicator_snapshots
WHERE symbol = %s AND date BETWEEN %s AND %s
ORDER BY date, indicator
"""


def fetch_ohlcv(symbol: str, source: str = "tiingo") -> pd.DataFrame:
    with get_source_conn() as conn:
        rows = conn.execute(_FETCH_OHLCV, (symbol, source)).fetchall()
    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    return df


def upsert_signals(records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    with get_conn() as conn:
        for r in records:
            conn.execute(
                _UPSERT_SIGNAL,
                (
                    r["signal_id"],
                    r["symbol"],
                    r["date"],
                    r["strategy"],
                    r["signal_type"],
                    json.dumps(r.get("detail_json")),
                ),
            )
        conn.commit()
    log.info("db.signals.upserted", count=len(records))
    return len(records)


def upsert_snapshots(records: list[IndicatorSnapshot]) -> int:
    if not records:
        return 0
    with get_conn() as conn:
        for r in records:
            conn.execute(_UPSERT_SNAPSHOT, (r.symbol, r.date, r.indicator, r.value))
        conn.commit()
    log.info("db.snapshots.upserted", count=len(records))
    return len(records)


def fetch_signals_by_date(target_date: date) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_SIGNALS, (target_date,)).fetchall()
    _COLS = ["signal_id", "symbol", "date", "strategy", "signal_type", "detail_json", "created_at"]
    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows, columns=_COLS)


def fetch_signals_range(start: date, end: date) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_SIGNALS_RANGE, (start, end)).fetchall()
    _COLS = ["signal_id", "symbol", "date", "strategy", "signal_type", "detail_json", "created_at"]
    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows, columns=_COLS)


def fetch_signals_for_symbol(symbol: str, start: date, end: date) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_SIGNALS_SYMBOL_RANGE, (symbol, start, end)).fetchall()
    _COLS = ["signal_id", "symbol", "date", "strategy", "signal_type", "detail_json", "created_at"]
    if not rows:
        return pd.DataFrame(columns=_COLS)
    return pd.DataFrame(rows, columns=_COLS)


def fetch_latest_snapshot_date() -> date | None:
    """Return the most recent date that has snapshot data, or None if table is empty."""
    with get_conn() as conn:
        row = conn.execute(_FETCH_LATEST_SNAPSHOT_DATE).fetchone()
    return row[0] if row and row[0] is not None else None


def fetch_snapshots_by_date(target_date: date) -> pd.DataFrame:
    """Return wide-format DataFrame: index=symbol, columns=indicator names."""
    with get_conn() as conn:
        rows = conn.execute(_FETCH_SNAPSHOTS_DATE, (target_date,)).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["symbol", "indicator", "value"])
    pivot = df.pivot(index="symbol", columns="indicator", values="value").reset_index()
    pivot.columns.name = None
    return pivot


def fetch_snapshots_for_symbol(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Return long-format DataFrame with columns [date, indicator, value] for one symbol."""
    with get_conn() as conn:
        rows = conn.execute(_FETCH_SNAPSHOTS_SYMBOL_RANGE, (symbol, start, end)).fetchall()
    if not rows:
        return pd.DataFrame(columns=["date", "indicator", "value"])
    df = pd.DataFrame(rows, columns=["date", "indicator", "value"])
    df["date"] = pd.to_datetime(df["date"])
    return df


# ---------------------------------------------------------------------------
# Generic DB viewer helpers — auto-discover tables, no hardcoding
# ---------------------------------------------------------------------------

_LIST_TABLES = """
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name
"""

_TABLE_COLUMNS = """
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = %s
ORDER BY ordinal_position
"""


def _ctx(use_source: bool):
    return get_source_conn() if use_source else get_conn()


def fetch_db_table_names(use_source: bool = False) -> list[str]:
    """Return all public BASE TABLE names from the selected database."""
    with _ctx(use_source) as conn:
        rows = conn.execute(_LIST_TABLES).fetchall()
    return [r[0] for r in rows]


def fetch_db_table_columns(table_name: str, use_source: bool = False) -> pd.DataFrame:
    """Return column names and data types for a table."""
    with _ctx(use_source) as conn:
        rows = conn.execute(_TABLE_COLUMNS, (table_name,)).fetchall()
    return pd.DataFrame(rows, columns=["列名", "类型"])


def fetch_db_table_row_count(table_name: str, use_source: bool = False) -> int:
    """Return approximate row count for a table (uses COUNT(*))."""
    q = sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name))
    with _ctx(use_source) as conn:
        row = conn.execute(q).fetchone()
    return int(row[0]) if row else 0


def fetch_db_table_data(
    table_name: str,
    limit: int = 100,
    offset: int = 0,
    use_source: bool = False,
) -> pd.DataFrame:
    """Return paginated rows from any table. Column names auto-detected."""
    col_df = fetch_db_table_columns(table_name, use_source=use_source)
    if col_df.empty:
        return pd.DataFrame()
    q = sql.SQL("SELECT * FROM {} LIMIT %s OFFSET %s").format(sql.Identifier(table_name))
    with _ctx(use_source) as conn:
        rows = conn.execute(q, (limit, offset)).fetchall()
    if not rows:
        return pd.DataFrame(columns=col_df["列名"].tolist())
    return pd.DataFrame(rows, columns=col_df["列名"].tolist())
