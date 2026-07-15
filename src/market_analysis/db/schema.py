from __future__ import annotations

import structlog

from market_analysis.db import get_conn

log = structlog.get_logger(__name__)

_CREATE_SUPPORT_RESISTANCE_DAILY = """
CREATE TABLE IF NOT EXISTS support_resistance_daily (
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
    PRIMARY KEY (symbol, date)
);
"""

_CREATE_SUPPORT_RESISTANCE_DAILY_IDX = """
CREATE INDEX IF NOT EXISTS support_resistance_daily_date_idx
    ON support_resistance_daily (date DESC);
CREATE INDEX IF NOT EXISTS support_resistance_daily_symbol_idx
    ON support_resistance_daily (symbol);
CREATE INDEX IF NOT EXISTS support_resistance_daily_status_idx
    ON support_resistance_daily (sr_status);
"""


_CREATE_TREND_DAILY = """
CREATE TABLE IF NOT EXISTS trend_daily (
    symbol                       TEXT NOT NULL,
    date                         DATE NOT NULL,
    window_label                 TEXT NOT NULL,
    far_bars                     INT  NOT NULL,
    near_bars                    INT  NOT NULL DEFAULT 0,
    slope                        FLOAT,
    r2                           FLOAT,
    method                       TEXT NOT NULL DEFAULT 'linear_regression',
    observation_count            INT,
    log_slope_per_bar            FLOAT,
    linearity_r2                 FLOAT,
    fitted_log_return            FLOAT,
    actual_log_return            FLOAT,
    realized_volatility_daily    FLOAT,
    vol_adjusted_trend           FLOAT,
    efficiency_ratio             FLOAT,
    jackknife_slope_stability    FLOAT,
    adjacent_slope_stability     FLOAT,
    calculation_version          TEXT,
    PRIMARY KEY (symbol, date, window_label)
);
"""

_ALTER_TREND_DAILY = """
ALTER TABLE trend_daily ADD COLUMN IF NOT EXISTS observation_count INT;
ALTER TABLE trend_daily ADD COLUMN IF NOT EXISTS log_slope_per_bar FLOAT;
ALTER TABLE trend_daily ADD COLUMN IF NOT EXISTS linearity_r2 FLOAT;
ALTER TABLE trend_daily ADD COLUMN IF NOT EXISTS fitted_log_return FLOAT;
ALTER TABLE trend_daily ADD COLUMN IF NOT EXISTS actual_log_return FLOAT;
ALTER TABLE trend_daily ADD COLUMN IF NOT EXISTS realized_volatility_daily FLOAT;
ALTER TABLE trend_daily ADD COLUMN IF NOT EXISTS vol_adjusted_trend FLOAT;
ALTER TABLE trend_daily ADD COLUMN IF NOT EXISTS efficiency_ratio FLOAT;
ALTER TABLE trend_daily ADD COLUMN IF NOT EXISTS jackknife_slope_stability FLOAT;
ALTER TABLE trend_daily ADD COLUMN IF NOT EXISTS adjacent_slope_stability FLOAT;
ALTER TABLE trend_daily ADD COLUMN IF NOT EXISTS calculation_version TEXT;
"""

_CREATE_TREND_DAILY_IDX = """
CREATE INDEX IF NOT EXISTS trend_daily_date_idx   ON trend_daily (date DESC);
CREATE INDEX IF NOT EXISTS trend_daily_symbol_idx ON trend_daily (symbol);
CREATE INDEX IF NOT EXISTS trend_daily_date_window_idx
    ON trend_daily (date DESC, window_label);
CREATE INDEX IF NOT EXISTS trend_daily_symbol_date_idx
    ON trend_daily (symbol, date DESC);
"""


_CREATE_SECTOR_HEAT_DAILY = """
CREATE TABLE IF NOT EXISTS sector_heat_daily (
    universe_ticker   TEXT  NOT NULL,
    date              DATE  NOT NULL,
    sector_turnover   FLOAT,
    constituent_count INT,
    turnover_ma20     FLOAT,
    turnover_ratio    FLOAT,
    turnover_zscore   FLOAT,
    PRIMARY KEY (universe_ticker, date)
);
"""

_CREATE_SECTOR_HEAT_DAILY_IDX = """
CREATE INDEX IF NOT EXISTS sector_heat_daily_date_idx   ON sector_heat_daily (date DESC);
CREATE INDEX IF NOT EXISTS sector_heat_daily_ticker_idx ON sector_heat_daily (universe_ticker);
"""


def _execute_statements(conn, statements: str) -> None:
    for stmt in statements.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


def init_schema() -> None:
    with get_conn() as conn:
        conn.execute(_CREATE_SUPPORT_RESISTANCE_DAILY)
        _execute_statements(conn, _CREATE_SUPPORT_RESISTANCE_DAILY_IDX)
        conn.execute(_CREATE_TREND_DAILY)
        _execute_statements(conn, _ALTER_TREND_DAILY)
        _execute_statements(conn, _CREATE_TREND_DAILY_IDX)
        conn.execute(_CREATE_SECTOR_HEAT_DAILY)
        _execute_statements(conn, _CREATE_SECTOR_HEAT_DAILY_IDX)
        conn.commit()
    log.info("db.schema.initialized")
