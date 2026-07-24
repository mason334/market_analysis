from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np

_METHOD = "rule_based_adaptive_segments"
_CALCULATION_VERSION = "trend_pattern_v3"
_EPSILON = 1e-12


def _compress(values: list[str]) -> list[str]:
    compressed: list[str] = []
    for value in values:
        if not compressed or compressed[-1] != value:
            compressed.append(value)
    return compressed


def _segment_direction(
    segment: dict[str, Any],
    min_abs_fitted_log_return: float,
    min_linearity_r2: float,
    min_abs_vol_adjusted_trend: float,
) -> str:
    fitted_return = float(segment.get("fitted_log_return") or 0.0)
    linearity_r2 = float(segment.get("linearity_r2") or 0.0)
    vol_adjusted = float(segment.get("vol_adjusted_trend") or 0.0)
    is_directional = abs(fitted_return) >= min_abs_fitted_log_return and (
        linearity_r2 >= min_linearity_r2
        or abs(vol_adjusted) >= min_abs_vol_adjusted_trend
    )
    if not is_directional:
        return "flat"
    return "up" if fitted_return > 0 else "down"


def _directional_legs(
    ordered: list[dict[str, Any]], directions: list[str]
) -> list[tuple[str, float]]:
    """Remove flat segments and merge adjacent effective directions."""
    legs: list[tuple[str, float]] = []
    for segment, direction in zip(ordered, directions, strict=True):
        if direction == "flat":
            continue
        fitted_return = float(segment.get("fitted_log_return") or 0.0)
        if legs and legs[-1][0] == direction:
            legs[-1] = (direction, legs[-1][1] + fitted_return)
        else:
            legs.append((direction, fitted_return))
    return legs


def _relative_similarity(left: float, right: float) -> float:
    scale = max(abs(left), abs(right), _EPSILON)
    return float(np.clip(1.0 - abs(abs(left) - abs(right)) / scale, 0.0, 1.0))


def _is_contracting(amplitudes: list[float], min_change: float) -> bool:
    return len(amplitudes) >= 3 and all(
        current <= previous * (1.0 - min_change)
        for previous, current in zip(amplitudes, amplitudes[1:])
    )


def _is_expanding(amplitudes: list[float], min_change: float) -> bool:
    return len(amplitudes) >= 3 and all(
        current >= previous * (1.0 + min_change)
        for previous, current in zip(amplitudes, amplitudes[1:])
    )


def _double_test_candidate(
    leg_directions: list[str],
    amplitudes: list[float],
    similarity_tolerance: float,
    confirmation_ratio: float,
) -> tuple[str, float] | None:
    """Return a double-test-like pattern and a simple geometry score."""
    if len(leg_directions) == 3:
        reference_left, reference_right = amplitudes[0], amplitudes[1]
        confirmation = amplitudes[2]
        pattern = (
            "double_bottom_like" if leg_directions == ["up", "down", "up"] else None
        )
        if leg_directions == ["down", "up", "down"]:
            pattern = "double_top_like"
    elif len(leg_directions) == 4:
        reference_left, reference_right = amplitudes[1], amplitudes[2]
        confirmation = amplitudes[3]
        pattern = (
            "double_top_like"
            if leg_directions == ["up", "down", "up", "down"]
            else None
        )
        if leg_directions == ["down", "up", "down", "up"]:
            pattern = "double_bottom_like"
    else:
        return None

    if pattern is None:
        return None
    similarity = _relative_similarity(reference_left, reference_right)
    confirmation_strength = confirmation / max(
        max(reference_left, reference_right) * confirmation_ratio, _EPSILON
    )
    if similarity < 1.0 - similarity_tolerance or confirmation_strength < 1.0:
        return None
    return pattern, float(np.clip(min(similarity, confirmation_strength), 0.0, 1.0))


def _directional_balance(leg_returns: list[float]) -> float:
    upward = sum(value for value in leg_returns if value > 0)
    downward = sum(abs(value) for value in leg_returns if value < 0)
    if upward <= _EPSILON or downward <= _EPSILON:
        return 1.0
    return float(min(upward, downward) / max(upward, downward))


def _simple_pattern_confidence(
    regime: str,
    path_structure: str,
    net_to_gross: float,
    gross_return: float,
    segment_count: int,
    min_return: float,
    leg_returns: list[float],
    structure_score: float,
) -> float:
    """Produce a transparent descriptive score rather than a probability."""
    if regime == "irregular":
        return 0.0
    if not leg_returns:
        flat_scale = min_return * max(segment_count, 1)
        return float(np.clip(1.0 - gross_return / flat_scale, 0.0, 1.0))
    if regime == "ranging":
        return float(np.clip(1.0 - net_to_gross, 0.0, 1.0))
    if path_structure in {"reversal", "double_test", "complex_reversal"}:
        return float(
            np.clip(min(_directional_balance(leg_returns), structure_score), 0.0, 1.0)
        )
    return float(np.clip(net_to_gross, 0.0, 1.0))


def classify_trend_pattern(
    summary: dict[str, Any],
    segments: list[dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Classify a long-window path into layered, descriptive attributes."""
    expected_count = int(summary["segment_count"])
    if expected_count < 1:
        raise ValueError("Adaptive segmentation must contain at least one segment.")
    ordered = sorted(segments, key=lambda row: int(row["segment_index"]))
    if len(ordered) != expected_count or [
        int(row["segment_index"]) for row in ordered
    ] != list(range(expected_count)):
        raise ValueError("Adaptive segments must be complete and consecutively indexed.")

    min_return = float(params.get("min_abs_fitted_log_return", 0.02))
    min_r2 = float(params.get("min_linearity_r2", 0.35))
    min_vol_adjusted = float(params.get("min_abs_vol_adjusted_trend", 0.75))
    range_ratio_max = float(params.get("range_net_to_gross_max", 0.35))
    similarity_tolerance = float(params.get("swing_similarity_tolerance", 0.25))
    amplitude_change_min = float(params.get("swing_amplitude_change_min", 0.15))
    confirmation_ratio = float(params.get("double_test_confirmation_ratio", 0.50))
    if (
        min_return <= 0
        or not 0 <= min_r2 <= 1
        or min_vol_adjusted <= 0
        or not 0 < range_ratio_max <= 1
        or not 0 <= similarity_tolerance < 1
        or not 0 < amplitude_change_min < 1
        or not 0 < confirmation_ratio <= 1
    ):
        raise ValueError("Trend pattern thresholds are outside their valid ranges.")

    directions = [
        _segment_direction(segment, min_return, min_r2, min_vol_adjusted)
        for segment in ordered
    ]
    compressed = _compress(directions)
    terminal_state = compressed[-1]
    legs = _directional_legs(ordered, directions)
    leg_directions = [direction for direction, _ in legs]
    leg_returns = [value for _, value in legs]
    amplitudes = [abs(value) for value in leg_returns]

    fitted_returns = [float(segment.get("fitted_log_return") or 0.0) for segment in ordered]
    net_return = float(sum(fitted_returns))
    gross_return = float(sum(abs(value) for value in fitted_returns))
    net_to_gross = 0.0 if gross_return <= _EPSILON else abs(net_return) / gross_return
    directional_bias = "neutral"
    if abs(net_return) >= min_return and net_to_gross > range_ratio_max:
        directional_bias = "up" if net_return > 0 else "down"

    regime = "irregular"
    path_structure = "mixed"
    pattern = "irregular_path"
    reason = "no_supported_path_geometry"
    structure_score = 1.0

    if not legs:
        regime = "ranging"
        path_structure = "flat"
        pattern = "range_bound"
        reason = "all_segments_flat"
    elif len(legs) == 1:
        regime = "trending"
        path_structure = "single_leg"
        pattern = f"{leg_directions[0]}trend"
        reason = "single_effective_direction"
        if terminal_state == "flat":
            pattern = f"{leg_directions[0]}trend_then_sideways"
            reason = "single_direction_then_flat"
    elif len(legs) == 2:
        regime = "transitioning"
        path_structure = "reversal"
        is_bottom = leg_directions == ["down", "up"]
        pattern = "bottom_reversal" if is_bottom else "top_reversal"
        reason = "down_to_up_reversal" if is_bottom else "up_to_down_reversal"
        if terminal_state == "flat":
            pattern = f"{pattern}_then_sideways"
            reason = f"{reason}_then_flat"
    else:
        double_test = _double_test_candidate(
            leg_directions,
            amplitudes,
            similarity_tolerance,
            confirmation_ratio,
        )
        if double_test is not None:
            regime = "transitioning"
            path_structure = "double_test"
            pattern, structure_score = double_test
            reason = "similar_extrema_with_confirming_leg"
        elif net_to_gross <= range_ratio_max:
            regime = "ranging"
            directional_bias = "neutral"
            if _is_contracting(amplitudes, amplitude_change_min):
                path_structure = "contracting"
                pattern = "contracting_range"
                reason = "alternating_legs_with_decreasing_amplitude"
            elif _is_expanding(amplitudes, amplitude_change_min):
                path_structure = "expanding"
                pattern = "expanding_range"
                reason = "alternating_legs_with_increasing_amplitude"
            else:
                path_structure = "stable_range"
                pattern = "range_bound"
                reason = "low_net_to_gross_movement"
        elif len(legs) == 3 and leg_directions == ["up", "down", "up"]:
            if net_return > 0:
                regime = "trending"
                if amplitudes[2] > amplitudes[1]:
                    path_structure = "resumption"
                    pattern = "uptrend_resumption"
                    reason = "pullback_recovered_by_latest_up_leg"
                else:
                    path_structure = "pullback"
                    pattern = "uptrend_with_pullback"
                    reason = "positive_path_with_intermediate_pullback"
            else:
                regime = "transitioning"
                path_structure = "complex_reversal"
                pattern = "complex_top_reversal"
                reason = "up_down_up_path_with_negative_net_bias"
        elif len(legs) == 3 and leg_directions == ["down", "up", "down"]:
            if net_return < 0:
                regime = "trending"
                if amplitudes[2] > amplitudes[1]:
                    path_structure = "resumption"
                    pattern = "downtrend_resumption"
                    reason = "rebound_reversed_by_latest_down_leg"
                else:
                    path_structure = "pullback"
                    pattern = "downtrend_with_rebound"
                    reason = "negative_path_with_intermediate_rebound"
            else:
                regime = "transitioning"
                path_structure = "complex_reversal"
                pattern = "complex_bottom_reversal"
                reason = "down_up_down_path_with_positive_net_bias"
        elif len(legs) >= 4 and net_return > 0:
            if leg_directions[0] == "down":
                regime = "transitioning"
                path_structure = "complex_reversal"
                pattern = "complex_bottom_reversal"
                reason = "multi_leg_path_shifted_from_down_to_positive"
            else:
                regime = "trending"
                path_structure = "multiple_pullbacks"
                pattern = "uptrend_with_multiple_pullbacks"
                reason = "positive_multi_leg_path"
        elif len(legs) >= 4 and net_return < 0:
            if leg_directions[0] == "up":
                regime = "transitioning"
                path_structure = "complex_reversal"
                pattern = "complex_top_reversal"
                reason = "multi_leg_path_shifted_from_up_to_negative"
            else:
                regime = "trending"
                path_structure = "multiple_pullbacks"
                pattern = "downtrend_with_multiple_rebounds"
                reason = "negative_multi_leg_path"

    confidence = _simple_pattern_confidence(
        regime,
        path_structure,
        net_to_gross,
        gross_return,
        expected_count,
        min_return,
        leg_returns,
        structure_score,
    )

    return {
        "symbol": summary["symbol"],
        "date": summary["date"],
        "lookback_bars": summary["lookback_bars"],
        "regime": regime,
        "directional_bias": directional_bias,
        "path_structure": path_structure,
        "terminal_state": terminal_state,
        "pattern": pattern,
        "pattern_confidence": confidence,
        "classification_reason": reason,
        "direction_sequence": ">".join(compressed),
        "segment_count": expected_count,
        "net_fitted_log_return": net_return,
        "gross_fitted_log_return": gross_return,
        "net_to_gross_ratio": net_to_gross,
        "latest_segment_direction": directions[-1],
        "latest_segment_log_slope": ordered[-1].get("log_slope_per_bar"),
        "latest_segment_fitted_log_return": ordered[-1].get("fitted_log_return"),
        "method": _METHOD,
        "calculation_version": _CALCULATION_VERSION,
    }


def classify_trend_patterns(
    summaries: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Classify every summary using its matching segment rows."""
    grouped: dict[tuple[str, Any, int], list[dict[str, Any]]] = {}
    for segment in segments:
        key = (str(segment["symbol"]), segment["date"], int(segment["lookback_bars"]))
        grouped.setdefault(key, []).append(segment)

    results: list[dict[str, Any]] = []
    for summary in summaries:
        key = (str(summary["symbol"]), summary["date"], int(summary["lookback_bars"]))
        results.append(classify_trend_pattern(summary, grouped.get(key, []), params))
    return results


def summarize_pattern_quality(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return in-memory distribution checks; no quality records are persisted."""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(int(row["lookback_bars"]), []).append(row)

    quality: list[dict[str, Any]] = []
    for lookback_bars, group in sorted(grouped.items()):
        patterns = [str(row["pattern"]) for row in group]
        regimes = [str(row["regime"]) for row in group]
        distribution = Counter(patterns)
        regime_distribution = Counter(regimes)
        total = len(patterns)
        dominant_pattern, dominant_count = distribution.most_common(1)[0]
        irregular_count = distribution.get("irregular_path", 0)
        quality.append(
            {
                "lookback_bars": lookback_bars,
                "total": total,
                "distribution": dict(sorted(distribution.items())),
                "regime_distribution": dict(sorted(regime_distribution.items())),
                "irregular_count": irregular_count,
                "irregular_ratio": irregular_count / total,
                "dominant_pattern": dominant_pattern,
                "dominant_pattern_ratio": dominant_count / total,
            }
        )
    return quality
