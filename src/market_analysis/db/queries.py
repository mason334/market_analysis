from __future__ import annotations

import json
from datetime import date
from typing import Any

import pandas as pd
import structlog

from market_analysis.db import get_conn, get_source_conn

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
