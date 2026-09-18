from __future__ import annotations

import structlog

from market_analysis.db import get_conn

log = structlog.get_logger(__name__)


_CREATE_TREND_SEGMENTATION_DAILY = """
CREATE TABLE IF NOT EXISTS trend_segmentation_daily (
    symbol                    TEXT NOT NULL,
    date                      DATE NOT NULL,
    requested_lookback_bars   INT  NOT NULL,
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
    search_mode               TEXT NOT NULL DEFAULT 'exact',
    is_global_optimum         BOOLEAN NOT NULL DEFAULT TRUE,
    candidates_evaluated      BIGINT NOT NULL DEFAULT 0,
    refinement_converged      BOOLEAN NOT NULL DEFAULT TRUE,
    search_config             JSONB NOT NULL DEFAULT '{}'::jsonb,
    search_diagnostics        JSONB NOT NULL DEFAULT '[]'::jsonb,
    method                    TEXT NOT NULL,
    calculation_version       TEXT NOT NULL,
    PRIMARY KEY (symbol, date, lookback_bars),
    CONSTRAINT trend_segmentation_requested_lookback_check
        CHECK (requested_lookback_bars >= lookback_bars)
);
"""

_ALTER_TREND_SEGMENTATION_DAILY = """
ALTER TABLE trend_segmentation_daily
    ADD COLUMN IF NOT EXISTS bic_penalty_multiplier FLOAT NOT NULL DEFAULT 3.0;
ALTER TABLE trend_segmentation_daily
    ADD COLUMN IF NOT EXISTS search_mode TEXT NOT NULL DEFAULT 'exact';
ALTER TABLE trend_segmentation_daily
    ADD COLUMN IF NOT EXISTS is_global_optimum BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE trend_segmentation_daily
    ADD COLUMN IF NOT EXISTS candidates_evaluated BIGINT NOT NULL DEFAULT 0;
ALTER TABLE trend_segmentation_daily
    ADD COLUMN IF NOT EXISTS refinement_converged BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE trend_segmentation_daily
    ADD COLUMN IF NOT EXISTS search_config JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE trend_segmentation_daily
    ADD COLUMN IF NOT EXISTS search_diagnostics JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE trend_segmentation_daily
    ADD COLUMN IF NOT EXISTS requested_lookback_bars INT;
UPDATE trend_segmentation_daily
    SET requested_lookback_bars = lookback_bars
    WHERE requested_lookback_bars IS NULL;
ALTER TABLE trend_segmentation_daily
    ALTER COLUMN requested_lookback_bars SET NOT NULL;
"""

_ENSURE_TREND_SEGMENTATION_REQUESTED_LOOKBACK_CHECK = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'trend_segmentation_requested_lookback_check'
          AND conrelid = 'trend_segmentation_daily'::regclass
    ) THEN
        ALTER TABLE trend_segmentation_daily
            ADD CONSTRAINT trend_segmentation_requested_lookback_check
            CHECK (requested_lookback_bars >= lookback_bars);
    END IF;
END
$$
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
    fitted_anchor_log_price      FLOAT,
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
ALTER TABLE trend_segment_daily ADD COLUMN IF NOT EXISTS fitted_anchor_log_price FLOAT;
"""

_CREATE_TREND_SEGMENT_DAILY_IDX = """
CREATE INDEX IF NOT EXISTS trend_segment_daily_date_lookback_idx
    ON trend_segment_daily (date DESC, lookback_bars);
CREATE INDEX IF NOT EXISTS trend_segment_daily_symbol_date_idx
    ON trend_segment_daily (symbol, date DESC);
"""


_CREATE_PIVOT_SEGMENTATION_DAILY = """
CREATE TABLE IF NOT EXISTS pivot_segmentation_daily (
    symbol                                  TEXT  NOT NULL,
    date                                    DATE  NOT NULL,
    requested_lookback_bars                 INT   NOT NULL,
    lookback_bars                           INT   NOT NULL,
    observation_count                       INT   NOT NULL,
    window_close_min                        FLOAT,
    window_close_max                        FLOAT,
    pivot_count                             INT   NOT NULL,
    segment_count                           INT   NOT NULL,
    fit_rss                                 FLOAT,
    search_radius_bars                      INT   NOT NULL,
    min_segment_bars                        INT   NOT NULL,
    classification_config                   JSONB NOT NULL DEFAULT '{}'::jsonb,
    resolution_diagnostics                  JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_segmentation_method              TEXT  NOT NULL,
    source_segmentation_calculation_version TEXT  NOT NULL,
    method                                  TEXT  NOT NULL,
    calculation_version                     TEXT  NOT NULL,
    PRIMARY KEY (symbol, date, lookback_bars),
    CONSTRAINT pivot_segmentation_requested_lookback_check
        CHECK (requested_lookback_bars >= lookback_bars),
    CHECK (lookback_bars >= 2),
    CHECK (observation_count = lookback_bars),
    CONSTRAINT pivot_segmentation_window_close_bounds_check CHECK (
        (window_close_min IS NULL AND window_close_max IS NULL)
        OR (
            window_close_min IS NOT NULL
            AND window_close_max IS NOT NULL
            AND window_close_min > 0
            AND window_close_max >= window_close_min
            AND window_close_min NOT IN (
                'NaN'::double precision,
                'Infinity'::double precision,
                '-Infinity'::double precision
            )
            AND window_close_max NOT IN (
                'NaN'::double precision,
                'Infinity'::double precision,
                '-Infinity'::double precision
            )
        )
    ),
    CHECK (pivot_count >= 0),
    CHECK (segment_count = pivot_count + 1),
    CHECK (search_radius_bars >= 0),
    CHECK (min_segment_bars >= 2)
);
"""

_ALTER_PIVOT_SEGMENTATION_DAILY = """
ALTER TABLE pivot_segmentation_daily
    ADD COLUMN IF NOT EXISTS requested_lookback_bars INT,
    ADD COLUMN IF NOT EXISTS window_close_min FLOAT,
    ADD COLUMN IF NOT EXISTS window_close_max FLOAT;
UPDATE pivot_segmentation_daily
    SET requested_lookback_bars = lookback_bars
    WHERE requested_lookback_bars IS NULL;
ALTER TABLE pivot_segmentation_daily
    ALTER COLUMN requested_lookback_bars SET NOT NULL;
"""

_ENSURE_PIVOT_SEGMENTATION_WINDOW_CLOSE_BOUNDS_CHECK = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'pivot_segmentation_window_close_bounds_check'
          AND conrelid = 'pivot_segmentation_daily'::regclass
    ) THEN
        ALTER TABLE pivot_segmentation_daily
            ADD CONSTRAINT pivot_segmentation_window_close_bounds_check CHECK (
                (window_close_min IS NULL AND window_close_max IS NULL)
                OR (
                    window_close_min IS NOT NULL
                    AND window_close_max IS NOT NULL
                    AND window_close_min > 0
                    AND window_close_max >= window_close_min
                    AND window_close_min NOT IN (
                        'NaN'::double precision,
                        'Infinity'::double precision,
                        '-Infinity'::double precision
                    )
                    AND window_close_max NOT IN (
                        'NaN'::double precision,
                        'Infinity'::double precision,
                        '-Infinity'::double precision
                    )
                )
            );
    END IF;
END
$$
"""

_ENSURE_PIVOT_SEGMENTATION_REQUESTED_LOOKBACK_CHECK = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'pivot_segmentation_requested_lookback_check'
          AND conrelid = 'pivot_segmentation_daily'::regclass
    ) THEN
        ALTER TABLE pivot_segmentation_daily
            ADD CONSTRAINT pivot_segmentation_requested_lookback_check
            CHECK (requested_lookback_bars >= lookback_bars);
    END IF;
END
$$
"""

_CREATE_PIVOT_SEGMENTATION_DAILY_IDX = """
CREATE INDEX IF NOT EXISTS pivot_segmentation_daily_date_lookback_idx
    ON pivot_segmentation_daily (date DESC, lookback_bars);
CREATE INDEX IF NOT EXISTS pivot_segmentation_daily_symbol_date_idx
    ON pivot_segmentation_daily (symbol, date DESC);
"""


_CREATE_PIVOT_SEGMENT_DAILY = """
CREATE TABLE IF NOT EXISTS pivot_segment_daily (
    symbol                           TEXT  NOT NULL,
    date                             DATE  NOT NULL,
    lookback_bars                    INT   NOT NULL,
    segment_index                    INT   NOT NULL,
    start_boundary_index             INT   NOT NULL,
    end_boundary_index_exclusive     INT   NOT NULL,
    start_endpoint_bar_index         INT   NOT NULL,
    end_endpoint_bar_index           INT   NOT NULL,
    start_endpoint_date              DATE  NOT NULL,
    end_endpoint_date                DATE  NOT NULL,
    observation_count                INT   NOT NULL,
    return_interval_count            INT   NOT NULL,
    segment_type                     TEXT  NOT NULL,
    log_slope_per_bar                FLOAT,
    linearity_r2                     FLOAT,
    fitted_start_log_price           FLOAT,
    fitted_end_log_price             FLOAT,
    fitted_log_return                FLOAT,
    actual_start_log_price           FLOAT,
    actual_end_log_price             FLOAT,
    actual_start_close               FLOAT,
    actual_end_close                 FLOAT,
    actual_log_return                FLOAT,
    realized_volatility_daily        FLOAT,
    vol_adjusted_trend               FLOAT,
    efficiency_ratio                 FLOAT,
    end_point_type                   TEXT  NOT NULL,
    pivot_seed_bar_index             INT,
    pivot_search_start_bar_index     INT,
    pivot_search_end_bar_index       INT,
    pivot_displacement_bars          INT,
    pivot_source_left_type           TEXT,
    pivot_source_right_type          TEXT,
    pivot_source_segment_indices     JSONB NOT NULL DEFAULT '[]'::jsonb,
    pivot_resolution_status          TEXT,
    method                           TEXT  NOT NULL,
    calculation_version              TEXT  NOT NULL,
    PRIMARY KEY (symbol, date, lookback_bars, segment_index),
    CHECK (segment_index >= 0),
    CHECK (start_boundary_index >= 0),
    CHECK (end_boundary_index_exclusive > start_boundary_index),
    CHECK (end_endpoint_bar_index >= start_endpoint_bar_index),
    CHECK (observation_count = end_boundary_index_exclusive - start_boundary_index),
    CHECK (return_interval_count = end_endpoint_bar_index - start_endpoint_bar_index),
    CHECK (segment_type IN ('up', 'down', 'flat')),
    CHECK (end_point_type IN ('high', 'low', 'window_end')),
    CHECK (pivot_source_left_type IS NULL OR pivot_source_left_type IN ('up', 'down', 'flat')),
    CHECK (pivot_source_right_type IS NULL OR pivot_source_right_type IN ('up', 'down', 'flat'))
);
"""

_CREATE_PIVOT_SEGMENT_DAILY_IDX = """
CREATE INDEX IF NOT EXISTS pivot_segment_daily_date_lookback_idx
    ON pivot_segment_daily (date DESC, lookback_bars);
CREATE INDEX IF NOT EXISTS pivot_segment_daily_symbol_date_idx
    ON pivot_segment_daily (symbol, date DESC);
CREATE INDEX IF NOT EXISTS pivot_segment_daily_date_endpoint_type_idx
    ON pivot_segment_daily (date DESC, lookback_bars, end_point_type);
"""


def _execute_statements(conn, statements: str) -> None:
    for stmt in statements.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)



def init_schema() -> None:
    """Create or update only the adaptive and pivot segmentation tables."""
    with get_conn() as conn:
        conn.execute(_CREATE_TREND_SEGMENTATION_DAILY)
        _execute_statements(conn, _ALTER_TREND_SEGMENTATION_DAILY)
        conn.execute(_ENSURE_TREND_SEGMENTATION_REQUESTED_LOOKBACK_CHECK)
        _execute_statements(conn, _CREATE_TREND_SEGMENTATION_DAILY_IDX)
        conn.execute(_CREATE_TREND_SEGMENT_DAILY)
        _execute_statements(conn, _ALTER_TREND_SEGMENT_DAILY)
        _execute_statements(conn, _CREATE_TREND_SEGMENT_DAILY_IDX)
        conn.execute(_CREATE_PIVOT_SEGMENTATION_DAILY)
        _execute_statements(conn, _ALTER_PIVOT_SEGMENTATION_DAILY)
        conn.execute(_ENSURE_PIVOT_SEGMENTATION_REQUESTED_LOOKBACK_CHECK)
        conn.execute(_ENSURE_PIVOT_SEGMENTATION_WINDOW_CLOSE_BOUNDS_CHECK)
        _execute_statements(conn, _CREATE_PIVOT_SEGMENTATION_DAILY_IDX)
        conn.execute(_CREATE_PIVOT_SEGMENT_DAILY)
        _execute_statements(conn, _CREATE_PIVOT_SEGMENT_DAILY_IDX)
        conn.commit()
    log.info("db.schema.initialized")
