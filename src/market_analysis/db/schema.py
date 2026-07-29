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


_CREATE_TREND_SEGMENTATION_DAILY = """
CREATE TABLE IF NOT EXISTS trend_segmentation_daily (
    symbol                    TEXT NOT NULL,
    date                      DATE NOT NULL,
    lookback_bars             INT  NOT NULL,
    observation_count         INT  NOT NULL,
    segment_count             INT  NOT NULL,
    change_point_count        INT  NOT NULL,
    selected_rss              FLOAT,
    single_segment_rss        FLOAT,
    selected_bic              FLOAT,
    single_segment_bic        FLOAT,
    bic_improvement           FLOAT,
    min_segment_bars          INT  NOT NULL,
    max_segments              INT  NOT NULL,
    bic_penalty_multiplier    FLOAT NOT NULL DEFAULT 3.0,
    method                    TEXT NOT NULL,
    calculation_version       TEXT NOT NULL,
    PRIMARY KEY (symbol, date, lookback_bars)
);
"""

_ALTER_TREND_SEGMENTATION_DAILY = """
ALTER TABLE trend_segmentation_daily
    ADD COLUMN IF NOT EXISTS bic_penalty_multiplier FLOAT NOT NULL DEFAULT 3.0;
"""

_CREATE_TREND_SEGMENTATION_DAILY_IDX = """
CREATE INDEX IF NOT EXISTS trend_segmentation_daily_date_lookback_idx
    ON trend_segmentation_daily (date DESC, lookback_bars);
CREATE INDEX IF NOT EXISTS trend_segmentation_daily_symbol_date_idx
    ON trend_segmentation_daily (symbol, date DESC);
"""

_CREATE_TREND_SEGMENT_DAILY = """
CREATE TABLE IF NOT EXISTS trend_segment_daily (
    symbol                       TEXT NOT NULL,
    date                         DATE NOT NULL,
    lookback_bars                INT  NOT NULL,
    segment_index                INT  NOT NULL,
    start_date                   DATE NOT NULL,
    end_date                     DATE NOT NULL,
    start_bar_index              INT  NOT NULL,
    end_bar_index                INT  NOT NULL,
    observation_count            INT  NOT NULL,
    log_slope_per_bar            FLOAT,
    linearity_r2                 FLOAT,
    fitted_log_return            FLOAT,
    actual_log_return            FLOAT,
    realized_volatility_daily    FLOAT,
    vol_adjusted_trend           FLOAT,
    efficiency_ratio             FLOAT,
    largest_move_log_return      FLOAT,
    largest_move_date            DATE,
    largest_move_bar_index       INT,
    largest_move_path_share      FLOAT,
    method                       TEXT NOT NULL,
    calculation_version          TEXT NOT NULL,
    PRIMARY KEY (symbol, date, lookback_bars, segment_index)
);
"""

_ALTER_TREND_SEGMENT_DAILY = """
ALTER TABLE trend_segment_daily ADD COLUMN IF NOT EXISTS largest_move_log_return FLOAT;
ALTER TABLE trend_segment_daily ADD COLUMN IF NOT EXISTS largest_move_date DATE;
ALTER TABLE trend_segment_daily ADD COLUMN IF NOT EXISTS largest_move_bar_index INT;
ALTER TABLE trend_segment_daily ADD COLUMN IF NOT EXISTS largest_move_path_share FLOAT;
"""

_CREATE_TREND_SEGMENT_DAILY_IDX = """
CREATE INDEX IF NOT EXISTS trend_segment_daily_date_lookback_idx
    ON trend_segment_daily (date DESC, lookback_bars);
CREATE INDEX IF NOT EXISTS trend_segment_daily_symbol_date_idx
    ON trend_segment_daily (symbol, date DESC);
"""


_CREATE_TREND_PATTERN_DAILY = """
CREATE TABLE IF NOT EXISTS trend_pattern_daily (
    symbol                              TEXT NOT NULL,
    date                                DATE NOT NULL,
    lookback_bars                       INT  NOT NULL,
    regime                              TEXT NOT NULL,
    directional_bias                    TEXT NOT NULL,
    path_structure                      TEXT NOT NULL,
    terminal_state                      TEXT NOT NULL,
    pattern                             TEXT NOT NULL,
    pattern_confidence                  FLOAT NOT NULL,
    classification_reason               TEXT NOT NULL,
    direction_sequence                  TEXT NOT NULL,
    segment_count                       INT  NOT NULL,
    net_fitted_log_return               FLOAT,
    gross_fitted_log_return             FLOAT,
    net_to_gross_ratio                  FLOAT,
    latest_segment_direction            TEXT,
    latest_segment_log_slope            FLOAT,
    latest_segment_fitted_log_return     FLOAT,
    method                              TEXT NOT NULL,
    calculation_version                 TEXT NOT NULL,
    PRIMARY KEY (symbol, date, lookback_bars)
);
"""

_ALTER_TREND_PATTERN_DAILY = """
ALTER TABLE trend_pattern_daily
    ADD COLUMN IF NOT EXISTS regime TEXT NOT NULL DEFAULT 'irregular';
ALTER TABLE trend_pattern_daily
    ADD COLUMN IF NOT EXISTS directional_bias TEXT NOT NULL DEFAULT 'neutral';
ALTER TABLE trend_pattern_daily
    ADD COLUMN IF NOT EXISTS path_structure TEXT NOT NULL DEFAULT 'mixed';
ALTER TABLE trend_pattern_daily
    ADD COLUMN IF NOT EXISTS terminal_state TEXT NOT NULL DEFAULT 'flat';
"""

_CREATE_TREND_PATTERN_DAILY_IDX = """
CREATE INDEX IF NOT EXISTS trend_pattern_daily_date_lookback_pattern_idx
    ON trend_pattern_daily (date DESC, lookback_bars, pattern);
CREATE INDEX IF NOT EXISTS trend_pattern_daily_symbol_date_idx
    ON trend_pattern_daily (symbol, date DESC);
"""


_CREATE_TREND_PATTERN_V4_DAILY = """
CREATE TABLE IF NOT EXISTS trend_pattern_v4_daily (
    symbol                                  TEXT  NOT NULL,
    date                                    DATE  NOT NULL,
    lookback_bars                           INT   NOT NULL,
    observation_count                       INT   NOT NULL,
    source_segment_count                    INT   NOT NULL,
    effective_leg_count                     INT   NOT NULL,
    start_direction                         TEXT,
    structure_index                         INT,
    structure_code                          TEXT,
    net_log_return                          FLOAT,
    path_efficiency                         FLOAT,
    historical_volatility                   FLOAT,
    terminal_price_rank                     FLOAT,
    terminal_price_position                 FLOAT,
    terminal_leg_start_position             FLOAT,
    terminal_breakout_distance_vol          FLOAT,
    squared_movement_time_position          FLOAT,
    squared_movement_concentration          FLOAT,
    min_abs_fitted_log_return               FLOAT NOT NULL,
    min_linearity_r2                        FLOAT NOT NULL,
    min_abs_vol_adjusted_trend              FLOAT NOT NULL,
    pivot_retest_tolerance                  FLOAT NOT NULL,
    source_segmentation_method              TEXT  NOT NULL,
    source_segmentation_calculation_version TEXT  NOT NULL,
    method                                  TEXT  NOT NULL,
    calculation_version                     TEXT  NOT NULL,
    PRIMARY KEY (symbol, date, lookback_bars),
    CHECK (lookback_bars >= 3),
    CHECK (observation_count = lookback_bars),
    CHECK (effective_leg_count BETWEEN 0 AND 4),
    CHECK (start_direction IS NULL OR start_direction IN ('up', 'down')),
    CHECK (structure_index IS NULL OR structure_index BETWEEN 1 AND 40)
);
"""

_CREATE_TREND_PATTERN_V4_DAILY_IDX = """
CREATE INDEX IF NOT EXISTS trend_pattern_v4_daily_date_structure_idx
    ON trend_pattern_v4_daily (date DESC, lookback_bars, structure_code);
CREATE INDEX IF NOT EXISTS trend_pattern_v4_daily_symbol_date_idx
    ON trend_pattern_v4_daily (symbol, date DESC);
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
        conn.execute(_CREATE_TREND_SEGMENTATION_DAILY)
        _execute_statements(conn, _ALTER_TREND_SEGMENTATION_DAILY)
        _execute_statements(conn, _CREATE_TREND_SEGMENTATION_DAILY_IDX)
        conn.execute(_CREATE_TREND_SEGMENT_DAILY)
        _execute_statements(conn, _ALTER_TREND_SEGMENT_DAILY)
        _execute_statements(conn, _CREATE_TREND_SEGMENT_DAILY_IDX)
        conn.execute(_CREATE_TREND_PATTERN_DAILY)
        _execute_statements(conn, _ALTER_TREND_PATTERN_DAILY)
        _execute_statements(conn, _CREATE_TREND_PATTERN_DAILY_IDX)
        conn.execute(_CREATE_TREND_PATTERN_V4_DAILY)
        _execute_statements(conn, _CREATE_TREND_PATTERN_V4_DAILY_IDX)
        conn.execute(_CREATE_SECTOR_HEAT_DAILY)
        _execute_statements(conn, _CREATE_SECTOR_HEAT_DAILY_IDX)
        conn.commit()
    log.info("db.schema.initialized")
