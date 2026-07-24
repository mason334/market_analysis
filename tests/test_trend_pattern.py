from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from market_analysis.indicators.trend_pattern import (
    classify_trend_pattern,
    classify_trend_patterns,
    summarize_pattern_quality,
)


def _summary(count: int, lookback: int = 40) -> dict[str, Any]:
    return {
        "symbol": "TEST",
        "date": date(2026, 7, 15),
        "lookback_bars": lookback,
        "segment_count": count,
    }


def _segment(index: int, fitted_return: float) -> dict[str, Any]:
    return {
        "symbol": "TEST",
        "date": date(2026, 7, 15),
        "lookback_bars": 40,
        "segment_index": index,
        "fitted_log_return": fitted_return,
        "log_slope_per_bar": fitted_return / 9,
        "linearity_r2": 0.8,
        "vol_adjusted_trend": 1.5 if fitted_return >= 0 else -1.5,
    }


@pytest.mark.parametrize(
    ("returns", "expected"),
    [
        ([0.10], ("trending", "up", "single_leg", "up", "uptrend")),
        ([-0.10], ("trending", "down", "single_leg", "down", "downtrend")),
        ([0.005], ("ranging", "neutral", "flat", "flat", "range_bound")),
        (
            [-0.10, 0.08],
            ("transitioning", "neutral", "reversal", "up", "bottom_reversal"),
        ),
        (
            [0.10, -0.08],
            ("transitioning", "neutral", "reversal", "down", "top_reversal"),
        ),
        (
            [-0.10, 0.08, 0.005],
            (
                "transitioning",
                "neutral",
                "reversal",
                "flat",
                "bottom_reversal_then_sideways",
            ),
        ),
        (
            [0.10, -0.03, 0.08],
            ("trending", "up", "resumption", "up", "uptrend_resumption"),
        ),
        (
            [0.10, -0.05, 0.03],
            ("trending", "up", "pullback", "up", "uptrend_with_pullback"),
        ),
        (
            [-0.10, 0.03, -0.08],
            ("trending", "down", "resumption", "down", "downtrend_resumption"),
        ),
        (
            [0.08, -0.075, 0.06],
            ("transitioning", "neutral", "double_test", "up", "double_bottom_like"),
        ),
        (
            [-0.08, 0.075, -0.06],
            ("transitioning", "neutral", "double_test", "down", "double_top_like"),
        ),
        (
            [0.10, -0.07, 0.04],
            ("ranging", "neutral", "contracting", "up", "contracting_range"),
        ),
        (
            [0.04, -0.08, 0.12],
            ("ranging", "neutral", "expanding", "up", "expanding_range"),
        ),
        (
            [0.08, -0.08, 0.02],
            ("ranging", "neutral", "stable_range", "up", "range_bound"),
        ),
        (
            [0.03, -0.15, 0.04],
            (
                "transitioning",
                "down",
                "complex_reversal",
                "up",
                "complex_top_reversal",
            ),
        ),
        (
            [0.12, -0.03, 0.08, -0.02],
            (
                "trending",
                "up",
                "multiple_pullbacks",
                "down",
                "uptrend_with_multiple_pullbacks",
            ),
        ),
    ],
)
def test_classifies_layered_long_term_pattern(
    returns: list[float], expected: tuple[str, str, str, str, str]
) -> None:
    segments = [_segment(index, value) for index, value in enumerate(returns)]

    result = classify_trend_pattern(_summary(len(segments)), segments, {})

    assert (
        result["regime"],
        result["directional_bias"],
        result["path_structure"],
        result["terminal_state"],
        result["pattern"],
    ) == expected
    assert 0.0 <= result["pattern_confidence"] <= 1.0
    assert result["calculation_version"] == "trend_pattern_v3"


def test_pattern_confidence_uses_simple_structure_specific_rules() -> None:
    reversal = classify_trend_pattern(
        _summary(2), [_segment(0, -0.10), _segment(1, 0.08)], {}
    )
    trend = classify_trend_pattern(
        _summary(3),
        [_segment(0, 0.10), _segment(1, -0.03), _segment(2, 0.08)],
        {},
    )
    range_result = classify_trend_pattern(
        _summary(3),
        [_segment(0, 0.08), _segment(1, -0.08), _segment(2, 0.02)],
        {},
    )

    assert reversal["pattern_confidence"] == pytest.approx(0.8)
    assert trend["pattern_confidence"] == pytest.approx(0.15 / 0.21)
    assert range_result["pattern_confidence"] == pytest.approx(1.0 - 0.02 / 0.18)


def test_flat_between_same_directions_is_removed_only_from_effective_legs() -> None:
    segments = [_segment(0, 0.05), _segment(1, 0.005), _segment(2, 0.04)]

    result = classify_trend_pattern(_summary(3), segments, {})

    assert result["direction_sequence"] == "up>flat>up"
    assert result["pattern"] == "uptrend"
    assert result["terminal_state"] == "up"


def test_rejects_incomplete_segment_sequence() -> None:
    with pytest.raises(ValueError, match="complete and consecutively indexed"):
        classify_trend_pattern(_summary(2), [_segment(1, 0.1)], {})


def test_batch_matches_summary_to_lookback() -> None:
    summary_40 = _summary(1, 40)
    summary_60 = _summary(1, 60)
    segment_40 = _segment(0, 0.1)
    segment_60 = {**_segment(0, -0.1), "lookback_bars": 60}

    results = classify_trend_patterns(
        [summary_40, summary_60], [segment_40, segment_60], {}
    )

    assert [(row["lookback_bars"], row["pattern"]) for row in results] == [
        (40, "uptrend"),
        (60, "downtrend"),
    ]


def test_quality_summary_reports_pattern_regime_and_irregular_distributions() -> None:
    rows = [
        {"lookback_bars": 40, "pattern": "uptrend", "regime": "trending"},
        {"lookback_bars": 40, "pattern": "irregular_path", "regime": "irregular"},
        {"lookback_bars": 40, "pattern": "uptrend", "regime": "trending"},
    ]

    quality = summarize_pattern_quality(rows)

    assert quality == [
        {
            "lookback_bars": 40,
            "total": 3,
            "distribution": {"irregular_path": 1, "uptrend": 2},
            "regime_distribution": {"irregular": 1, "trending": 2},
            "irregular_count": 1,
            "irregular_ratio": pytest.approx(1 / 3),
            "dominant_pattern": "uptrend",
            "dominant_pattern_ratio": pytest.approx(2 / 3),
        }
    ]
