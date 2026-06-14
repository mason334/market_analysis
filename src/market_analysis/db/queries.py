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
    "trend_slope_10d", "trend_r2_10d",
    "trend_slope_20d", "trend_r2_20d",
    "trend_slope_40d", "trend_r2_40d",
    "trend_slope_60d", "trend_r2_60d",
]

_FETCH_OHLCV = """
SELECT date, open, high, low, close, volume
FROM daily_bars_split_adjusted
WHERE symbol = %s
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
    trend_slope_5d, trend_r2_5d,
    trend_slope_10d, trend_r2_10d,
    trend_slope_20d, trend_r2_20d,
    trend_slope_40d, trend_r2_40d,
    trend_slope_60d, trend_r2_60d
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    trend_r2_5d         = EXCLUDED.trend_r2_5d,
    trend_slope_10d     = EXCLUDED.trend_slope_10d,
    trend_r2_10d        = EXCLUDED.trend_r2_10d,
    trend_slope_20d     = EXCLUDED.trend_slope_20d,
    trend_r2_20d        = EXCLUDED.trend_r2_20d,
    trend_slope_40d     = EXCLUDED.trend_slope_40d,
    trend_r2_40d        = EXCLUDED.trend_r2_40d,
    trend_slope_60d     = EXCLUDED.trend_slope_60d,
    trend_r2_60d        = EXCLUDED.trend_r2_60d
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
       trend_slope_5d, trend_r2_5d,
       trend_slope_10d, trend_r2_10d,
       trend_slope_20d, trend_r2_20d,
       trend_slope_40d, trend_r2_40d,
       trend_slope_60d, trend_r2_60d
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
       trend_slope_5d, trend_r2_5d,
       trend_slope_10d, trend_r2_10d,
       trend_slope_20d, trend_r2_20d,
       trend_slope_40d, trend_r2_40d,
       trend_slope_60d, trend_r2_60d
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
       trend_slope_5d, trend_r2_5d,
       trend_slope_10d, trend_r2_10d,
       trend_slope_20d, trend_r2_20d,
       trend_slope_40d, trend_r2_40d,
       trend_slope_60d, trend_r2_60d
FROM indicators_daily
WHERE symbol = %s
ORDER BY date DESC
LIMIT 1
"""


def fetch_ohlcv(symbol: str, source: str = "") -> pd.DataFrame:
    with get_source_conn() as conn:
        rows = conn.execute(_FETCH_OHLCV, (symbol,)).fetchall()
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
                row.get("trend_slope_10d"),
                row.get("trend_r2_10d"),
                row.get("trend_slope_20d"),
                row.get("trend_r2_20d"),
                row.get("trend_slope_40d"),
                row.get("trend_r2_40d"),
                row.get("trend_slope_60d"),
                row.get("trend_r2_60d"),
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
# Filtered query for indicators_daily (DB viewer)
# ---------------------------------------------------------------------------

_FETCH_INDICATORS_DAILY_SYMBOLS = """
SELECT DISTINCT symbol FROM indicators_daily ORDER BY symbol
"""


def fetch_indicators_daily_symbols() -> list[str]:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_INDICATORS_DAILY_SYMBOLS).fetchall()
    return [r[0] for r in rows]


def fetch_indicators_daily_filtered(
    symbols: list[str] | None,
    date_from: "date | None",
    date_to: "date | None",
    limit: int = 200,
    offset: int = 0,
) -> tuple[pd.DataFrame, int]:
    """Return (data_df, total_count) with optional symbol / date filters."""
    conditions: list[str] = []
    params: list[Any] = []

    if symbols:
        placeholders = ", ".join(["%s"] * len(symbols))
        conditions.append(f"symbol IN ({placeholders})")
        params.extend(symbols)
    if date_from:
        conditions.append("date >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("date <= %s")
        params.append(date_to)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    count_sql = f"SELECT COUNT(*) FROM indicators_daily {where}"
    data_sql = f"""
        SELECT {', '.join(_INDICATORS_DAILY_COLS)}
        FROM indicators_daily {where}
        ORDER BY date DESC, symbol
        LIMIT %s OFFSET %s
    """

    with get_conn() as conn:
        total = conn.execute(count_sql, params).fetchone()[0]
        rows = conn.execute(data_sql, params + [limit, offset]).fetchall()

    if not rows:
        return pd.DataFrame(columns=_INDICATORS_DAILY_COLS), total
    return pd.DataFrame(rows, columns=_INDICATORS_DAILY_COLS), total


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


# ---------------------------------------------------------------------------
# Universe constituents (from source / market_data DB)
# ---------------------------------------------------------------------------

_FETCH_ALL_UNIVERSE_CONSTITUENTS = """
SELECT universe_ticker, stock_ticker
FROM universe_constituents
ORDER BY universe_ticker, stock_ticker
"""

_FETCH_CONSTITUENTS_FOR_TICKER = """
SELECT stock_ticker
FROM universe_constituents
WHERE universe_ticker = %s
ORDER BY stock_ticker
"""

_FETCH_UNIVERSE_TICKER_LIST = """
SELECT ticker
FROM universe
WHERE LENGTH(ticker) <= 4
ORDER BY ticker
"""

_FETCH_UNIVERSE_SUBCATEGORY_MAP = """
SELECT ticker, sub_category
FROM universe
WHERE LENGTH(ticker) <= 4
ORDER BY ticker
"""


def fetch_universe_ticker_list() -> list[str]:
    """Returns ETF tickers from the universe table (ticker <= 4 chars).
    Labels longer than 4 chars are excluded automatically.
    """
    try:
        with get_source_conn() as conn:
            rows = conn.execute(_FETCH_UNIVERSE_TICKER_LIST).fetchall()
    except Exception:
        log.exception("db.fetch_universe_ticker_list.error")
        return []
    return [r[0] for r in rows]


def fetch_universe_subcategory_map() -> dict[str, str]:
    """Returns {ticker: sub_category} for all ETF tickers in universe table."""
    try:
        with get_source_conn() as conn:
            rows = conn.execute(_FETCH_UNIVERSE_SUBCATEGORY_MAP).fetchall()
    except Exception:
        log.exception("db.fetch_universe_subcategory_map.error")
        return {}
    return {r[0]: (r[1] or "") for r in rows}


def fetch_constituents_for_ticker(universe_ticker: str) -> list[str]:
    """Returns the list of stock_tickers for a single universe_ticker."""
    try:
        with get_source_conn() as conn:
            rows = conn.execute(_FETCH_CONSTITUENTS_FOR_TICKER, (universe_ticker,)).fetchall()
    except Exception:
        log.exception("db.fetch_constituents_for_ticker.error", ticker=universe_ticker)
        return []
    return [r[0] for r in rows]


def fetch_all_universe_constituents() -> dict[str, list[str]]:
    """Returns {universe_ticker: [stock_ticker, ...]} from source DB."""
    try:
        with get_source_conn() as conn:
            rows = conn.execute(_FETCH_ALL_UNIVERSE_CONSTITUENTS).fetchall()
    except Exception:
        log.exception("db.fetch_all_universe_constituents.error")
        return {}
    result: dict[str, list[str]] = {}
    for universe_ticker, stock_ticker in rows:
        result.setdefault(universe_ticker, []).append(stock_ticker)
    return result


def fetch_constituent_turnover_batch(
    stock_tickers: list[str],
    start_date: "date",
    source: str = "tiingo",
) -> pd.DataFrame:
    """
    Fetch close*volume for a list of stock_tickers from start_date onwards.
    Returns wide DataFrame: index=date (DatetimeIndex), columns=stock_ticker.
    """
    if not stock_tickers:
        return pd.DataFrame()
    placeholders = ", ".join(["%s"] * len(stock_tickers))
    query = f"""
        SELECT symbol, date, close * volume AS turnover
        FROM daily_bars_split_adjusted
        WHERE symbol IN ({placeholders}) AND source = %s AND date >= %s
        ORDER BY date, symbol
    """
    with get_source_conn() as conn:
        rows = conn.execute(query, (*stock_tickers, source, start_date)).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["symbol", "date", "turnover"])
    df["date"] = pd.to_datetime(df["date"])
    wide = df.pivot(index="date", columns="symbol", values="turnover")
    wide.columns.name = None
    return wide.sort_index()


# ---------------------------------------------------------------------------
# sector_heat_daily read / write
# ---------------------------------------------------------------------------

_SECTOR_HEAT_COLS = [
    "universe_ticker", "date", "sector_turnover", "constituent_count",
    "turnover_ma20", "turnover_ratio", "turnover_zscore",
]

_UPSERT_SECTOR_HEAT = """
INSERT INTO sector_heat_daily (
    universe_ticker, date, sector_turnover, constituent_count,
    turnover_ma20, turnover_ratio, turnover_zscore
)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (universe_ticker, date) DO UPDATE SET
    sector_turnover   = EXCLUDED.sector_turnover,
    constituent_count = EXCLUDED.constituent_count,
    turnover_ma20     = EXCLUDED.turnover_ma20,
    turnover_ratio    = EXCLUDED.turnover_ratio,
    turnover_zscore   = EXCLUDED.turnover_zscore
"""

_FETCH_LATEST_SECTOR_HEAT_DATE = """
SELECT MAX(date) FROM sector_heat_daily
"""

_FETCH_SECTOR_HEAT_SNAPSHOT = """
SELECT universe_ticker, date, sector_turnover, constituent_count,
       turnover_ma20, turnover_ratio, turnover_zscore
FROM sector_heat_daily
WHERE date = %s
ORDER BY turnover_ratio DESC NULLS LAST
"""

_FETCH_SECTOR_HEAT_HISTORY = """
SELECT universe_ticker, date, sector_turnover, constituent_count,
       turnover_ma20, turnover_ratio, turnover_zscore
FROM sector_heat_daily
WHERE universe_ticker = %s AND date BETWEEN %s AND %s
ORDER BY date
"""

_FETCH_LATEST_SECTOR_HEAT_FOR_TICKER = """
SELECT universe_ticker, date, sector_turnover, constituent_count,
       turnover_ma20, turnover_ratio, turnover_zscore
FROM sector_heat_daily
WHERE universe_ticker = %s
ORDER BY date DESC
LIMIT 1
"""


def upsert_sector_heat_daily(row: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            _UPSERT_SECTOR_HEAT,
            (
                row["universe_ticker"],
                row["date"],
                row.get("sector_turnover"),
                row.get("constituent_count"),
                row.get("turnover_ma20"),
                row.get("turnover_ratio"),
                row.get("turnover_zscore"),
            ),
        )
        conn.commit()


def fetch_latest_sector_heat_date() -> "date | None":
    with get_conn() as conn:
        row = conn.execute(_FETCH_LATEST_SECTOR_HEAT_DATE).fetchone()
    return row[0] if row and row[0] is not None else None


def fetch_sector_heat_snapshot(target_date: "date") -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_SECTOR_HEAT_SNAPSHOT, (target_date,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=_SECTOR_HEAT_COLS)
    return pd.DataFrame(rows, columns=_SECTOR_HEAT_COLS)


def fetch_sector_heat_history(
    universe_ticker: str, start: "date", end: "date"
) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_SECTOR_HEAT_HISTORY, (universe_ticker, start, end)).fetchall()
    if not rows:
        return pd.DataFrame(columns=_SECTOR_HEAT_COLS)
    df = pd.DataFrame(rows, columns=_SECTOR_HEAT_COLS)
    df["date"] = pd.to_datetime(df["date"])
    return df


def fetch_latest_sector_heat_for_ticker(universe_ticker: str) -> "dict[str, Any] | None":
    with get_conn() as conn:
        row = conn.execute(_FETCH_LATEST_SECTOR_HEAT_FOR_TICKER, (universe_ticker,)).fetchone()
    if not row:
        return None
    return dict(zip(_SECTOR_HEAT_COLS, row))


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
