from __future__ import annotations

from math import sqrt

import numpy as np
import pytest

from market_analysis.indicators.trend_pattern_v4_metrics import (
    calculate_trend_pattern_v4_metrics,
)


def test_metrics_match_hand_calculated_breakout_path() -> None:
    closes = np.exp([0.0, 0.01, 0.0, 0.03])

    result = calculate_trend_pattern_v4_metrics(
        closes,
        terminal_leg_start_index=2,
    )

    assert result.net_log_return == pytest.approx(0.03)
    assert result.path_efficiency == pytest.approx(0.6)
    assert result.historical_volatility == pytest.approx(100 * sqrt(252) * 0.02)
    assert result.terminal_price_rank == pytest.approx(1.0)
    assert result.terminal_price_position == pytest.approx(1.0)
    assert result.terminal_leg_start_position == pytest.approx(0.0)
    assert result.terminal_breakout_distance_vol == pytest.approx(1.0)

    weights = np.array([0.0001, 0.0001, 0.0009]) / 0.0011
    midpoints = np.array([1 / 6, 0.5, 5 / 6])
    assert result.squared_movement_time_position == pytest.approx(
        float(np.dot(weights, midpoints))
    )
    assert result.squared_movement_concentration == pytest.approx(
        1.0 - 1.0 / (3.0 * float(np.square(weights).sum()))
    )


def test_terminal_leg_start_position_and_derived_range_share() -> None:
    closes = np.exp([0.0, 0.04, 0.02, 0.08])

    result = calculate_trend_pattern_v4_metrics(
        closes,
        terminal_leg_start_index=2,
    )

    assert result.terminal_price_position == pytest.approx(1.0)
    assert result.terminal_leg_start_position == pytest.approx(0.25)
    assert abs(
        result.terminal_price_position - result.terminal_leg_start_position
    ) == pytest.approx(0.75)


def test_flat_path_uses_defined_boundaries() -> None:
    result = calculate_trend_pattern_v4_metrics(
        [100.0, 100.0, 100.0],
        terminal_leg_start_index=None,
    )

    assert result.net_log_return == 0.0
    assert result.path_efficiency == 0.0
    assert result.historical_volatility == 0.0
    assert result.terminal_price_rank == 0.5
    assert result.terminal_price_position == 0.5
    assert result.terminal_leg_start_position is None
    assert result.terminal_breakout_distance_vol == 0.0
    assert result.squared_movement_time_position == 0.5
    assert result.squared_movement_concentration == 0.0


@pytest.mark.parametrize("observation_count", [40, 60])
def test_observation_count_maps_to_one_fewer_return_intervals(
    observation_count: int,
) -> None:
    log_prices = np.full(observation_count, 0.1)
    log_prices[0] = 0.0

    result = calculate_trend_pattern_v4_metrics(
        np.exp(log_prices),
        terminal_leg_start_index=0,
    )

    return_count = observation_count - 1
    assert result.squared_movement_time_position == pytest.approx(1 / (2 * return_count))
    assert result.squared_movement_concentration == pytest.approx(1 - 1 / return_count)


@pytest.mark.parametrize(
    "closes",
    [
        [100.0, 101.0],
        [100.0, 0.0, 101.0],
        [100.0, float("nan"), 101.0],
    ],
)
def test_invalid_close_windows_return_null_metrics(closes: list[float]) -> None:
    result = calculate_trend_pattern_v4_metrics(
        closes,
        terminal_leg_start_index=None,
    )

    assert all(value is None for value in result.to_dict().values())


def test_terminal_leg_index_must_be_inside_window() -> None:
    with pytest.raises(ValueError, match="outside"):
        calculate_trend_pattern_v4_metrics(
            [100.0, 101.0, 102.0],
            terminal_leg_start_index=3,
        )
