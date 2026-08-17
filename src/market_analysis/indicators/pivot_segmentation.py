from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

_METHOD = "pivot_seeded_continuous_piecewise_log_linear"
_CALCULATION_VERSION = "pivot_refined_segmentation_v2"
_SOURCE_CALCULATION_VERSION = "adaptive_segmentation_v3"
_EPSILON = 1e-12

_PIVOT_TYPE_BY_TRANSITION = {
    ("up", "down"): "high",
    ("up", "flat"): "high",
    ("flat", "down"): "high",
    ("down", "up"): "low",
    ("down", "flat"): "low",
    ("flat", "up"): "low",
}


@dataclass(frozen=True)
class _MergedSegment:
    direction: str
    start_endpoint_bar_index: int
    end_endpoint_bar_index: int
    source_segment_indices: tuple[int, ...]


@dataclass(frozen=True)
class _PivotSeed:
    seed_group_index: int
    pivot_type: str
    seed_bar_indices: tuple[int, ...]
    left_type: str
    right_type: str
    source_segment_indices: tuple[int, ...]


@dataclass(frozen=True)
class _PivotCandidate:
    seed_group_index: int
    pivot_type: str
    bar_index: int
    close: float
    rank: int
    seed_bar_index: int
    search_start_bar_index: int
    search_end_bar_index: int
    displacement_bars: int
    left_type: str
    right_type: str
    source_segment_indices: tuple[int, ...]


def _finite_number(value: Any, *, field: str) -> float:
    number = float(value)
    if not isfinite(number):
        raise ValueError(f"{field} must be finite.")
    return number


def _classification_thresholds(params: dict[str, Any] | None) -> tuple[float, float, float]:
    values = dict(params or {})
    min_return = float(values.get("min_abs_fitted_log_return", 0.02))
    min_r2 = float(values.get("min_linearity_r2", 0.35))
    min_vol_adjusted = float(values.get("min_abs_vol_adjusted_trend", 0.75))
    if (
        not isfinite(min_return)
        or min_return <= 0.0
        or not isfinite(min_r2)
        or not 0.0 <= min_r2 <= 1.0
        or not isfinite(min_vol_adjusted)
        or min_vol_adjusted <= 0.0
    ):
        raise ValueError("Pivot segment classification thresholds are outside valid ranges.")
    return min_return, min_r2, min_vol_adjusted


def classify_segment_direction(
    segment: dict[str, Any],
    *,
    min_abs_fitted_log_return: float = 0.02,
    min_linearity_r2: float = 0.35,
    min_abs_vol_adjusted_trend: float = 0.75,
) -> str:
    """Classify one fitted segment as up, down, or flat."""
    fitted_return = _finite_number(
        segment.get("fitted_log_return", 0.0), field="fitted_log_return"
    )
    linearity_r2 = _finite_number(segment.get("linearity_r2", 0.0), field="linearity_r2")
    vol_adjusted = segment.get("vol_adjusted_trend")
    finite_vol_adjusted = (
        0.0
        if vol_adjusted is None
        else _finite_number(vol_adjusted, field="vol_adjusted_trend")
    )
    directional = abs(fitted_return) >= min_abs_fitted_log_return and (
        linearity_r2 >= min_linearity_r2
        or abs(finite_vol_adjusted) >= min_abs_vol_adjusted_trend
    )
    if not directional:
        return "flat"
    return "up" if fitted_return > 0.0 else "down"


def _validate_source_segments(
    summary: dict[str, Any], segments: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if summary.get("calculation_version") != _SOURCE_CALCULATION_VERSION:
        raise ValueError(
            "Pivot refinement requires adaptive_segmentation_v3 source rows."
        )
    expected_count = int(summary["segment_count"])
    ordered = sorted(segments, key=lambda row: int(row["segment_index"]))
    if expected_count < 1 or len(ordered) != expected_count:
        raise ValueError("Adaptive segmentation source rows are incomplete.")
    if [int(row["segment_index"]) for row in ordered] != list(range(expected_count)):
        raise ValueError("Adaptive segmentation source indexes must be consecutive.")
    expected_start = 0
    for row in ordered:
        start = int(row["start_bar_index"])
        end = int(row["end_bar_index"])
        if start != expected_start or end < start:
            raise ValueError("Adaptive segmentation source boundaries are invalid.")
        expected_start = end + 1
    if expected_start != int(summary["lookback_bars"]):
        raise ValueError("Adaptive segmentation source rows do not cover the lookback window.")
    return ordered


def merge_classified_segments(
    segments: list[dict[str, Any]],
    *,
    min_abs_fitted_log_return: float = 0.02,
    min_linearity_r2: float = 0.35,
    min_abs_vol_adjusted_trend: float = 0.75,
) -> tuple[_MergedSegment, ...]:
    """Merge adjacent source segments with the same three-state direction."""
    merged: list[_MergedSegment] = []
    for row in sorted(segments, key=lambda item: int(item["segment_index"])):
        segment_index = int(row["segment_index"])
        start_boundary = int(row["start_bar_index"])
        start_endpoint = 0 if start_boundary == 0 else start_boundary - 1
        end_endpoint = int(row["end_bar_index"])
        direction = classify_segment_direction(
            row,
            min_abs_fitted_log_return=min_abs_fitted_log_return,
            min_linearity_r2=min_linearity_r2,
            min_abs_vol_adjusted_trend=min_abs_vol_adjusted_trend,
        )
        if merged and merged[-1].direction == direction:
            previous = merged[-1]
            merged[-1] = _MergedSegment(
                direction=direction,
                start_endpoint_bar_index=previous.start_endpoint_bar_index,
                end_endpoint_bar_index=end_endpoint,
                source_segment_indices=(*previous.source_segment_indices, segment_index),
            )
        else:
            merged.append(
                _MergedSegment(
                    direction=direction,
                    start_endpoint_bar_index=start_endpoint,
                    end_endpoint_bar_index=end_endpoint,
                    source_segment_indices=(segment_index,),
                )
            )
    return tuple(merged)


def _raw_pivot_seeds(merged: tuple[_MergedSegment, ...]) -> tuple[_PivotSeed, ...]:
    seeds: list[_PivotSeed] = []
    for seed_index, (left, right) in enumerate(zip(merged[:-1], merged[1:])):
        pivot_type = _PIVOT_TYPE_BY_TRANSITION.get((left.direction, right.direction))
        if pivot_type is None:
            raise ValueError("Merged segment directions do not define a pivot transition.")
        seeds.append(
            _PivotSeed(
                seed_group_index=seed_index,
                pivot_type=pivot_type,
                seed_bar_indices=(left.end_endpoint_bar_index,),
                left_type=left.direction,
                right_type=right.direction,
                source_segment_indices=tuple(
                    dict.fromkeys((*left.source_segment_indices, *right.source_segment_indices))
                ),
            )
        )
    return tuple(seeds)


def _collapse_same_type_seeds(seeds: tuple[_PivotSeed, ...]) -> tuple[_PivotSeed, ...]:
    collapsed: list[_PivotSeed] = []
    for seed in seeds:
        if collapsed and collapsed[-1].pivot_type == seed.pivot_type:
            previous = collapsed[-1]
            collapsed[-1] = _PivotSeed(
                seed_group_index=previous.seed_group_index,
                pivot_type=seed.pivot_type,
                seed_bar_indices=(*previous.seed_bar_indices, *seed.seed_bar_indices),
                left_type=previous.left_type,
                right_type=seed.right_type,
                source_segment_indices=tuple(
                    dict.fromkeys(
                        (*previous.source_segment_indices, *seed.source_segment_indices)
                    )
                ),
            )
        else:
            collapsed.append(seed)
    return tuple(collapsed)


def _candidate_pool(
    seed: _PivotSeed,
    closes: np.ndarray,
    *,
    search_radius_bars: int,
    min_segment_bars: int,
) -> tuple[_PivotCandidate, ...]:
    size = len(closes)
    minimum_position = min_segment_bars - 1
    maximum_position = size - min_segment_bars - 1
    position_to_seed: dict[int, tuple[int, int, int]] = {}
    for seed_bar_index in seed.seed_bar_indices:
        search_start = max(0, seed_bar_index - search_radius_bars)
        search_end = min(size - 1, seed_bar_index + search_radius_bars)
        for position in range(
            max(search_start, minimum_position), min(search_end, maximum_position) + 1
        ):
            candidate_seed = (abs(position - seed_bar_index), seed_bar_index, search_start)
            current = position_to_seed.get(position)
            if current is None or candidate_seed < current:
                position_to_seed[position] = candidate_seed
    positions = list(position_to_seed)
    if seed.pivot_type == "high":
        positions.sort(
            key=lambda position: (
                -float(closes[position]),
                position_to_seed[position][0],
                position,
            )
        )
    else:
        positions.sort(
            key=lambda position: (
                float(closes[position]),
                position_to_seed[position][0],
                position,
            )
        )
    candidates: list[_PivotCandidate] = []
    for rank, position in enumerate(positions):
        displacement, seed_bar_index, search_start = position_to_seed[position]
        search_end = min(size - 1, seed_bar_index + search_radius_bars)
        candidates.append(
            _PivotCandidate(
                seed_group_index=seed.seed_group_index,
                pivot_type=seed.pivot_type,
                bar_index=position,
                close=float(closes[position]),
                rank=rank,
                seed_bar_index=seed_bar_index,
                search_start_bar_index=search_start,
                search_end_bar_index=search_end,
                displacement_bars=position - seed_bar_index,
                left_type=seed.left_type,
                right_type=seed.right_type,
                source_segment_indices=seed.source_segment_indices,
            )
        )
    return tuple(candidates)


def _path_score(path: tuple[_PivotCandidate, ...]) -> tuple[Any, ...]:
    return (
        -len(path),
        sum(candidate.rank for candidate in path),
        sum(abs(candidate.displacement_bars) for candidate in path),
        tuple(candidate.bar_index for candidate in path),
        tuple(candidate.seed_group_index for candidate in path),
    )


def _select_pivots(
    seeds: tuple[_PivotSeed, ...],
    closes: np.ndarray,
    *,
    search_radius_bars: int,
    min_segment_bars: int,
) -> tuple[tuple[_PivotCandidate, ...], dict[str, Any]]:
    pools = [
        _candidate_pool(
            seed,
            closes,
            search_radius_bars=search_radius_bars,
            min_segment_bars=min_segment_bars,
        )
        for seed in seeds
    ]
    states: dict[tuple[int | None, str | None], tuple[_PivotCandidate, ...]] = {
        (None, None): ()
    }
    for pool in pools:
        next_states = dict(states)
        for path in states.values():
            previous = path[-1] if path else None
            for candidate in pool:
                if previous is not None and (
                    candidate.pivot_type == previous.pivot_type
                    or candidate.bar_index - previous.bar_index < min_segment_bars
                ):
                    continue
                candidate_path = (*path, candidate)
                key = (candidate.bar_index, candidate.pivot_type)
                incumbent = next_states.get(key)
                if incumbent is None or _path_score(candidate_path) < _path_score(incumbent):
                    next_states[key] = candidate_path
        states = next_states
    selected = min(states.values(), key=_path_score)
    selected_groups = {candidate.seed_group_index for candidate in selected}
    diagnostics = {
        "raw_seed_count": sum(len(seed.seed_bar_indices) for seed in seeds),
        "normalized_seed_count": len(seeds),
        "selected_pivot_count": len(selected),
        "discarded_seed_groups": [
            {
                "seed_group_index": seed.seed_group_index,
                "pivot_type": seed.pivot_type,
                "seed_bar_indices": list(seed.seed_bar_indices),
                "reason": "no_position_in_best_feasible_alternating_path",
            }
            for seed in seeds
            if seed.seed_group_index not in selected_groups
        ],
    }
    return selected, diagnostics


def _continuous_fit(
    log_prices: np.ndarray, boundaries: tuple[int, ...]
) -> tuple[np.ndarray, np.ndarray, float]:
    size = len(log_prices)
    scale = float(max(size - 1, 1))
    x = np.arange(size, dtype=float) / scale
    columns = [np.ones(size, dtype=float), x]
    columns.extend(
        np.maximum(x - float(boundary - 1) / scale, 0.0)
        for boundary in boundaries[1:-1]
    )
    design = np.column_stack(columns)
    coefficients, _, _, _ = np.linalg.lstsq(design, log_prices, rcond=None)
    fitted = design @ coefficients
    residuals = log_prices - fitted
    rss = max(float(np.dot(residuals, residuals)), 0.0)
    slopes = coefficients[1] + np.concatenate(
        (np.array([0.0]), np.cumsum(coefficients[2:], dtype=float))
    )
    slopes /= scale
    return fitted, slopes.astype(float), rss


def _linearity_r2(actual: np.ndarray, fitted: np.ndarray) -> float:
    residuals = actual - fitted
    rss = float(np.dot(residuals, residuals))
    centered = actual - float(np.mean(actual))
    total = float(np.dot(centered, centered))
    if total <= _EPSILON:
        return 1.0 if rss <= _EPSILON else 0.0
    return float(np.clip(1.0 - rss / total, 0.0, 1.0))


def _segment_metrics(
    log_prices: np.ndarray,
    fitted: np.ndarray,
    slope: float,
    start_boundary: int,
    end_boundary: int,
) -> dict[str, Any]:
    start_endpoint = 0 if start_boundary == 0 else start_boundary - 1
    end_endpoint = end_boundary - 1
    actual = log_prices[start_endpoint:end_boundary]
    fitted_segment = fitted[start_endpoint:end_boundary]
    log_returns = np.diff(actual)
    return_interval_count = len(log_returns)
    actual_return = float(actual[-1] - actual[0])
    fitted_return = float(fitted_segment[-1] - fitted_segment[0])
    total_path = float(np.abs(log_returns).sum())
    efficiency = 0.0 if total_path <= _EPSILON else abs(actual_return) / total_path
    volatility = float(np.std(log_returns, ddof=1)) if len(log_returns) >= 2 else None
    if volatility is not None and volatility <= _EPSILON:
        volatility = None
    vol_adjusted = None
    if volatility is not None and return_interval_count > 0:
        vol_adjusted = float(slope * np.sqrt(return_interval_count) / volatility)
    return {
        "start_endpoint_bar_index": start_endpoint,
        "end_endpoint_bar_index": end_endpoint,
        "observation_count": end_boundary - start_boundary,
        "return_interval_count": return_interval_count,
        "log_slope_per_bar": float(slope),
        "linearity_r2": _linearity_r2(actual, fitted_segment),
        "fitted_start_log_price": float(fitted_segment[0]),
        "fitted_end_log_price": float(fitted_segment[-1]),
        "fitted_log_return": fitted_return,
        "actual_start_log_price": float(actual[0]),
        "actual_end_log_price": float(actual[-1]),
        "actual_start_close": float(np.exp(actual[0])),
        "actual_end_close": float(np.exp(actual[-1])),
        "actual_log_return": actual_return,
        "realized_volatility_daily": volatility,
        "vol_adjusted_trend": vol_adjusted,
        "efficiency_ratio": float(np.clip(efficiency, 0.0, 1.0)),
    }


def compute_pivot_segmentation(
    symbol: str,
    df: pd.DataFrame,
    source_summary: dict[str, Any],
    source_segments: list[dict[str, Any]],
    *,
    search_radius_bars: int = 5,
    min_segment_bars: int = 5,
    classification_params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Refine adaptive segments into deterministic close-pivot segments."""
    if search_radius_bars < 0 or min_segment_bars < 2:
        raise ValueError("Pivot search parameters are outside valid ranges.")
    lookback_bars = int(source_summary["lookback_bars"])
    if df.empty or "close" not in df or len(df) < lookback_bars:
        raise ValueError("Insufficient close observations for pivot refinement.")
    ordered_source = _validate_source_segments(source_summary, source_segments)
    source_date = source_summary["date"]
    frame = df[df.index.date <= source_date].iloc[-lookback_bars:].copy()
    if len(frame) != lookback_bars or frame.index[-1].date() != source_date:
        raise ValueError("Close window does not match the adaptive segmentation snapshot date.")
    closes = frame["close"].to_numpy(dtype=float)
    if not np.isfinite(closes).all() or np.any(closes <= 0.0):
        raise ValueError("Pivot refinement requires finite positive close prices.")

    min_return, min_r2, min_vol_adjusted = _classification_thresholds(
        classification_params
    )
    merged = merge_classified_segments(
        ordered_source,
        min_abs_fitted_log_return=min_return,
        min_linearity_r2=min_r2,
        min_abs_vol_adjusted_trend=min_vol_adjusted,
    )
    raw_seeds = _raw_pivot_seeds(merged)
    normalized_seeds = _collapse_same_type_seeds(raw_seeds)
    selected_pivots, diagnostics = _select_pivots(
        normalized_seeds,
        closes,
        search_radius_bars=search_radius_bars,
        min_segment_bars=min_segment_bars,
    )
    boundaries = (0, *(pivot.bar_index + 1 for pivot in selected_pivots), lookback_bars)
    log_prices = np.log(closes)
    fitted, slopes, rss = _continuous_fit(log_prices, boundaries)

    classification_config = {
        "min_abs_fitted_log_return": min_return,
        "min_linearity_r2": min_r2,
        "min_abs_vol_adjusted_trend": min_vol_adjusted,
    }
    summary = {
        "symbol": symbol,
        "date": source_date,
        "requested_lookback_bars": int(
            source_summary.get("requested_lookback_bars", lookback_bars)
        ),
        "lookback_bars": lookback_bars,
        "observation_count": lookback_bars,
        "pivot_count": len(selected_pivots),
        "segment_count": len(boundaries) - 1,
        "fit_rss": rss,
        "search_radius_bars": search_radius_bars,
        "min_segment_bars": min_segment_bars,
        "classification_config": classification_config,
        "resolution_diagnostics": diagnostics,
        "source_segmentation_method": source_summary["method"],
        "source_segmentation_calculation_version": source_summary[
            "calculation_version"
        ],
        "method": _METHOD,
        "calculation_version": _CALCULATION_VERSION,
    }

    segments: list[dict[str, Any]] = []
    for segment_index, (start_boundary, end_boundary) in enumerate(
        zip(boundaries[:-1], boundaries[1:])
    ):
        metrics = _segment_metrics(
            log_prices,
            fitted,
            float(slopes[segment_index]),
            start_boundary,
            end_boundary,
        )
        endpoint_date: date = frame.index[metrics["end_endpoint_bar_index"]].date()
        start_endpoint_date: date = frame.index[
            metrics["start_endpoint_bar_index"]
        ].date()
        pivot = selected_pivots[segment_index] if segment_index < len(selected_pivots) else None
        row = {
            "symbol": symbol,
            "date": source_date,
            "lookback_bars": lookback_bars,
            "segment_index": segment_index,
            "start_boundary_index": start_boundary,
            "end_boundary_index_exclusive": end_boundary,
            "start_endpoint_date": start_endpoint_date,
            "end_endpoint_date": endpoint_date,
            **metrics,
            "end_point_type": pivot.pivot_type if pivot is not None else "window_end",
            "pivot_seed_bar_index": None if pivot is None else pivot.seed_bar_index,
            "pivot_search_start_bar_index": (
                None if pivot is None else pivot.search_start_bar_index
            ),
            "pivot_search_end_bar_index": (
                None if pivot is None else pivot.search_end_bar_index
            ),
            "pivot_displacement_bars": (
                None if pivot is None else pivot.displacement_bars
            ),
            "pivot_source_left_type": None if pivot is None else pivot.left_type,
            "pivot_source_right_type": None if pivot is None else pivot.right_type,
            "pivot_source_segment_indices": (
                [] if pivot is None else list(pivot.source_segment_indices)
            ),
            "pivot_resolution_status": (
                None if pivot is None else "selected_close_extreme"
            ),
            "method": _METHOD,
            "calculation_version": _CALCULATION_VERSION,
        }
        row["segment_type"] = classify_segment_direction(
            row,
            min_abs_fitted_log_return=min_return,
            min_linearity_r2=min_r2,
            min_abs_vol_adjusted_trend=min_vol_adjusted,
        )
        segments.append(row)
    return summary, segments


def reconstruct_pivot_fit(
    df: pd.DataFrame,
    summary: dict[str, Any],
    segments: list[dict[str, Any]],
) -> pd.DataFrame:
    """Reconstruct a persisted pivot fit for diagnostics or downstream charts."""
    lookback_bars = int(summary["lookback_bars"])
    source_date = summary["date"]
    frame = df[df.index.date <= source_date].iloc[-lookback_bars:]
    if len(frame) != lookback_bars or not segments:
        return pd.DataFrame()
    ordered = sorted(segments, key=lambda row: int(row["segment_index"]))
    boundaries = (
        0,
        *(int(row["end_boundary_index_exclusive"]) for row in ordered[:-1]),
        lookback_bars,
    )
    log_prices = np.log(frame["close"].to_numpy(dtype=float))
    fitted, _, _ = _continuous_fit(log_prices, boundaries)
    return pd.DataFrame(
        {
            "close": np.exp(log_prices),
            "log_close": log_prices,
            "fitted_close": np.exp(fitted),
            "fitted_log_close": fitted,
            "residual_log": log_prices - fitted,
        },
        index=frame.index,
    )
