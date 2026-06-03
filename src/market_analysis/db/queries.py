from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import structlog
from psycopg import sql

from market_analysis.db import get_conn, get_source_conn

log = structlog.get_logger(__name__)

_INDICATORS_DAILY_COLS = [
    "symbol", "date",
    "nearest_support", "nearest_resistance",
    "dist_support_pct", "dist_support_atr",
    "dist_resistance_pct", "dist_resistance_atr",
    "atr_14", "sr_status",
    "breakout_5d", "breakout_level",
    "trend_slope_5d", "trend_r2_5d",
]

_FETCH_OHLCV = """
SELECT date, open, high, low, close, volume
FROM daily_bars_split_adjusted
WHERE symbol = %s AND source = %s
ORDER BY date
"""

_UPSERT_SR_DAILY = """
INSERT INTO indicators_daily (
    symbol, date,
    nearest_support, nearest_resistance,
    dist_support_pct, dist_support_atr,
    dist_resistance_pct, dist_resistance_atr,
    atr_14, sr_status,
    breakout_5d, breakout_level,
    trend_slope_5d, trend_r2_5d
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (symbol, date) DO UPDATE SET
    nearest_support     = EXCLUDED.nearest_support,
    nearest_resistance  = EXCLUDED.nearest_resistance,
    dist_support_pct    = EXCLUDED.dist_support_pct,
    dist_support_atr    = EXCLUDED.dist_support_atr,
    dist_resistance_pct = EXCLUDED.dist_resistance_pct,
    dist_resistance_atr = EXCLUDED.dist_resistance_atr,
    atr_14              = EXCLUDED.atr_14,
    sr_status           = EXCLUDED.sr_status,
    breakout_5d         = EXCLUDED.breakout_5d,
    breakout_level      = EXCLUDED.breakout_level,
    trend_slope_5d      = EXCLUDED.trend_slope_5d,
    trend_r2_5d         = EXCLUDED.trend_r2_5d
"""

_FETCH_LATEST_SR_DATE = """
SELECT MAX(date) FROM indicators_daily
"""

_FETCH_SR_DAILY_BY_DATE = """
SELECT symbol, date,
       nearest_support, nearest_resistance,
       dist_support_pct, dist_support_atr,
       dist_resistance_pct, dist_resistance_atr,
       atr_14, sr_status,
       breakout_5d, breakout_level,
       trend_slope_5d, trend_r2_5d
FROM indicators_daily
WHERE date = %s
ORDER BY symbol
"""

_FETCH_SR_DAILY_FOR_SYMBOL = """
SELECT symbol, date,
       nearest_support, nearest_resistance,
       dist_support_pct, dist_support_atr,
       dist_resistance_pct, dist_resistance_atr,
       atr_14, sr_status,
       breakout_5d, breakout_level,
       trend_slope_5d, trend_r2_5d
FROM indicators_daily
WHERE symbol = %s AND date BETWEEN %s AND %s
ORDER BY date DESC
"""

_FETCH_SR_DAILY_LATEST_SYMBOL = """
SELECT symbol, date,
       nearest_support, nearest_resistance,
       dist_support_pct, dist_support_atr,
       dist_resistance_pct, dist_resistance_atr,
       atr_14, sr_status,
       breakout_5d, breakout_level,
       trend_slope_5d, trend_r2_5d
FROM indicators_daily
WHERE symbol = %s
ORDER BY date DESC
LIMIT 1
"""


def fetch_ohlcv(symbol: str, source: str = "tiingo") -> pd.DataFrame:
    with get_source_conn() as conn:
        rows = conn.execute(_FETCH_OHLCV, (symbol, source)).fetchall()
    if not rows:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def upsert_indicators_daily(row: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            _UPSERT_SR_DAILY,
            (
                row["symbol"],
                row["date"],
                row.get("nearest_support"),
                row.get("nearest_resistance"),
                row.get("dist_support_pct"),
                row.get("dist_support_atr"),
                row.get("dist_resistance_pct"),
                row.get("dist_resistance_atr"),
                row.get("atr_14"),
                row.get("sr_status"),
                row.get("breakout_5d"),
                row.get("breakout_level"),
                row.get("trend_slope_5d"),
                row.get("trend_r2_5d"),
            ),
        )
        conn.commit()


def fetch_latest_indicators_daily_date() -> date | None:
    with get_conn() as conn:
        row = conn.execute(_FETCH_LATEST_SR_DATE).fetchone()
    return row[0] if row and row[0] is not None else None


def fetch_indicators_daily_by_date(target_date: date) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_SR_DAILY_BY_DATE, (target_date,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=_INDICATORS_DAILY_COLS)
    return pd.DataFrame(rows, columns=_INDICATORS_DAILY_COLS)


def fetch_indicators_daily_for_symbol(symbol: str, start: date, end: date) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_SR_DAILY_FOR_SYMBOL, (symbol, start, end)).fetchall()
    if not rows:
        return pd.DataFrame(columns=_INDICATORS_DAILY_COLS)
    df = pd.DataFrame(rows, columns=_INDICATORS_DAILY_COLS)
    df["date"] = pd.to_datetime(df["date"])
    return df


def fetch_latest_indicators_daily_for_symbol(symbol: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(_FETCH_SR_DAILY_LATEST_SYMBOL, (symbol,)).fetchone()
    if not row:
        return None
    return dict(zip(_INDICATORS_DAILY_COLS, row))


# ---------------------------------------------------------------------------
# Generic DB viewer helpers
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
    with _ctx(use_source) as conn:
        rows = conn.execute(_LIST_TABLES).fetchall()
    return [r[0] for r in rows]


def fetch_db_table_columns(table_name: str, use_source: bool = False) -> pd.DataFrame:
    with _ctx(use_source) as conn:
        rows = conn.execute(_TABLE_COLUMNS, (table_name,)).fetchall()
    return pd.DataFrame(rows, columns=["列名", "类型"])


def fetch_db_table_row_count(table_name: str, use_source: bool = False) -> int:
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
    col_df = fetch_db_table_columns(table_name, use_source=use_source)
    if col_df.empty:
        return pd.DataFrame()
    q = sql.SQL("SELECT * FROM {} LIMIT %s OFFSET %s").format(sql.Identifier(table_name))
    with _ctx(use_source) as conn:
        rows = conn.execute(q, (limit, offset)).fetchall()
    if not rows:
        return pd.DataFrame(columns=col_df["列名"].tolist())
    return pd.DataFrame(rows, columns=col_df["列名"].tolist())
