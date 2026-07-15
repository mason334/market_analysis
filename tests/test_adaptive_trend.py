from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from market_analysis.indicators.adaptive_trend import (
    compute_adaptive_segmentation,
    compute_adaptive_trend_experiment,
)


def _frame(log_prices: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        {"close": np.exp(log_prices)},
        index=pd.bdate_range("2026-01-02", periods=len(log_prices)),
    )


def test_single_linear_path_is_not_oversegmented() -> None:
    df = _frame(4.0 + 0.01 * np.arange(60))

    summary, segments = compute_adaptive_segmentation("TEST", df, 60)

    assert summary is not None
    assert summary["segment_count"] == 1
    assert summary["change_point_count"] == 0
    assert len(segments) == 1
    assert segments[0]["log_slope_per_bar"] == pytest.approx(0.01)
    assert segments[0]["linearity_r2"] == pytest.approx(1.0)


def test_v_shape_finds_unconfigured_turning_point() -> None:
    down = 4.5 - 0.02 * np.arange(23)
    up = down[-1] + 0.03 * np.arange(1, 18)
    df = _frame(np.concatenate((down, up)))

    summary, segments = compute_adaptive_segmentation("TEST", df, 40)

    assert summary is not None
    assert summary["segment_count"] == 2
    assert len(segments) == 2
    assert 20 <= segments[0]["end_bar_index"] <= 23
    assert segments[0]["log_slope_per_bar"] < 0
    assert segments[1]["log_slope_per_bar"] > 0
    assert summary["bic_improvement"] > 0


def test_minimum_segment_length_is_enforced() -> None:
    log_prices = np.concatenate(
        (
            4.0 + 0.01 * np.arange(18),
            np.array([4.8, 4.9]),
            4.2 + 0.01 * np.arange(20),
        )
    )
    df = _frame(log_prices)

    summary, segments = compute_adaptive_segmentation(
        "TEST", df, 40, min_segment_bars=6, max_segments=4
    )

    assert summary is not None
    assert all(segment["observation_count"] >= 6 for segment in segments)


def test_experiment_supports_multiple_lookbacks() -> None:
    df = _frame(4.0 + 0.005 * np.arange(80))

    summaries, segments = compute_adaptive_trend_experiment(
        "TEST",
        df,
        {"lookbacks": [40, 60], "min_segment_bars": 5, "max_segments": 3},
    )

    assert [row["lookback_bars"] for row in summaries] == [40, 60]
    assert {row["lookback_bars"] for row in segments} == {40, 60}


@pytest.mark.parametrize(
    "frame,lookback",
    [
        (_frame(np.arange(10, dtype=float)), 20),
        (pd.DataFrame({"close": [1.0, 0.0, 2.0]}), 3),
        (pd.DataFrame({"open": [1.0, 2.0, 3.0]}), 3),
    ],
)
def test_invalid_inputs_return_no_segmentation(frame: pd.DataFrame, lookback: int) -> None:
    summary, segments = compute_adaptive_segmentation("TEST", frame, lookback)

    assert summary is None
    assert segments == []
