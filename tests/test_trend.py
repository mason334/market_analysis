from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from market_analysis.indicators.trend import (
    compute_fixed_trend_metrics,
    compute_trend_indicators,
)


def _price_frame(prices: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame(
        {"close": prices.astype(float)},
        index=pd.bdate_range("2026-01-02", periods=len(prices)),
    )


def test_fixed_trend_metrics_for_exponential_path() -> None:
    log_prices = np.log(100.0) + 0.01 * np.arange(40)
    df = _price_frame(np.exp(log_prices))

    result = compute_fixed_trend_metrics(df, far=20)

    assert result["observation_count"] == 20
    assert result["log_slope_per_bar"] == pytest.approx(0.01)
    assert result["linearity_r2"] == pytest.approx(1.0)
    assert result["fitted_log_return"] == pytest.approx(0.19)
    assert result["actual_log_return"] == pytest.approx(0.19)
    assert result["efficiency_ratio"] == pytest.approx(1.0)
    assert result["jackknife_slope_stability"] == pytest.approx(1.0)
    assert result["adjacent_slope_stability"] == pytest.approx(1.0)
    assert result["realized_volatility_daily"] is None
    assert result["vol_adjusted_trend"] is None
    assert result["calculation_version"] == "fixed_trend_v2"


def test_volatility_and_vol_adjusted_trend_use_consistent_horizon() -> None:
    log_returns = np.array([0.01, -0.005, 0.02, 0.0])
    log_prices = np.r_[np.log(100.0), np.log(100.0) + np.cumsum(log_returns)]
    df = _price_frame(np.exp(log_prices))
    expected_slope = stats.linregress(np.arange(5, dtype=float), log_prices).slope
    expected_volatility = np.std(log_returns, ddof=1)

    result = compute_fixed_trend_metrics(df, far=5)

    assert result["realized_volatility_daily"] == pytest.approx(expected_volatility)
    assert result["vol_adjusted_trend"] == pytest.approx(
        expected_slope * np.sqrt(4) / expected_volatility
    )
    assert result["actual_log_return"] == pytest.approx(log_returns.sum())


def test_efficiency_ratio_distinguishes_zigzag_path() -> None:
    log_returns = np.array([0.02, -0.02, 0.02, -0.02])
    log_prices = np.r_[np.log(100.0), np.log(100.0) + np.cumsum(log_returns)]

    result = compute_fixed_trend_metrics(_price_frame(np.exp(log_prices)), far=5)

    assert result["actual_log_return"] == pytest.approx(0.0, abs=1e-12)
    assert result["efficiency_ratio"] == pytest.approx(0.0, abs=1e-12)


def test_adjacent_stability_detects_slope_reversal() -> None:
    prior = np.log(100.0) + 0.01 * np.arange(20)
    current = prior[-1] - 0.01 * np.arange(1, 21)
    df = _price_frame(np.exp(np.r_[prior, current]))

    result = compute_fixed_trend_metrics(df, far=20)

    assert result["log_slope_per_bar"] == pytest.approx(-0.01)
    assert result["adjacent_slope_stability"] == pytest.approx(0.0)


def test_metrics_return_nulls_for_incomplete_or_invalid_window() -> None:
    short = _price_frame(np.linspace(100.0, 110.0, 10))
    incomplete = compute_fixed_trend_metrics(short, far=20)

    assert incomplete["observation_count"] == 0
    assert incomplete["log_slope_per_bar"] is None
    assert incomplete["efficiency_ratio"] is None

    invalid = _price_frame(np.array([100.0, 101.0, 0.0, 102.0, 103.0]))
    invalid_result = compute_fixed_trend_metrics(invalid, far=5)

    assert invalid_result["observation_count"] == 5
    assert invalid_result["log_slope_per_bar"] is None
    assert invalid_result["actual_log_return"] is None


def test_default_indicator_rows_only_use_standard_rolling_windows() -> None:
    rng = np.random.default_rng(42)
    prices = 100.0 * np.exp(np.cumsum(rng.normal(0.001, 0.01, 140)))
    df = _price_frame(prices)

    rows = compute_trend_indicators("TEST", df, params={})

    assert [row["window_label"] for row in rows] == ["5d", "10d", "20d", "40d", "60d"]
    assert all(row["near_bars"] == 0 for row in rows)
    assert all(row["calculation_version"] == "fixed_trend_v2" for row in rows)
    assert all(row["observation_count"] == row["far_bars"] for row in rows)
    assert all("log_slope_per_bar" in row for row in rows)
    assert all("adjacent_slope_stability" in row for row in rows)
