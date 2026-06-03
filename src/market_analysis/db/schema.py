from __future__ import annotations

import structlog

from market_analysis.db import get_conn

log = structlog.get_logger(__name__)

# Primary daily analysis table (wide format, one row per symbol per day)
_CREATE_INDICATORS_DAILY = """
CREATE TABLE IF NOT EXISTS indicators_daily (
    symbol                TEXT    NOT NULL,
    date                  DATE    NOT NULL,
    nearest_support       FLOAT,
    nearest_resistance    FLOAT,
    dist_support_pct      FLOAT,
    dist_support_atr      FLOAT,
    dist_resistance_pct   FLOAT,
    dist_resistance_atr   FLOAT,
    atr_14                FLOAT,
    sr_status             TEXT,
    breakout_5d           TEXT,
    breakout_level        FLOAT,
    trend_slope_5d        FLOAT,
    trend_r2_5d           FLOAT,
    PRIMARY KEY (symbol, date)
);
"""

_CREATE_INDICATORS_DAILY_IDX = """
CREATE INDEX IF NOT EXISTS indicators_daily_date_idx   ON indicators_daily (date DESC);
CREATE INDEX IF NOT EXISTS indicators_daily_symbol_idx ON indicators_daily (symbol);
CREATE INDEX IF NOT EXISTS indicators_daily_status_idx ON indicators_daily (sr_status);
"""


def init_schema() -> None:
    with get_conn() as conn:
        conn.execute(_CREATE_INDICATORS_DAILY)
        for stmt in _CREATE_INDICATORS_DAILY_IDX.strip().splitlines():
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
    log.info("db.schema.initialized")
