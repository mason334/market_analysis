from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

import pandas as pd
import structlog

from market_analysis.config import settings
from market_analysis.db.queries import (
    fetch_latest_trend_segmentation_date,
    fetch_split_adjusted_close_window,
    fetch_trend_segment_snapshot,
    fetch_trend_segmentation_snapshot,
    upsert_trend_pattern_v4_daily,
)
from market_analysis.indicators.trend_pattern_v4_legs import extract_effective_legs
from market_analysis.indicators.trend_pattern_v4_metrics import (
    TREND_PATTERN_V4_CALCULATION_VERSION,
    TREND_PATTERN_V4_METHOD,
    calculate_trend_pattern_v4_metrics,
)
from market_analysis.indicators.trend_pattern_v4_structure import (
    classify_effective_leg_structure,
)

log = structlog.get_logger(__name__)


def _v4_params() -> dict[str, float]:
    configured: dict[str, Any] = dict(settings.indicators.get("trend_pattern_v4", {}))
    return {
        "min_abs_fitted_log_return": float(
            configured.get("min_abs_fitted_log_return", 0.02)
        ),
        "min_linearity_r2": float(configured.get("min_linearity_r2", 0.35)),
        "min_abs_vol_adjusted_trend": float(
            configured.get("min_abs_vol_adjusted_trend", 0.75)
        ),
        "pivot_retest_tolerance": float(
            configured.get("pivot_retest_tolerance", 0.25)
        ),
    }


def _validate_window_alignment(
    summary: dict[str, Any],
    segments: list[dict[str, Any]],
    close_window: pd.DataFrame,
) -> None:
    lookback_bars = int(summary["lookback_bars"])
    observation_count = int(summary["observation_count"])
    expected_segment_count = int(summary["segment_count"])
    if observation_count != lookback_bars:
        raise ValueError("lookback_bars must equal the persisted observation_count.")
    if len(close_window) != observation_count:
        raise ValueError("Close window does not match the persisted observation_count.")
    if close_window.empty or close_window.index[-1].date() != summary["date"]:
        raise ValueError("Close window does not end on the segmentation snapshot date.")
    if len(segments) != expected_segment_count:
        raise ValueError("Segment rows do not match the persisted segment_count.")
    ordered_indexes = sorted(int(row["segment_index"]) for row in segments)
    if ordered_indexes != list(range(expected_segment_count)):
        raise ValueError("Segment indexes are incomplete or non-consecutive.")

    for segment in segments:
        start = int(segment["start_bar_index"])
        end = int(segment["end_bar_index"])
        if not 0 <= start <= end < observation_count:
            raise ValueError("Segment bar indexes fall outside the close window.")
        if close_window.index[start].date() != segment["start_date"]:
            raise ValueError("Segment start_date does not match the close window.")
        if close_window.index[end].date() != segment["end_date"]:
            raise ValueError("Segment end_date does not match the close window.")
        if segment.get("method") != summary.get("method") or segment.get(
            "calculation_version"
        ) != summary.get("calculation_version"):
            raise ValueError("Segment method/version does not match its summary.")


def _build_v4_row(
    summary: dict[str, Any],
    segments: list[dict[str, Any]],
    close_window: pd.DataFrame,
    params: dict[str, float],
) -> dict[str, Any]:
    _validate_window_alignment(summary, segments, close_window)
    legs = extract_effective_legs(
        segments,
        min_abs_fitted_log_return=params["min_abs_fitted_log_return"],
        min_linearity_r2=params["min_linearity_r2"],
        min_abs_vol_adjusted_trend=params["min_abs_vol_adjusted_trend"],
    )
    structure = None
    if 1 <= len(legs) <= 4:
        structure = classify_effective_leg_structure(
            tuple(leg.fitted_log_return for leg in legs),
            pivot_retest_tolerance=params["pivot_retest_tolerance"],
        )
    terminal_leg_start_index = legs[-1].start_price_index if legs else None
    metrics = calculate_trend_pattern_v4_metrics(
        close_window["close"].to_numpy(dtype=float),
        terminal_leg_start_index=terminal_leg_start_index,
    )

    return {
        "symbol": str(summary["symbol"]),
        "date": summary["date"],
        "lookback_bars": int(summary["lookback_bars"]),
        "observation_count": int(summary["observation_count"]),
        "source_segment_count": int(summary["segment_count"]),
        "effective_leg_count": len(legs),
        "start_direction": None if structure is None else structure.start_direction,
        "structure_index": None if structure is None else structure.structure_index,
        "structure_code": None if structure is None else structure.structure_code,
        **metrics.to_dict(),
        **params,
        "source_segmentation_method": str(summary["method"]),
        "source_segmentation_calculation_version": str(
            summary["calculation_version"]
        ),
        "method": TREND_PATTERN_V4_METHOD,
        "calculation_version": TREND_PATTERN_V4_CALCULATION_VERSION,
    }


def run_trend_pattern_v4_pipeline(target_date: date | None = None) -> int:
    """Build v4 structure and close-path metric snapshots from persisted segments."""
    snapshot_date = target_date or fetch_latest_trend_segmentation_date()
    if snapshot_date is None:
        raise ValueError("No adaptive trend segmentation snapshot is available.")

    summaries = fetch_trend_segmentation_snapshot(snapshot_date)
    segments = fetch_trend_segment_snapshot(snapshot_date)
    if not summaries:
        raise ValueError(f"No adaptive trend segmentation rows found for {snapshot_date}.")
    segments_by_key: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for segment in segments:
        key = (str(segment["symbol"]), int(segment["lookback_bars"]))
        segments_by_key.setdefault(key, []).append(segment)

    summaries_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for summary in summaries:
        if summary["date"] != snapshot_date:
            raise ValueError("Segmentation summary date does not match the requested snapshot.")
        summaries_by_symbol.setdefault(str(summary["symbol"]), []).append(summary)

    params = _v4_params()
    output_rows: list[dict[str, Any]] = []
    skipped = 0
    for symbol, symbol_summaries in summaries_by_symbol.items():
        max_observations = max(int(row["observation_count"]) for row in symbol_summaries)
        full_close_window = fetch_split_adjusted_close_window(
            symbol,
            snapshot_date,
            max_observations,
        )
        for summary in sorted(symbol_summaries, key=lambda row: int(row["lookback_bars"])):
            lookback_bars = int(summary["lookback_bars"])
            observation_count = int(summary["observation_count"])
            close_window = full_close_window.iloc[-observation_count:]
            key = (symbol, lookback_bars)
            try:
                output_rows.append(
                    _build_v4_row(
                        summary,
                        segments_by_key.get(key, []),
                        close_window,
                        params,
                    )
                )
            except Exception:
                skipped += 1
                log.exception(
                    "trend_pattern_v4.row.error",
                    symbol=symbol,
                    date=str(snapshot_date),
                    lookback_bars=lookback_bars,
                )

    upsert_trend_pattern_v4_daily(output_rows)
    structures = Counter(row.get("structure_code") or "no_effective_leg" for row in output_rows)
    log.info(
        "trend_pattern_v4.done",
        date=str(snapshot_date),
        rows=len(output_rows),
        skipped=skipped,
        structures=dict(structures),
    )
    return len(output_rows)
