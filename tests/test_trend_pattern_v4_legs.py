from __future__ import annotations

import pytest

from market_analysis.indicators.trend_pattern_v4_legs import extract_effective_legs


def _segment(
    segment_index: int,
    start_bar_index: int,
    end_bar_index: int,
    fitted_log_return: float,
    *,
    linearity_r2: float = 0.8,
    vol_adjusted_trend: float = 1.2,
) -> dict[str, float | int]:
    return {
        "segment_index": segment_index,
        "start_bar_index": start_bar_index,
        "end_bar_index": end_bar_index,
        "fitted_log_return": fitted_log_return,
        "linearity_r2": linearity_r2,
        "vol_adjusted_trend": vol_adjusted_trend,
    }


def test_effective_leg_builder_removes_flat_and_merges_same_direction() -> None:
    segments = [
        _segment(0, 0, 9, 0.05),
        _segment(1, 10, 19, 0.005),
        _segment(2, 20, 29, 0.04),
        _segment(3, 30, 39, -0.06, linearity_r2=0.2, vol_adjusted_trend=-1.0),
    ]

    legs = extract_effective_legs(segments)

    assert [leg.fitted_log_return for leg in legs] == pytest.approx((0.09, -0.06))
    assert legs[0].start_price_index == 0
    assert legs[0].end_price_index == 29
    assert legs[0].source_segment_indices == (0, 2)
    assert legs[1].start_price_index == 29
    assert legs[1].end_price_index == 39
    assert legs[1].source_segment_indices == (3,)
    assert [leg.direction for leg in legs] == ["up", "down"]


def test_trailing_flat_segment_does_not_change_terminal_leg_start() -> None:
    legs = extract_effective_legs(
        [
            _segment(0, 0, 19, 0.08),
            _segment(1, 20, 39, 0.005),
        ]
    )

    assert len(legs) == 1
    assert legs[0].start_price_index == 0
    assert legs[0].end_price_index == 19


def test_effective_leg_builder_rejects_incomplete_segment_indexes() -> None:
    with pytest.raises(ValueError, match="consecutively indexed"):
        extract_effective_legs([_segment(1, 0, 9, 0.05)])


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("min_abs_fitted_log_return", 0.0),
        ("min_linearity_r2", 1.1),
        ("min_abs_vol_adjusted_trend", float("inf")),
    ],
)
def test_effective_leg_builder_rejects_invalid_thresholds(name: str, value: float) -> None:
    params = {
        "min_abs_fitted_log_return": 0.02,
        "min_linearity_r2": 0.35,
        "min_abs_vol_adjusted_trend": 0.75,
    }
    params[name] = value

    with pytest.raises(ValueError, match="thresholds"):
        extract_effective_legs([_segment(0, 0, 9, 0.05)], **params)
