from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import structlog
from psycopg import sql
from psycopg.types.json import Jsonb

from market_analysis.db import get_conn, get_source_conn

log = structlog.get_logger(__name__)

_INDICATOR_SNAPSHOT_COLS = [
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

_TREND_PIVOT_COLS = """
       MAX(CASE WHEN window_label = '5d' THEN slope END) AS trend_slope_5d,
       MAX(CASE WHEN window_label = '5d' THEN r2 END) AS trend_r2_5d,
       MAX(CASE WHEN window_label = '10d' THEN slope END) AS trend_slope_10d,
       MAX(CASE WHEN window_label = '10d' THEN r2 END) AS trend_r2_10d,
       MAX(CASE WHEN window_label = '20d' THEN slope END) AS trend_slope_20d,
       MAX(CASE WHEN window_label = '20d' THEN r2 END) AS trend_r2_20d,
       MAX(CASE WHEN window_label = '40d' THEN slope END) AS trend_slope_40d,
       MAX(CASE WHEN window_label = '40d' THEN r2 END) AS trend_r2_40d,
       MAX(CASE WHEN window_label = '60d' THEN slope END) AS trend_slope_60d,
       MAX(CASE WHEN window_label = '60d' THEN r2 END) AS trend_r2_60d
"""

_FETCH_OHLCV = """
SELECT date, open, high, low, close, volume
FROM daily_bars_split_adjusted
WHERE symbol = %s
ORDER BY date
"""

_FETCH_ADAPTIVE_INPUT_METADATA = """
WITH requested(symbol) AS (
    SELECT UNNEST(%s::text[])
)
SELECT requested.symbol,
       MAX(recent.date) AS latest_date,
       COUNT(recent.date) AS available_bars
FROM requested
LEFT JOIN LATERAL (
    SELECT date
    FROM daily_bars_split_adjusted
    WHERE symbol = requested.symbol
      AND source = %s
      AND (%s::date IS NULL OR date <= %s)
    ORDER BY date DESC
    LIMIT %s
) AS recent ON TRUE
GROUP BY requested.symbol
ORDER BY requested.symbol
"""

_FETCH_ADAPTIVE_CLOSE_WINDOW = """
SELECT date, close
FROM (
    SELECT date, close
    FROM daily_bars_split_adjusted
    WHERE symbol = %s
      AND source = %s
      AND (%s::date IS NULL OR date <= %s)
    ORDER BY date DESC
    LIMIT %s
) AS recent
ORDER BY date
"""

_FETCH_SPLIT_ADJUSTED_CLOSE_WINDOW = """
SELECT date, close
FROM (
    SELECT date, close
    FROM daily_bars_split_adjusted
    WHERE symbol = %s AND date <= %s
    ORDER BY date DESC
    LIMIT %s
) AS latest_window
ORDER BY date
"""

_UPSERT_SUPPORT_RESISTANCE_DAILY = """
INSERT INTO support_resistance_daily (
    symbol, date,
    nearest_support, nearest_resistance,
    dist_support_pct, dist_support_atr,
    dist_resistance_pct, dist_resistance_atr,
    atr_14, sr_status,
    breakout_5d, breakout_level
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    breakout_level      = EXCLUDED.breakout_level
"""

_UPSERT_TREND_DAILY = """
INSERT INTO trend_daily (
    symbol, date, window_label, far_bars, near_bars, slope, r2, method,
    observation_count, log_slope_per_bar, linearity_r2,
    fitted_log_return, actual_log_return, realized_volatility_daily,
    vol_adjusted_trend, efficiency_ratio,
    jackknife_slope_stability, adjacent_slope_stability,
    calculation_version
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (symbol, date, window_label) DO UPDATE SET
    far_bars                    = EXCLUDED.far_bars,
    near_bars                   = EXCLUDED.near_bars,
    slope                       = EXCLUDED.slope,
    r2                          = EXCLUDED.r2,
    method                      = EXCLUDED.method,
    observation_count           = EXCLUDED.observation_count,
    log_slope_per_bar           = EXCLUDED.log_slope_per_bar,
    linearity_r2                = EXCLUDED.linearity_r2,
    fitted_log_return           = EXCLUDED.fitted_log_return,
    actual_log_return           = EXCLUDED.actual_log_return,
    realized_volatility_daily   = EXCLUDED.realized_volatility_daily,
    vol_adjusted_trend          = EXCLUDED.vol_adjusted_trend,
    efficiency_ratio            = EXCLUDED.efficiency_ratio,
    jackknife_slope_stability   = EXCLUDED.jackknife_slope_stability,
    adjacent_slope_stability    = EXCLUDED.adjacent_slope_stability,
    calculation_version         = EXCLUDED.calculation_version
"""

_DELETE_UNCONFIGURED_TREND_WINDOWS = """
DELETE FROM trend_daily
WHERE symbol = %s
  AND date = %s
  AND NOT (window_label = ANY(%s))
"""

_UPSERT_TREND_SEGMENTATION_DAILY = """
INSERT INTO trend_segmentation_daily (
    symbol, date, requested_lookback_bars, lookback_bars, observation_count,
    segment_count, change_point_count,
    selected_rss, single_segment_rss, selected_bic, single_segment_bic,
    bic_improvement, min_segment_bars, max_segments, bic_penalty_multiplier,
    search_mode, is_global_optimum, candidates_evaluated, refinement_converged,
    search_config, search_diagnostics,
    method, calculation_version
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (symbol, date, lookback_bars) DO UPDATE SET
    requested_lookback_bars = EXCLUDED.requested_lookback_bars,
    observation_count   = EXCLUDED.observation_count,
    segment_count       = EXCLUDED.segment_count,
    change_point_count  = EXCLUDED.change_point_count,
    selected_rss        = EXCLUDED.selected_rss,
    single_segment_rss = EXCLUDED.single_segment_rss,
    selected_bic        = EXCLUDED.selected_bic,
    single_segment_bic  = EXCLUDED.single_segment_bic,
    bic_improvement     = EXCLUDED.bic_improvement,
    min_segment_bars    = EXCLUDED.min_segment_bars,
    max_segments        = EXCLUDED.max_segments,
    bic_penalty_multiplier = EXCLUDED.bic_penalty_multiplier,
    search_mode         = EXCLUDED.search_mode,
    is_global_optimum   = EXCLUDED.is_global_optimum,
    candidates_evaluated = EXCLUDED.candidates_evaluated,
    refinement_converged = EXCLUDED.refinement_converged,
    search_config       = EXCLUDED.search_config,
    search_diagnostics  = EXCLUDED.search_diagnostics,
    method              = EXCLUDED.method,
    calculation_version = EXCLUDED.calculation_version
"""

_UPSERT_TREND_SEGMENT_DAILY = """
INSERT INTO trend_segment_daily (
    symbol, date, lookback_bars, segment_index,
    start_date, end_date, start_bar_index, end_bar_index, observation_count,
    log_slope_per_bar, linearity_r2, fitted_log_return, fitted_anchor_log_price,
    actual_log_return,
    realized_volatility_daily, vol_adjusted_trend, efficiency_ratio,
    largest_move_log_return, largest_move_date, largest_move_bar_index,
    largest_move_path_share,
    method, calculation_version
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (symbol, date, lookback_bars, segment_index) DO UPDATE SET
    start_date                = EXCLUDED.start_date,
    end_date                  = EXCLUDED.end_date,
    start_bar_index           = EXCLUDED.start_bar_index,
    end_bar_index             = EXCLUDED.end_bar_index,
    observation_count         = EXCLUDED.observation_count,
    log_slope_per_bar         = EXCLUDED.log_slope_per_bar,
    linearity_r2              = EXCLUDED.linearity_r2,
    fitted_log_return         = EXCLUDED.fitted_log_return,
    fitted_anchor_log_price   = EXCLUDED.fitted_anchor_log_price,
    actual_log_return         = EXCLUDED.actual_log_return,
    realized_volatility_daily = EXCLUDED.realized_volatility_daily,
    vol_adjusted_trend        = EXCLUDED.vol_adjusted_trend,
    efficiency_ratio          = EXCLUDED.efficiency_ratio,
    largest_move_log_return   = EXCLUDED.largest_move_log_return,
    largest_move_date         = EXCLUDED.largest_move_date,
    largest_move_bar_index    = EXCLUDED.largest_move_bar_index,
    largest_move_path_share   = EXCLUDED.largest_move_path_share,
    method                    = EXCLUDED.method,
    calculation_version       = EXCLUDED.calculation_version
"""

_DELETE_TREND_SEGMENTS_FOR_LOOKBACK = """
DELETE FROM trend_segment_daily
WHERE symbol = %s AND date = %s AND lookback_bars = %s
"""

_DELETE_UNCONFIGURED_SEGMENT_LOOKBACKS = """
DELETE FROM trend_segment_daily
WHERE symbol = %s AND date = %s AND NOT (lookback_bars = ANY(%s))
"""

_DELETE_UNCONFIGURED_SEGMENTATION_LOOKBACKS = """
DELETE FROM trend_segmentation_daily
WHERE symbol = %s AND date = %s AND NOT (lookback_bars = ANY(%s))
"""

_TREND_SEGMENTATION_COLS = [
    "symbol", "date", "requested_lookback_bars", "lookback_bars",
    "observation_count",
    "segment_count", "change_point_count", "selected_rss", "single_segment_rss",
    "selected_bic", "single_segment_bic", "bic_improvement", "min_segment_bars",
    "max_segments", "bic_penalty_multiplier", "search_mode", "is_global_optimum",
    "candidates_evaluated", "refinement_converged", "search_config",
    "search_diagnostics", "method", "calculation_version",
]

_TREND_SEGMENT_COLS = [
    "symbol", "date", "lookback_bars", "segment_index", "start_date", "end_date",
    "start_bar_index", "end_bar_index", "observation_count", "log_slope_per_bar",
    "linearity_r2", "fitted_log_return", "fitted_anchor_log_price", "actual_log_return",
    "realized_volatility_daily", "vol_adjusted_trend", "efficiency_ratio",
    "largest_move_log_return", "largest_move_date", "largest_move_bar_index",
    "largest_move_path_share", "method", "calculation_version",
]

_FETCH_LATEST_TREND_SEGMENTATION_DATE = """
SELECT MAX(date) FROM trend_segmentation_daily
"""

_FETCH_LATEST_ADAPTIVE_SEGMENTATION_DATE = """
SELECT MAX(date) FROM trend_segmentation_daily
WHERE calculation_version = %s
"""

_FETCH_TREND_SEGMENTATION_SNAPSHOT = """
SELECT symbol, date, requested_lookback_bars, lookback_bars, observation_count,
       segment_count, change_point_count, selected_rss, single_segment_rss,
       selected_bic, single_segment_bic, bic_improvement, min_segment_bars,
       max_segments, bic_penalty_multiplier,
       search_mode, is_global_optimum, candidates_evaluated, refinement_converged,
       search_config, search_diagnostics, method, calculation_version
FROM trend_segmentation_daily
WHERE date = %s
ORDER BY symbol, lookback_bars
"""

_FETCH_TREND_SEGMENT_SNAPSHOT = """
SELECT symbol, date, lookback_bars, segment_index, start_date, end_date,
       start_bar_index, end_bar_index, observation_count, log_slope_per_bar,
       linearity_r2, fitted_log_return, fitted_anchor_log_price, actual_log_return,
       realized_volatility_daily, vol_adjusted_trend, efficiency_ratio,
       largest_move_log_return, largest_move_date, largest_move_bar_index,
       largest_move_path_share,
       method, calculation_version
FROM trend_segment_daily
WHERE date = %s
ORDER BY symbol, lookback_bars, segment_index
"""

_FETCH_ADAPTIVE_RESUME_CANDIDATES = """
WITH latest AS (
    SELECT DISTINCT ON (summary.symbol, summary.requested_lookback_bars)
           summary.symbol, summary.date, summary.requested_lookback_bars,
           summary.lookback_bars, summary.observation_count,
           summary.segment_count, summary.min_segment_bars,
           summary.max_segments, summary.bic_penalty_multiplier,
           summary.search_config, summary.method, summary.calculation_version
    FROM trend_segmentation_daily AS summary
    WHERE summary.symbol = ANY(%s)
      AND summary.requested_lookback_bars = ANY(%s)
      AND summary.calculation_version = %s
      AND (%s::date IS NULL OR summary.date <= %s)
    ORDER BY summary.symbol, summary.requested_lookback_bars,
             summary.date DESC, summary.lookback_bars DESC
)
SELECT latest.symbol, latest.date, latest.requested_lookback_bars,
       latest.lookback_bars, latest.observation_count,
       latest.segment_count, latest.min_segment_bars,
       latest.max_segments, latest.bic_penalty_multiplier,
       latest.search_config, latest.method, latest.calculation_version,
       COUNT(segment.segment_index) AS persisted_segment_count,
       MIN(segment.segment_index) AS min_segment_index,
       MAX(segment.segment_index) AS max_segment_index,
       BOOL_AND(
           segment.method = latest.method
           AND segment.calculation_version = latest.calculation_version
       ) AS segment_identity_matches
FROM latest
LEFT JOIN trend_segment_daily AS segment
  ON segment.symbol = latest.symbol
 AND segment.date = latest.date
 AND segment.lookback_bars = latest.lookback_bars
GROUP BY latest.symbol, latest.date, latest.requested_lookback_bars,
         latest.lookback_bars, latest.observation_count,
         latest.segment_count, latest.min_segment_bars,
         latest.max_segments, latest.bic_penalty_multiplier,
         latest.search_config, latest.method, latest.calculation_version
ORDER BY latest.symbol, latest.requested_lookback_bars
"""

_ADAPTIVE_RESUME_CANDIDATE_COLS = [
    "symbol",
    "date",
    "requested_lookback_bars",
    "lookback_bars",
    "observation_count",
    "segment_count",
    "min_segment_bars",
    "max_segments",
    "bic_penalty_multiplier",
    "search_config",
    "method",
    "calculation_version",
    "persisted_segment_count",
    "min_segment_index",
    "max_segment_index",
    "segment_identity_matches",
]

_UPSERT_PIVOT_SEGMENTATION_DAILY = """
INSERT INTO pivot_segmentation_daily (
    symbol, date, requested_lookback_bars, lookback_bars, observation_count,
    pivot_count, segment_count, fit_rss,
    search_radius_bars, min_segment_bars,
    classification_config, resolution_diagnostics,
    source_segmentation_method, source_segmentation_calculation_version,
    method, calculation_version
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (symbol, date, lookback_bars) DO UPDATE SET
    requested_lookback_bars                 = EXCLUDED.requested_lookback_bars,
    observation_count                       = EXCLUDED.observation_count,
    pivot_count                             = EXCLUDED.pivot_count,
    segment_count                           = EXCLUDED.segment_count,
    fit_rss                                 = EXCLUDED.fit_rss,
    search_radius_bars                      = EXCLUDED.search_radius_bars,
    min_segment_bars                        = EXCLUDED.min_segment_bars,
    classification_config                   = EXCLUDED.classification_config,
    resolution_diagnostics                  = EXCLUDED.resolution_diagnostics,
    source_segmentation_method              = EXCLUDED.source_segmentation_method,
    source_segmentation_calculation_version = EXCLUDED.source_segmentation_calculation_version,
    method                                  = EXCLUDED.method,
    calculation_version                     = EXCLUDED.calculation_version
"""

_UPSERT_PIVOT_SEGMENT_DAILY = """
INSERT INTO pivot_segment_daily (
    symbol, date, lookback_bars, segment_index,
    start_boundary_index, end_boundary_index_exclusive,
    start_endpoint_bar_index, end_endpoint_bar_index,
    start_endpoint_date, end_endpoint_date,
    observation_count, return_interval_count, segment_type,
    log_slope_per_bar, linearity_r2,
    fitted_start_log_price, fitted_end_log_price, fitted_log_return,
    actual_start_log_price, actual_end_log_price,
    actual_start_close, actual_end_close, actual_log_return,
    realized_volatility_daily, vol_adjusted_trend, efficiency_ratio,
    end_point_type, pivot_seed_bar_index,
    pivot_search_start_bar_index, pivot_search_end_bar_index,
    pivot_displacement_bars, pivot_source_left_type, pivot_source_right_type,
    pivot_source_segment_indices, pivot_resolution_status,
    method, calculation_version
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (symbol, date, lookback_bars, segment_index) DO UPDATE SET
    start_boundary_index         = EXCLUDED.start_boundary_index,
    end_boundary_index_exclusive = EXCLUDED.end_boundary_index_exclusive,
    start_endpoint_bar_index     = EXCLUDED.start_endpoint_bar_index,
    end_endpoint_bar_index       = EXCLUDED.end_endpoint_bar_index,
    start_endpoint_date          = EXCLUDED.start_endpoint_date,
    end_endpoint_date            = EXCLUDED.end_endpoint_date,
    observation_count            = EXCLUDED.observation_count,
    return_interval_count        = EXCLUDED.return_interval_count,
    segment_type                 = EXCLUDED.segment_type,
    log_slope_per_bar            = EXCLUDED.log_slope_per_bar,
    linearity_r2                 = EXCLUDED.linearity_r2,
    fitted_start_log_price       = EXCLUDED.fitted_start_log_price,
    fitted_end_log_price         = EXCLUDED.fitted_end_log_price,
    fitted_log_return            = EXCLUDED.fitted_log_return,
    actual_start_log_price       = EXCLUDED.actual_start_log_price,
    actual_end_log_price         = EXCLUDED.actual_end_log_price,
    actual_start_close           = EXCLUDED.actual_start_close,
    actual_end_close             = EXCLUDED.actual_end_close,
    actual_log_return            = EXCLUDED.actual_log_return,
    realized_volatility_daily    = EXCLUDED.realized_volatility_daily,
    vol_adjusted_trend           = EXCLUDED.vol_adjusted_trend,
    efficiency_ratio             = EXCLUDED.efficiency_ratio,
    end_point_type               = EXCLUDED.end_point_type,
    pivot_seed_bar_index         = EXCLUDED.pivot_seed_bar_index,
    pivot_search_start_bar_index = EXCLUDED.pivot_search_start_bar_index,
    pivot_search_end_bar_index   = EXCLUDED.pivot_search_end_bar_index,
    pivot_displacement_bars      = EXCLUDED.pivot_displacement_bars,
    pivot_source_left_type       = EXCLUDED.pivot_source_left_type,
    pivot_source_right_type      = EXCLUDED.pivot_source_right_type,
    pivot_source_segment_indices = EXCLUDED.pivot_source_segment_indices,
    pivot_resolution_status      = EXCLUDED.pivot_resolution_status,
    method                       = EXCLUDED.method,
    calculation_version          = EXCLUDED.calculation_version
"""

_DELETE_PIVOT_SEGMENTS_FOR_LOOKBACK = """
DELETE FROM pivot_segment_daily
WHERE symbol = %s AND date = %s AND lookback_bars = %s
"""

_DELETE_UNCONFIGURED_PIVOT_SEGMENTS = """
DELETE FROM pivot_segment_daily
WHERE symbol = %s AND date = %s AND NOT (lookback_bars = ANY(%s))
"""

_DELETE_UNCONFIGURED_PIVOT_SUMMARIES = """
DELETE FROM pivot_segmentation_daily
WHERE symbol = %s AND date = %s AND NOT (lookback_bars = ANY(%s))
"""

_FETCH_LATEST_PIVOT_SEGMENTATION_DATE = """
SELECT MAX(date) FROM pivot_segmentation_daily
WHERE calculation_version = 'pivot_refined_segmentation_v2'
"""

_FETCH_PIVOT_SEGMENTATION_SNAPSHOT = """
SELECT symbol, date, requested_lookback_bars, lookback_bars, observation_count,
       pivot_count, segment_count, fit_rss,
       search_radius_bars, min_segment_bars,
       classification_config, resolution_diagnostics,
       source_segmentation_method, source_segmentation_calculation_version,
       method, calculation_version
FROM pivot_segmentation_daily
WHERE date = %s
ORDER BY symbol, lookback_bars
"""

_PIVOT_SEGMENTATION_COLS = [
    "symbol", "date", "requested_lookback_bars", "lookback_bars",
    "observation_count", "pivot_count",
    "segment_count", "fit_rss", "search_radius_bars", "min_segment_bars",
    "classification_config", "resolution_diagnostics", "source_segmentation_method",
    "source_segmentation_calculation_version", "method", "calculation_version",
]

_FETCH_PIVOT_SEGMENT_SNAPSHOT = """
SELECT symbol, date, lookback_bars, segment_index,
       start_boundary_index, end_boundary_index_exclusive,
       start_endpoint_bar_index, end_endpoint_bar_index,
       start_endpoint_date, end_endpoint_date,
       observation_count, return_interval_count, segment_type,
       log_slope_per_bar, linearity_r2,
       fitted_start_log_price, fitted_end_log_price, fitted_log_return,
       actual_start_log_price, actual_end_log_price,
       actual_start_close, actual_end_close, actual_log_return,
       realized_volatility_daily, vol_adjusted_trend, efficiency_ratio,
       end_point_type, pivot_seed_bar_index,
       pivot_search_start_bar_index, pivot_search_end_bar_index,
       pivot_displacement_bars, pivot_source_left_type, pivot_source_right_type,
       pivot_source_segment_indices, pivot_resolution_status,
       method, calculation_version
FROM pivot_segment_daily
WHERE date = %s
ORDER BY symbol, lookback_bars, segment_index
"""

_PIVOT_SEGMENT_COLS = [
    "symbol", "date", "lookback_bars", "segment_index", "start_boundary_index",
    "end_boundary_index_exclusive", "start_endpoint_bar_index",
    "end_endpoint_bar_index", "start_endpoint_date", "end_endpoint_date",
    "observation_count", "return_interval_count", "segment_type",
    "log_slope_per_bar", "linearity_r2", "fitted_start_log_price",
    "fitted_end_log_price", "fitted_log_return", "actual_start_log_price",
    "actual_end_log_price", "actual_start_close", "actual_end_close",
    "actual_log_return", "realized_volatility_daily", "vol_adjusted_trend",
    "efficiency_ratio", "end_point_type", "pivot_seed_bar_index",
    "pivot_search_start_bar_index", "pivot_search_end_bar_index",
    "pivot_displacement_bars", "pivot_source_left_type", "pivot_source_right_type",
    "pivot_source_segment_indices", "pivot_resolution_status", "method",
    "calculation_version",
]


_UPSERT_TREND_PATTERN_DAILY = """
INSERT INTO trend_pattern_daily (
    symbol, date, lookback_bars,
    regime, directional_bias, path_structure, terminal_state,
    pattern, pattern_confidence,
    classification_reason, direction_sequence, segment_count,
    net_fitted_log_return, gross_fitted_log_return, net_to_gross_ratio,
    latest_segment_direction, latest_segment_log_slope,
    latest_segment_fitted_log_return, method, calculation_version
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (symbol, date, lookback_bars) DO UPDATE SET
    regime                           = EXCLUDED.regime,
    directional_bias                = EXCLUDED.directional_bias,
    path_structure                  = EXCLUDED.path_structure,
    terminal_state                  = EXCLUDED.terminal_state,
    pattern                          = EXCLUDED.pattern,
    pattern_confidence               = EXCLUDED.pattern_confidence,
    classification_reason            = EXCLUDED.classification_reason,
    direction_sequence               = EXCLUDED.direction_sequence,
    segment_count                    = EXCLUDED.segment_count,
    net_fitted_log_return            = EXCLUDED.net_fitted_log_return,
    gross_fitted_log_return          = EXCLUDED.gross_fitted_log_return,
    net_to_gross_ratio               = EXCLUDED.net_to_gross_ratio,
    latest_segment_direction         = EXCLUDED.latest_segment_direction,
    latest_segment_log_slope         = EXCLUDED.latest_segment_log_slope,
    latest_segment_fitted_log_return = EXCLUDED.latest_segment_fitted_log_return,
    method                           = EXCLUDED.method,
    calculation_version              = EXCLUDED.calculation_version
"""

_DELETE_UNCONFIGURED_PATTERN_LOOKBACKS = """
DELETE FROM trend_pattern_daily
WHERE symbol = %s AND date = %s AND NOT (lookback_bars = ANY(%s))
"""

_UPSERT_TREND_PATTERN_V4_DAILY = """
INSERT INTO trend_pattern_v4_daily (
    symbol, date, lookback_bars, observation_count,
    source_segment_count, effective_leg_count,
    start_direction, structure_index, structure_code,
    net_log_return, path_efficiency, historical_volatility,
    terminal_price_rank, terminal_price_position,
    terminal_leg_start_position, terminal_breakout_distance_vol,
    squared_movement_time_position, squared_movement_concentration,
    min_abs_fitted_log_return, min_linearity_r2,
    min_abs_vol_adjusted_trend, pivot_retest_tolerance,
    source_segmentation_method, source_segmentation_calculation_version,
    method, calculation_version
)
VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (symbol, date, lookback_bars) DO UPDATE SET
    observation_count                       = EXCLUDED.observation_count,
    source_segment_count                    = EXCLUDED.source_segment_count,
    effective_leg_count                     = EXCLUDED.effective_leg_count,
    start_direction                         = EXCLUDED.start_direction,
    structure_index                         = EXCLUDED.structure_index,
    structure_code                          = EXCLUDED.structure_code,
    net_log_return                          = EXCLUDED.net_log_return,
    path_efficiency                         = EXCLUDED.path_efficiency,
    historical_volatility                   = EXCLUDED.historical_volatility,
    terminal_price_rank                     = EXCLUDED.terminal_price_rank,
    terminal_price_position                 = EXCLUDED.terminal_price_position,
    terminal_leg_start_position             = EXCLUDED.terminal_leg_start_position,
    terminal_breakout_distance_vol          = EXCLUDED.terminal_breakout_distance_vol,
    squared_movement_time_position          = EXCLUDED.squared_movement_time_position,
    squared_movement_concentration          = EXCLUDED.squared_movement_concentration,
    min_abs_fitted_log_return               = EXCLUDED.min_abs_fitted_log_return,
    min_linearity_r2                        = EXCLUDED.min_linearity_r2,
    min_abs_vol_adjusted_trend              = EXCLUDED.min_abs_vol_adjusted_trend,
    pivot_retest_tolerance                  = EXCLUDED.pivot_retest_tolerance,
    source_segmentation_method              = EXCLUDED.source_segmentation_method,
    source_segmentation_calculation_version =
        EXCLUDED.source_segmentation_calculation_version,
    method                                  = EXCLUDED.method,
    calculation_version                     = EXCLUDED.calculation_version
"""

_DELETE_UNCONFIGURED_PATTERN_V4_LOOKBACKS = """
DELETE FROM trend_pattern_v4_daily
WHERE symbol = %s AND date = %s AND NOT (lookback_bars = ANY(%s))
"""

_TREND_PATTERN_V4_COLS = [
    "symbol", "date", "lookback_bars", "observation_count",
    "source_segment_count", "effective_leg_count", "start_direction",
    "structure_index", "structure_code", "net_log_return", "path_efficiency",
    "historical_volatility", "terminal_price_rank", "terminal_price_position",
    "terminal_leg_start_position", "terminal_breakout_distance_vol",
    "squared_movement_time_position", "squared_movement_concentration",
    "min_abs_fitted_log_return", "min_linearity_r2",
    "min_abs_vol_adjusted_trend", "pivot_retest_tolerance",
    "source_segmentation_method", "source_segmentation_calculation_version",
    "method", "calculation_version",
]

_FETCH_TREND_PATTERN_V4_SNAPSHOT = f"""
SELECT {", ".join(_TREND_PATTERN_V4_COLS)}
FROM trend_pattern_v4_daily
WHERE date = %s
ORDER BY symbol, lookback_bars
"""

_FETCH_TREND_PATTERN_V4_FOR_SYMBOL = f"""
SELECT {", ".join(_TREND_PATTERN_V4_COLS)}
FROM trend_pattern_v4_daily
WHERE symbol = %s AND date BETWEEN %s AND %s
ORDER BY date DESC, lookback_bars
"""

_FETCH_LATEST_INDICATOR_DATE = """
SELECT MAX(date) FROM support_resistance_daily
"""

_FETCH_INDICATOR_SNAPSHOT_BY_DATE = f"""
WITH trend_pivot AS (
    SELECT symbol, date,
{_TREND_PIVOT_COLS}
    FROM trend_daily
    GROUP BY symbol, date
)
SELECT sr.symbol, sr.date,
       sr.nearest_support, sr.nearest_resistance,
       sr.dist_support_pct, sr.dist_support_atr,
       sr.dist_resistance_pct, sr.dist_resistance_atr,
       sr.atr_14, sr.sr_status,
       sr.breakout_5d, sr.breakout_level,
       tp.trend_slope_5d, tp.trend_r2_5d,
       tp.trend_slope_10d, tp.trend_r2_10d,
       tp.trend_slope_20d, tp.trend_r2_20d,
       tp.trend_slope_40d, tp.trend_r2_40d,
       tp.trend_slope_60d, tp.trend_r2_60d
FROM support_resistance_daily sr
LEFT JOIN trend_pivot tp
  ON sr.symbol = tp.symbol AND sr.date = tp.date
WHERE sr.date = %s
ORDER BY sr.symbol
"""

_FETCH_INDICATOR_SNAPSHOT_FOR_SYMBOL = f"""
WITH trend_pivot AS (
    SELECT symbol, date,
{_TREND_PIVOT_COLS}
    FROM trend_daily
    GROUP BY symbol, date
)
SELECT sr.symbol, sr.date,
       sr.nearest_support, sr.nearest_resistance,
       sr.dist_support_pct, sr.dist_support_atr,
       sr.dist_resistance_pct, sr.dist_resistance_atr,
       sr.atr_14, sr.sr_status,
       sr.breakout_5d, sr.breakout_level,
       tp.trend_slope_5d, tp.trend_r2_5d,
       tp.trend_slope_10d, tp.trend_r2_10d,
       tp.trend_slope_20d, tp.trend_r2_20d,
       tp.trend_slope_40d, tp.trend_r2_40d,
       tp.trend_slope_60d, tp.trend_r2_60d
FROM support_resistance_daily sr
LEFT JOIN trend_pivot tp
  ON sr.symbol = tp.symbol AND sr.date = tp.date
WHERE sr.symbol = %s AND sr.date BETWEEN %s AND %s
ORDER BY sr.date DESC
"""

_FETCH_INDICATOR_SNAPSHOT_LATEST_SYMBOL = f"""
WITH trend_pivot AS (
    SELECT symbol, date,
{_TREND_PIVOT_COLS}
    FROM trend_daily
    GROUP BY symbol, date
)
SELECT sr.symbol, sr.date,
       sr.nearest_support, sr.nearest_resistance,
       sr.dist_support_pct, sr.dist_support_atr,
       sr.dist_resistance_pct, sr.dist_resistance_atr,
       sr.atr_14, sr.sr_status,
       sr.breakout_5d, sr.breakout_level,
       tp.trend_slope_5d, tp.trend_r2_5d,
       tp.trend_slope_10d, tp.trend_r2_10d,
       tp.trend_slope_20d, tp.trend_r2_20d,
       tp.trend_slope_40d, tp.trend_r2_40d,
       tp.trend_slope_60d, tp.trend_r2_60d
FROM support_resistance_daily sr
LEFT JOIN trend_pivot tp
  ON sr.symbol = tp.symbol AND sr.date = tp.date
WHERE sr.symbol = %s
ORDER BY sr.date DESC
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


def fetch_adaptive_input_metadata(
    symbols: list[str],
    *,
    source: str,
    target_date: date | None,
    max_lookback_bars: int,
) -> dict[str, tuple[date | None, int]]:
    """Read only the latest date and capped available-bar count for each symbol."""
    if not symbols:
        return {}
    with get_source_conn() as conn:
        rows = conn.execute(
            _FETCH_ADAPTIVE_INPUT_METADATA,
            (symbols, source, target_date, target_date, max_lookback_bars),
        ).fetchall()
    return {
        str(symbol): (latest_date, int(available_bars))
        for symbol, latest_date, available_bars in rows
    }


def fetch_adaptive_close_window(
    symbol: str,
    *,
    source: str,
    target_date: date | None,
    observation_count: int,
) -> pd.DataFrame:
    """Read the exact capped close window required by adaptive segmentation."""
    if observation_count <= 0:
        return pd.DataFrame(columns=["close"])
    with get_source_conn() as conn:
        rows = conn.execute(
            _FETCH_ADAPTIVE_CLOSE_WINDOW,
            (symbol, source, target_date, target_date, observation_count),
        ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["close"])
    frame = pd.DataFrame(rows, columns=["date", "close"])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date").sort_index()


def fetch_split_adjusted_close_window(
    symbol: str,
    target_date: date,
    observation_count: int,
) -> pd.DataFrame:
    """Read one exact historical close window from the market_data database."""
    count = int(observation_count)
    if count <= 0:
        raise ValueError("observation_count must be positive.")
    with get_source_conn() as conn:
        rows = conn.execute(
            _FETCH_SPLIT_ADJUSTED_CLOSE_WINDOW,
            (symbol, target_date, count),
        ).fetchall()
    if not rows:
        return pd.DataFrame(columns=["close"], index=pd.DatetimeIndex([], name="date"))
    frame = pd.DataFrame(rows, columns=["date", "close"])
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.set_index("date").sort_index()


def upsert_support_resistance_daily(row: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            _UPSERT_SUPPORT_RESISTANCE_DAILY,
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
            ),
        )
        conn.commit()


def upsert_trend_daily(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with get_conn() as conn:
        windows_by_snapshot: dict[tuple[str, date], list[str]] = {}
        for row in rows:
            key = (str(row["symbol"]), row["date"])
            windows_by_snapshot.setdefault(key, []).append(str(row["window_label"]))
        for (symbol, snapshot_date), window_labels in windows_by_snapshot.items():
            conn.execute(
                _DELETE_UNCONFIGURED_TREND_WINDOWS,
                (symbol, snapshot_date, window_labels),
            )
        for row in rows:
            conn.execute(
                _UPSERT_TREND_DAILY,
                (
                    row["symbol"],
                    row["date"],
                    row["window_label"],
                    row["far_bars"],
                    row["near_bars"],
                    row.get("slope"),
                    row.get("r2"),
                    row.get("method", "linear_regression"),
                    row.get("observation_count"),
                    row.get("log_slope_per_bar"),
                    row.get("linearity_r2"),
                    row.get("fitted_log_return"),
                    row.get("actual_log_return"),
                    row.get("realized_volatility_daily"),
                    row.get("vol_adjusted_trend"),
                    row.get("efficiency_ratio"),
                    row.get("jackknife_slope_stability"),
                    row.get("adjacent_slope_stability"),
                    row.get("calculation_version"),
                ),
            )
        conn.commit()


def upsert_trend_segmentation_daily(
    summaries: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> None:
    """Replace adaptive segment details and upsert summaries in one transaction."""
    if not summaries:
        return
    symbol = str(summaries[0]["symbol"])
    snapshot_date = summaries[0]["date"]
    lookbacks = [int(row["lookback_bars"]) for row in summaries]
    if any(
        str(row["symbol"]) != symbol or row["date"] != snapshot_date for row in summaries
    ):
        raise ValueError("Segmentation summaries must belong to one symbol/date snapshot.")
    expected_counts = {
        int(row["lookback_bars"]): int(row["segment_count"]) for row in summaries
    }
    actual_counts = dict.fromkeys(expected_counts, 0)
    for row in segments:
        lookback_bars = int(row["lookback_bars"])
        if (
            str(row["symbol"]) != symbol
            or row["date"] != snapshot_date
            or lookback_bars not in expected_counts
        ):
            raise ValueError("Segments must match the summary symbol/date/lookback snapshot.")
        actual_counts[lookback_bars] += 1
    if actual_counts != expected_counts:
        raise ValueError("Segment row counts must match summary segment_count values.")

    with get_conn() as conn:
        conn.execute(
            _DELETE_UNCONFIGURED_SEGMENT_LOOKBACKS,
            (symbol, snapshot_date, lookbacks),
        )
        conn.execute(
            _DELETE_UNCONFIGURED_SEGMENTATION_LOOKBACKS,
            (symbol, snapshot_date, lookbacks),
        )
        for row in summaries:
            lookback_bars = int(row["lookback_bars"])
            conn.execute(
                _DELETE_TREND_SEGMENTS_FOR_LOOKBACK,
                (symbol, snapshot_date, lookback_bars),
            )
            conn.execute(
                _UPSERT_TREND_SEGMENTATION_DAILY,
                (
                    symbol,
                    snapshot_date,
                    row.get("requested_lookback_bars", lookback_bars),
                    lookback_bars,
                    row["observation_count"],
                    row["segment_count"],
                    row["change_point_count"],
                    row.get("selected_rss"),
                    row.get("single_segment_rss"),
                    row.get("selected_bic"),
                    row.get("single_segment_bic"),
                    row.get("bic_improvement"),
                    row["min_segment_bars"],
                    row["max_segments"],
                    row["bic_penalty_multiplier"],
                    row.get("search_mode", "exact"),
                    row.get("is_global_optimum", True),
                    row.get("candidates_evaluated", 0),
                    row.get("refinement_converged", True),
                    Jsonb(row.get("search_config", {})),
                    Jsonb(row.get("search_diagnostics", [])),
                    row["method"],
                    row["calculation_version"],
                ),
            )
        for row in segments:
            conn.execute(
                _UPSERT_TREND_SEGMENT_DAILY,
                (
                    row["symbol"],
                    row["date"],
                    row["lookback_bars"],
                    row["segment_index"],
                    row["start_date"],
                    row["end_date"],
                    row["start_bar_index"],
                    row["end_bar_index"],
                    row["observation_count"],
                    row.get("log_slope_per_bar"),
                    row.get("linearity_r2"),
                    row.get("fitted_log_return"),
                    row.get("fitted_anchor_log_price"),
                    row.get("actual_log_return"),
                    row.get("realized_volatility_daily"),
                    row.get("vol_adjusted_trend"),
                    row.get("efficiency_ratio"),
                    row.get("largest_move_log_return"),
                    row.get("largest_move_date"),
                    row.get("largest_move_bar_index"),
                    row.get("largest_move_path_share"),
                    row["method"],
                    row["calculation_version"],
                ),
            )
        conn.commit()


def fetch_latest_trend_segmentation_date() -> date | None:
    with get_conn() as conn:
        row = conn.execute(_FETCH_LATEST_TREND_SEGMENTATION_DATE).fetchone()
    return row[0] if row and row[0] is not None else None


def fetch_latest_adaptive_segmentation_date() -> date | None:
    """Return the latest canonical adaptive segmentation snapshot date."""
    with get_conn() as conn:
        row = conn.execute(
            _FETCH_LATEST_ADAPTIVE_SEGMENTATION_DATE,
            ("adaptive_segmentation_v3",),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def fetch_trend_segmentation_snapshot(target_date: date) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_TREND_SEGMENTATION_SNAPSHOT, (target_date,)).fetchall()
    return [dict(zip(_TREND_SEGMENTATION_COLS, row)) for row in rows]


def fetch_trend_segment_snapshot(target_date: date) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_TREND_SEGMENT_SNAPSHOT, (target_date,)).fetchall()
    return [dict(zip(_TREND_SEGMENT_COLS, row)) for row in rows]


def fetch_adaptive_resume_candidates(
    symbols: list[str],
    requested_lookbacks: list[int],
    *,
    target_date: date | None,
    calculation_version: str,
) -> list[dict[str, Any]]:
    """Return latest resumable summaries plus persisted segment integrity metadata."""
    if not symbols or not requested_lookbacks:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            _FETCH_ADAPTIVE_RESUME_CANDIDATES,
            (
                symbols,
                requested_lookbacks,
                calculation_version,
                target_date,
                target_date,
            ),
        ).fetchall()
    return [
        dict(zip(_ADAPTIVE_RESUME_CANDIDATE_COLS, row, strict=True))
        for row in rows
    ]


def upsert_pivot_segmentation_daily(
    summaries: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> None:
    """Replace pivot-refined segment details and upsert summaries atomically."""
    if not summaries:
        return
    symbol = str(summaries[0]["symbol"])
    snapshot_date = summaries[0]["date"]
    lookbacks = [int(row["lookback_bars"]) for row in summaries]
    if any(
        str(row["symbol"]) != symbol or row["date"] != snapshot_date for row in summaries
    ):
        raise ValueError("Pivot summaries must belong to one symbol/date snapshot.")
    expected_counts = {
        int(row["lookback_bars"]): int(row["segment_count"]) for row in summaries
    }
    expected_pivots = {
        int(row["lookback_bars"]): int(row["pivot_count"]) for row in summaries
    }
    grouped: dict[int, list[dict[str, Any]]] = {lookback: [] for lookback in expected_counts}
    for row in segments:
        lookback_bars = int(row["lookback_bars"])
        if (
            str(row["symbol"]) != symbol
            or row["date"] != snapshot_date
            or lookback_bars not in grouped
        ):
            raise ValueError("Pivot segments must match the summary snapshot.")
        grouped[lookback_bars].append(row)
    for lookback_bars, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: int(row["segment_index"]))
        if len(ordered) != expected_counts[lookback_bars] or [
            int(row["segment_index"]) for row in ordered
        ] != list(range(expected_counts[lookback_bars])):
            raise ValueError("Pivot segment indexes must be complete and consecutive.")
        pivot_count = sum(row["end_point_type"] in {"high", "low"} for row in ordered)
        if (
            pivot_count != expected_pivots[lookback_bars]
            or pivot_count != len(ordered) - 1
            or any(row["end_point_type"] not in {"high", "low"} for row in ordered[:-1])
            or ordered[-1]["end_point_type"] != "window_end"
        ):
            raise ValueError("Pivot endpoint types do not match the summary counts.")

    with get_conn() as conn:
        conn.execute(
            _DELETE_UNCONFIGURED_PIVOT_SEGMENTS,
            (symbol, snapshot_date, lookbacks),
        )
        conn.execute(
            _DELETE_UNCONFIGURED_PIVOT_SUMMARIES,
            (symbol, snapshot_date, lookbacks),
        )
        for row in summaries:
            lookback_bars = int(row["lookback_bars"])
            conn.execute(
                _DELETE_PIVOT_SEGMENTS_FOR_LOOKBACK,
                (symbol, snapshot_date, lookback_bars),
            )
            conn.execute(
                _UPSERT_PIVOT_SEGMENTATION_DAILY,
                (
                    symbol,
                    snapshot_date,
                    row.get("requested_lookback_bars", lookback_bars),
                    lookback_bars,
                    row["observation_count"],
                    row["pivot_count"],
                    row["segment_count"],
                    row.get("fit_rss"),
                    row["search_radius_bars"],
                    row["min_segment_bars"],
                    Jsonb(row.get("classification_config", {})),
                    Jsonb(row.get("resolution_diagnostics", {})),
                    row["source_segmentation_method"],
                    row["source_segmentation_calculation_version"],
                    row["method"],
                    row["calculation_version"],
                ),
            )
        for row in segments:
            conn.execute(
                _UPSERT_PIVOT_SEGMENT_DAILY,
                (
                    row["symbol"],
                    row["date"],
                    row["lookback_bars"],
                    row["segment_index"],
                    row["start_boundary_index"],
                    row["end_boundary_index_exclusive"],
                    row["start_endpoint_bar_index"],
                    row["end_endpoint_bar_index"],
                    row["start_endpoint_date"],
                    row["end_endpoint_date"],
                    row["observation_count"],
                    row["return_interval_count"],
                    row["segment_type"],
                    row.get("log_slope_per_bar"),
                    row.get("linearity_r2"),
                    row.get("fitted_start_log_price"),
                    row.get("fitted_end_log_price"),
                    row.get("fitted_log_return"),
                    row.get("actual_start_log_price"),
                    row.get("actual_end_log_price"),
                    row.get("actual_start_close"),
                    row.get("actual_end_close"),
                    row.get("actual_log_return"),
                    row.get("realized_volatility_daily"),
                    row.get("vol_adjusted_trend"),
                    row.get("efficiency_ratio"),
                    row["end_point_type"],
                    row.get("pivot_seed_bar_index"),
                    row.get("pivot_search_start_bar_index"),
                    row.get("pivot_search_end_bar_index"),
                    row.get("pivot_displacement_bars"),
                    row.get("pivot_source_left_type"),
                    row.get("pivot_source_right_type"),
                    Jsonb(row.get("pivot_source_segment_indices", [])),
                    row.get("pivot_resolution_status"),
                    row["method"],
                    row["calculation_version"],
                ),
            )
        conn.commit()


def fetch_latest_pivot_segmentation_date() -> date | None:
    with get_conn() as conn:
        row = conn.execute(_FETCH_LATEST_PIVOT_SEGMENTATION_DATE).fetchone()
    return row[0] if row and row[0] is not None else None


def fetch_pivot_segmentation_snapshot(target_date: date) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_PIVOT_SEGMENTATION_SNAPSHOT, (target_date,)).fetchall()
    return [dict(zip(_PIVOT_SEGMENTATION_COLS, row)) for row in rows]


def fetch_pivot_segment_snapshot(target_date: date) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_PIVOT_SEGMENT_SNAPSHOT, (target_date,)).fetchall()
    return [dict(zip(_PIVOT_SEGMENT_COLS, row)) for row in rows]


def upsert_trend_pattern_daily(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    lookbacks_by_snapshot: dict[tuple[str, date], list[int]] = {}
    for row in rows:
        key = (str(row["symbol"]), row["date"])
        lookbacks_by_snapshot.setdefault(key, []).append(int(row["lookback_bars"]))

    with get_conn() as conn:
        for (symbol, snapshot_date), lookbacks in lookbacks_by_snapshot.items():
            conn.execute(
                _DELETE_UNCONFIGURED_PATTERN_LOOKBACKS,
                (symbol, snapshot_date, lookbacks),
            )
        for row in rows:
            conn.execute(
                _UPSERT_TREND_PATTERN_DAILY,
                (
                    row["symbol"], row["date"], row["lookback_bars"], row["regime"],
                    row["directional_bias"], row["path_structure"],
                    row["terminal_state"], row["pattern"],
                    row["pattern_confidence"], row["classification_reason"],
                    row["direction_sequence"], row["segment_count"],
                    row.get("net_fitted_log_return"), row.get("gross_fitted_log_return"),
                    row.get("net_to_gross_ratio"), row.get("latest_segment_direction"),
                    row.get("latest_segment_log_slope"),
                    row.get("latest_segment_fitted_log_return"), row["method"],
                    row["calculation_version"],
                ),
            )
        conn.commit()


def upsert_trend_pattern_v4_daily(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    lookbacks_by_snapshot: dict[tuple[str, date], list[int]] = {}
    for row in rows:
        key = (str(row["symbol"]), row["date"])
        lookbacks_by_snapshot.setdefault(key, []).append(int(row["lookback_bars"]))

    with get_conn() as conn:
        for (symbol, snapshot_date), lookbacks in lookbacks_by_snapshot.items():
            conn.execute(
                _DELETE_UNCONFIGURED_PATTERN_V4_LOOKBACKS,
                (symbol, snapshot_date, lookbacks),
            )
        for row in rows:
            conn.execute(
                _UPSERT_TREND_PATTERN_V4_DAILY,
                (
                    row["symbol"],
                    row["date"],
                    row["lookback_bars"],
                    row["observation_count"],
                    row["source_segment_count"],
                    row["effective_leg_count"],
                    row.get("start_direction"),
                    row.get("structure_index"),
                    row.get("structure_code"),
                    row.get("net_log_return"),
                    row.get("path_efficiency"),
                    row.get("historical_volatility"),
                    row.get("terminal_price_rank"),
                    row.get("terminal_price_position"),
                    row.get("terminal_leg_start_position"),
                    row.get("terminal_breakout_distance_vol"),
                    row.get("squared_movement_time_position"),
                    row.get("squared_movement_concentration"),
                    row["min_abs_fitted_log_return"],
                    row["min_linearity_r2"],
                    row["min_abs_vol_adjusted_trend"],
                    row["pivot_retest_tolerance"],
                    row["source_segmentation_method"],
                    row["source_segmentation_calculation_version"],
                    row["method"],
                    row["calculation_version"],
                ),
            )
        conn.commit()


def fetch_trend_pattern_v4_snapshot(target_date: date) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_TREND_PATTERN_V4_SNAPSHOT, (target_date,)).fetchall()
    return [dict(zip(_TREND_PATTERN_V4_COLS, row)) for row in rows]


def fetch_trend_pattern_v4_for_symbol(
    symbol: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            _FETCH_TREND_PATTERN_V4_FOR_SYMBOL,
            (symbol, start_date, end_date),
        ).fetchall()
    return [dict(zip(_TREND_PATTERN_V4_COLS, row)) for row in rows]


def fetch_latest_indicator_snapshot_date() -> date | None:
    with get_conn() as conn:
        row = conn.execute(_FETCH_LATEST_INDICATOR_DATE).fetchone()
    return row[0] if row and row[0] is not None else None


def fetch_indicator_snapshot_by_date(target_date: date) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_INDICATOR_SNAPSHOT_BY_DATE, (target_date,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=_INDICATOR_SNAPSHOT_COLS)
    return pd.DataFrame(rows, columns=_INDICATOR_SNAPSHOT_COLS)


def fetch_indicator_snapshot_for_symbol(symbol: str, start: date, end: date) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(
            _FETCH_INDICATOR_SNAPSHOT_FOR_SYMBOL,
            (symbol, start, end),
        ).fetchall()
    if not rows:
        return pd.DataFrame(columns=_INDICATOR_SNAPSHOT_COLS)
    df = pd.DataFrame(rows, columns=_INDICATOR_SNAPSHOT_COLS)
    df["date"] = pd.to_datetime(df["date"])
    return df


def fetch_latest_indicator_snapshot_for_symbol(symbol: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(_FETCH_INDICATOR_SNAPSHOT_LATEST_SYMBOL, (symbol,)).fetchone()
    if not row:
        return None
    return dict(zip(_INDICATOR_SNAPSHOT_COLS, row))


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

_FETCH_UNIVERSE_CATEGORY_MAPS = """
SELECT ticker, category, sub_category
FROM universe
WHERE LENGTH(ticker) <= 4
ORDER BY ticker
"""

_FETCH_SEGMENTATION_ANALYSIS_SYMBOLS = """
WITH selected_latest AS (
    SELECT c.universe_ticker, MAX(c.as_of_date) AS as_of_date
    FROM universe_constituents c
    JOIN universe u ON u.ticker = c.universe_ticker
    WHERE u.include_constituents_in_price_update = TRUE
    GROUP BY c.universe_ticker
), candidates AS (
    SELECT c.stock_ticker AS symbol
    FROM universe_constituents c
    JOIN selected_latest latest
      ON latest.universe_ticker = c.universe_ticker
     AND latest.as_of_date = c.as_of_date
    WHERE c.stock_ticker IS NOT NULL
      AND c.asset_cat = 'EC'
      AND c.stock_ticker ~ '^[A-Z]{1,5}$'
    UNION
    SELECT u.ticker AS symbol
    FROM universe u
    WHERE u.universe_type = 'etf'
      AND u.ticker ~ '^[A-Z]{1,5}$'
)
SELECT symbol
FROM candidates
ORDER BY symbol
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


def fetch_universe_category_maps() -> tuple[dict[str, str], dict[str, str]]:
    """Returns ({ticker: category}, {ticker: sub_category}) for all ETF tickers."""
    try:
        with get_source_conn() as conn:
            rows = conn.execute(_FETCH_UNIVERSE_CATEGORY_MAPS).fetchall()
    except Exception:
        log.exception("db.fetch_universe_category_maps.error")
        return {}, {}
    return (
        {r[0]: (r[1] or "") for r in rows},
        {r[0]: (r[2] or "") for r in rows},
    )


def fetch_universe_subcategory_map() -> dict[str, str]:
    """Returns {ticker: sub_category} for all ETF tickers in universe table."""
    _, subcat = fetch_universe_category_maps()
    return subcat


def fetch_segmentation_analysis_symbols() -> list[str]:
    """Return the price-update-aligned symbol scope for segmentation pipelines."""
    try:
        with get_source_conn() as conn:
            rows = conn.execute(_FETCH_SEGMENTATION_ANALYSIS_SYMBOLS).fetchall()
    except Exception:
        log.exception("db.fetch_segmentation_analysis_symbols.error")
        return []
    return [str(row[0]) for row in rows]


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
    start_date: date,
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


def fetch_latest_sector_heat_date() -> date | None:
    with get_conn() as conn:
        row = conn.execute(_FETCH_LATEST_SECTOR_HEAT_DATE).fetchone()
    return row[0] if row and row[0] is not None else None


def fetch_sector_heat_snapshot(target_date: date) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_SECTOR_HEAT_SNAPSHOT, (target_date,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=_SECTOR_HEAT_COLS)
    return pd.DataFrame(rows, columns=_SECTOR_HEAT_COLS)


def fetch_sector_heat_history(universe_ticker: str, start: date, end: date) -> pd.DataFrame:
    with get_conn() as conn:
        rows = conn.execute(_FETCH_SECTOR_HEAT_HISTORY, (universe_ticker, start, end)).fetchall()
    if not rows:
        return pd.DataFrame(columns=_SECTOR_HEAT_COLS)
    df = pd.DataFrame(rows, columns=_SECTOR_HEAT_COLS)
    df["date"] = pd.to_datetime(df["date"])
    return df


def fetch_latest_sector_heat_for_ticker(universe_ticker: str) -> dict[str, Any] | None:
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
