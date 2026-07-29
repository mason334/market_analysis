from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from math import isfinite, sqrt
from typing import Any

import numpy as np

TREND_PATTERN_V4_METHOD = "effective_leg_structure_and_close_path_metrics"
TREND_PATTERN_V4_CALCULATION_VERSION = "trend_pattern_v4_1"


@dataclass(frozen=True)
class TrendPatternV4Metrics:
    """Accepted v4 close-path metrics for one lookback window."""

    net_log_return: float | None
    path_efficiency: float | None
    historical_volatility: float | None
    terminal_price_rank: float | None
    terminal_price_position: float | None
    terminal_leg_start_position: float | None
    terminal_breakout_distance_vol: float | None
    squared_movement_time_position: float | None
    squared_movement_concentration: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _null_metrics() -> TrendPatternV4Metrics:
    return TrendPatternV4Metrics(
        net_log_return=None,
        path_efficiency=None,
        historical_volatility=None,
        terminal_price_rank=None,
        terminal_price_position=None,
        terminal_leg_start_position=None,
        terminal_breakout_distance_vol=None,
        squared_movement_time_position=None,
        squared_movement_concentration=None,
    )


def calculate_trend_pattern_v4_metrics(
    close_prices: Sequence[float],
    *,
    terminal_leg_start_index: int | None,
    annualization_basis: int = 252,
) -> TrendPatternV4Metrics:
    """Calculate accepted raw-close v4 metrics without database or structure imports."""
    basis = int(annualization_basis)
    if basis <= 0:
        raise ValueError("annualization_basis must be a positive integer.")

    prices = np.asarray(close_prices, dtype=float)
    observation_count = int(prices.size)
    if observation_count < 3 or not np.isfinite(prices).all() or np.any(prices <= 0.0):
        return _null_metrics()
    if terminal_leg_start_index is not None and not (
        0 <= int(terminal_leg_start_index) < observation_count
    ):
        raise ValueError("terminal_leg_start_index is outside the close-price window.")

    log_prices = np.log(prices)
    log_returns = np.diff(log_prices)
    return_count = int(log_returns.size)
    net_log_return = float(log_prices[-1] - log_prices[0])

    total_variation = float(np.abs(log_returns).sum())
    path_efficiency = (
        0.0 if total_variation == 0.0 else abs(net_log_return) / total_variation
    )

    mean_return = float(log_returns.mean())
    centered_sum_squares = float(np.square(log_returns - mean_return).sum())
    daily_sample_std = sqrt(centered_sum_squares / (return_count - 1))
    historical_volatility = 100.0 * sqrt(basis) * daily_sample_std

    terminal_log_price = float(log_prices[-1])
    prior_log_prices = log_prices[:-1]
    below_count = int(np.count_nonzero(prior_log_prices < terminal_log_price))
    equal_count = int(np.count_nonzero(prior_log_prices == terminal_log_price))
    terminal_price_rank = (below_count + 0.5 * equal_count) / return_count

    minimum_log_price = float(log_prices.min())
    maximum_log_price = float(log_prices.max())
    log_price_range = maximum_log_price - minimum_log_price
    terminal_price_position = (
        0.5
        if log_price_range == 0.0
        else (terminal_log_price - minimum_log_price) / log_price_range
    )
    terminal_leg_start_position = None
    if terminal_leg_start_index is not None and log_price_range > 0.0:
        start_log_price = float(log_prices[int(terminal_leg_start_index)])
        terminal_leg_start_position = (
            start_log_price - minimum_log_price
        ) / log_price_range

    prior_high = float(prior_log_prices.max())
    prior_low = float(prior_log_prices.min())
    up_extension = max(terminal_log_price - prior_high, 0.0)
    down_extension = max(prior_low - terminal_log_price, 0.0)
    signed_extension = up_extension - down_extension
    terminal_breakout_distance_vol = 0.0
    if signed_extension != 0.0:
        terminal_breakout_distance_vol = (
            None if daily_sample_std == 0.0 else signed_extension / daily_sample_std
        )

    quadratic_variation = float(np.square(log_returns).sum())
    if quadratic_variation == 0.0:
        squared_movement_time_position = 0.5
        squared_movement_concentration = 0.0
    else:
        movement_weights = np.square(log_returns) / quadratic_variation
        time_midpoints = (np.arange(1, return_count + 1, dtype=float) - 0.5) / return_count
        squared_movement_time_position = float(
            np.dot(time_midpoints, movement_weights)
        )
        herfindahl_index = float(np.square(movement_weights).sum())
        squared_movement_concentration = 1.0 - 1.0 / (
            return_count * herfindahl_index
        )

    values = TrendPatternV4Metrics(
        net_log_return=net_log_return,
        path_efficiency=float(np.clip(path_efficiency, 0.0, 1.0)),
        historical_volatility=historical_volatility,
        terminal_price_rank=float(np.clip(terminal_price_rank, 0.0, 1.0)),
        terminal_price_position=float(np.clip(terminal_price_position, 0.0, 1.0)),
        terminal_leg_start_position=(
            None
            if terminal_leg_start_position is None
            else float(np.clip(terminal_leg_start_position, 0.0, 1.0))
        ),
        terminal_breakout_distance_vol=terminal_breakout_distance_vol,
        squared_movement_time_position=float(squared_movement_time_position),
        squared_movement_concentration=float(
            np.clip(squared_movement_concentration, 0.0, 1.0 - 1.0 / return_count)
        ),
    )
    if any(value is not None and not isfinite(value) for value in asdict(values).values()):
        return _null_metrics()
    return values
