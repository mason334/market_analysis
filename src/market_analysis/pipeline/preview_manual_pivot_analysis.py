"""提供单只标的、只读的自适应分段与 Pivot 精炼分析。"""

from __future__ import annotations

import re
from datetime import date
from time import perf_counter
from typing import Any

import numpy as np

from market_analysis.config import settings
from market_analysis.db.queries import fetch_ohlcv
from market_analysis.segmentation.adaptive_segmentation import (
    compute_adaptive_segmentation,
    recommended_max_segments,
    reconstruct_adaptive_fit,
)
from market_analysis.segmentation.pivot_segmentation import (
    compute_pivot_segmentation,
    reconstruct_pivot_fit,
)

_SYMBOL_PATTERN = re.compile(r"^[A-Z]{1,5}$")
_MIN_OBSERVATIONS = 40
_MAX_OBSERVATIONS = 500


def _resolved_number(value: int | float | None, default: Any, cast: type) -> Any:
    return cast(default if value is None else value)


def _fit_points(frame: Any, *, include_segment_index: bool = False) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        point = {
            "date": index.date(),
            "close": float(row["close"]),
            "fitted_close": float(row["fitted_close"]),
        }
        if include_segment_index:
            point["segment_index"] = int(row["segment_index"])
        points.append(point)
    return points


def _ohlcv_points(frame: Any) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        point: dict[str, Any] = {"date": index.date()}
        for column in ("open", "high", "low", "close", "volume"):
            value = row[column]
            point[column] = None if value is None or not np.isfinite(value) else float(value)
        points.append(point)
    return points


def compute_manual_pivot_analysis_for_symbol(
    symbol: str,
    *,
    start_date: date,
    end_date: date,
    adaptive_min_segment_bars: int | None = None,
    adaptive_max_segments: int | None = None,
    adaptive_bic_penalty_multiplier: float | None = None,
    pivot_search_radius_bars: int | None = None,
    pivot_min_segment_bars: int | None = None,
    pivot_min_abs_fitted_log_return: float | None = None,
    pivot_min_linearity_r2: float | None = None,
    pivot_min_abs_vol_adjusted_trend: float | None = None,
) -> dict[str, Any]:
    """Run both segmentation stages for one inclusive date range without writes."""
    normalized_symbol = symbol.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(normalized_symbol):
        raise ValueError("symbol must contain 1 to 5 uppercase English letters.")
    if start_date > end_date:
        raise ValueError("start_date must be on or before end_date.")

    adaptive_defaults = dict(settings.indicators.get("adaptive_segmentation", {}))
    pivot_defaults = dict(settings.indicators.get("pivot_refinement", {}))
    classification_defaults = dict(adaptive_defaults.get("segment_classification", {}))
    search_params = dict(adaptive_defaults.get("search", {}))
    source = str(settings.pipeline.get("source", "tiingo"))

    adaptive_min = _resolved_number(
        adaptive_min_segment_bars, adaptive_defaults.get("min_segment_bars", 5), int
    )
    adaptive_max_cap = int(adaptive_defaults.get("max_segments_cap", 10))
    adaptive_max = (
        int(adaptive_max_segments) if adaptive_max_segments is not None else None
    )
    adaptive_bic = _resolved_number(
        adaptive_bic_penalty_multiplier,
        adaptive_defaults.get("bic_penalty_multiplier", 2.0),
        float,
    )
    pivot_radius = _resolved_number(
        pivot_search_radius_bars, pivot_defaults.get("search_radius_bars", 5), int
    )
    pivot_min = _resolved_number(
        pivot_min_segment_bars, pivot_defaults.get("min_segment_bars", 5), int
    )
    classification = {
        "min_abs_fitted_log_return": _resolved_number(
            pivot_min_abs_fitted_log_return,
            classification_defaults.get("min_abs_fitted_log_return", 0.02),
            float,
        ),
        "min_linearity_r2": _resolved_number(
            pivot_min_linearity_r2,
            classification_defaults.get("min_linearity_r2", 0.35),
            float,
        ),
        "min_abs_vol_adjusted_trend": _resolved_number(
            pivot_min_abs_vol_adjusted_trend,
            classification_defaults.get("min_abs_vol_adjusted_trend", 0.75),
            float,
        ),
    }

    if not 2 <= adaptive_min <= 30:
        raise ValueError("adaptive_min_segment_bars must be between 2 and 30.")
    if adaptive_max is not None and not 1 <= adaptive_max <= 20:
        raise ValueError("adaptive_max_segments must be between 1 and 20.")
    if not 1 <= adaptive_max_cap <= 20:
        raise ValueError("configured max_segments_cap must be between 1 and 20.")
    if not 0.1 <= adaptive_bic <= 10.0:
        raise ValueError("adaptive_bic_penalty_multiplier must be between 0.1 and 10.")
    if not 0 <= pivot_radius <= 30:
        raise ValueError("pivot_search_radius_bars must be between 0 and 30.")
    if not 2 <= pivot_min <= 30:
        raise ValueError("pivot_min_segment_bars must be between 2 and 30.")
    if not 0.0 <= classification["min_abs_fitted_log_return"] <= 1.0:
        raise ValueError("pivot_min_abs_fitted_log_return must be between 0 and 1.")
    if not 0.0 <= classification["min_linearity_r2"] <= 1.0:
        raise ValueError("pivot_min_linearity_r2 must be between 0 and 1.")
    if not 0.0 <= classification["min_abs_vol_adjusted_trend"] <= 10.0:
        raise ValueError("pivot_min_abs_vol_adjusted_trend must be between 0 and 10.")

    frame = fetch_ohlcv(normalized_symbol, source=source)
    if frame.empty:
        raise ValueError(f"No {source} OHLCV data found for {normalized_symbol}.")
    window = frame[
        (frame.index.date >= start_date) & (frame.index.date <= end_date)
    ].copy()
    observation_count = len(window)
    if observation_count < _MIN_OBSERVATIONS:
        raise ValueError(
            f"Selected range contains {observation_count} observations; "
            f"at least {_MIN_OBSERVATIONS} are required."
        )
    if observation_count > _MAX_OBSERVATIONS:
        raise ValueError(
            f"Selected range contains {observation_count} observations; "
            f"at most {_MAX_OBSERVATIONS} are supported."
        )
    if adaptive_max is None:
        adaptive_max = recommended_max_segments(observation_count, adaptive_max_cap)
    if not np.isfinite(window[["open", "high", "low", "close"]].to_numpy()).all():
        raise ValueError("Selected OHLC window contains non-finite values.")

    started = perf_counter()
    adaptive_summary, adaptive_segments = compute_adaptive_segmentation(
        normalized_symbol,
        window,
        lookback_bars=observation_count,
        min_segment_bars=adaptive_min,
        max_segments=adaptive_max,
        bic_penalty_multiplier=adaptive_bic,
        search_params=search_params,
    )
    if adaptive_summary is None or not adaptive_segments:
        raise ValueError("Adaptive segmentation calculation returned no result.")
    pivot_summary, pivot_segments = compute_pivot_segmentation(
        normalized_symbol,
        window,
        adaptive_summary,
        adaptive_segments,
        search_radius_bars=pivot_radius,
        min_segment_bars=pivot_min,
        classification_params=classification,
    )
    adaptive_fit = reconstruct_adaptive_fit(window, observation_count, adaptive_segments)
    pivot_fit = reconstruct_pivot_fit(window, pivot_summary, pivot_segments)
    if adaptive_fit.empty or pivot_fit.empty:
        raise ValueError("Unable to reconstruct one or both fitted paths.")

    effective_start = window.index[0].date()
    effective_end = window.index[-1].date()
    return {
        "request": {
            "symbol": normalized_symbol,
            "start_date": start_date,
            "end_date": end_date,
            "parameters": {
                "adaptive_min_segment_bars": adaptive_min_segment_bars,
                "adaptive_max_segments": adaptive_max_segments,
                "adaptive_bic_penalty_multiplier": adaptive_bic_penalty_multiplier,
                "pivot_search_radius_bars": pivot_search_radius_bars,
                "pivot_min_segment_bars": pivot_min_segment_bars,
                "pivot_min_abs_fitted_log_return": (
                    pivot_min_abs_fitted_log_return
                ),
                "pivot_min_linearity_r2": pivot_min_linearity_r2,
                "pivot_min_abs_vol_adjusted_trend": (
                    pivot_min_abs_vol_adjusted_trend
                ),
            },
        },
        "resolved_window": {
            "symbol": normalized_symbol,
            "requested_start_date": start_date,
            "requested_end_date": end_date,
            "effective_start_date": effective_start,
            "effective_end_date": effective_end,
            "observation_count": observation_count,
            "price_source": source,
        },
        "resolved_parameters": {
            "adaptive": {
                "min_segment_bars": adaptive_min,
                "max_segments": adaptive_max,
                "bic_penalty_multiplier": adaptive_bic,
                "search": adaptive_summary["search_config"],
            },
            "pivot": {
                "search_radius_bars": pivot_radius,
                "min_segment_bars": pivot_min,
                "classification": classification,
            },
        },
        "ohlcv_points": _ohlcv_points(window),
        "adaptive": {
            "summary": adaptive_summary,
            "segments": adaptive_segments,
            "fitted_points": _fit_points(adaptive_fit),
        },
        "pivot": {
            "summary": pivot_summary,
            "segments": pivot_segments,
            "fitted_points": _fit_points(pivot_fit, include_segment_index=True),
        },
        "elapsed_seconds": perf_counter() - started,
    }
