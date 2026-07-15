from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

DEFAULT_TREND_WINDOWS: tuple[dict[str, int | str], ...] = (
    {"label": "5d", "far_bars": 5, "near_bars": 0},
    {"label": "10d", "far_bars": 10, "near_bars": 0},
    {"label": "20d", "far_bars": 20, "near_bars": 0},
    {"label": "40d", "far_bars": 40, "near_bars": 0},
    {"label": "60d", "far_bars": 60, "near_bars": 0},
)

_CALCULATION_VERSION = "fixed_trend_v2"
_EPSILON = 1e-12


def _window_close_values(df: pd.DataFrame, far: int, near: int = 0) -> np.ndarray:
    """Return a complete configured close-price window, or an empty array."""
    if far <= near or near < 0 or len(df) < far or "close" not in df:
        return np.array([], dtype=float)
    if near == 0:
        values = df["close"].iloc[-far:].to_numpy(dtype=float)
    else:
        values = df["close"].iloc[-far:-near].to_numpy(dtype=float)
    if len(values) < 2 or not np.isfinite(values).all():
        return np.array([], dtype=float)
    return values


def _log_slope(log_prices: np.ndarray, x: np.ndarray | None = None) -> float | None:
    if len(log_prices) < 2:
        return None
    regressors = np.arange(len(log_prices), dtype=float) if x is None else x
    slope, _, _, _, _ = stats.linregress(regressors, log_prices)
    return float(slope) if np.isfinite(slope) else None


def _jackknife_slope_stability(log_prices: np.ndarray, full_slope: float) -> float | None:
    """Measure whether leave-one-out log slopes retain direction and magnitude."""
    if len(log_prices) < 4 or abs(full_slope) <= _EPSILON:
        return None

    x = np.arange(len(log_prices), dtype=float)
    slopes: list[float] = []
    for index in range(len(log_prices)):
        keep = np.arange(len(log_prices)) != index
        slope = _log_slope(log_prices[keep], x[keep])
        if slope is not None:
            slopes.append(slope)
    if not slopes:
        return None

    slope_values = np.asarray(slopes, dtype=float)
    sign_consistency = float(np.mean(slope_values * full_slope > 0))
    median_slope = float(np.median(slope_values))
    mad = float(np.median(np.abs(slope_values - median_slope)))
    relative_dispersion = mad / max(abs(full_slope), _EPSILON)
    return float(np.clip(sign_consistency * np.exp(-relative_dispersion), 0.0, 1.0))


def _adjacent_slope_stability(
    df: pd.DataFrame,
    far: int,
    near: int,
    current_slope: float,
) -> float | None:
    """Compare the selected window with the equally sized segment immediately before it."""
    window_bars = far - near
    prior_prices = _window_close_values(df, far + window_bars, far)
    if not len(prior_prices) or np.any(prior_prices <= 0):
        return None
    prior_slope = _log_slope(np.log(prior_prices))
    if prior_slope is None:
        return None

    denominator = abs(current_slope) + abs(prior_slope)
    if denominator <= _EPSILON:
        return None
    stability = 1.0 - abs(current_slope - prior_slope) / denominator
    return float(np.clip(stability, 0.0, 1.0))


def compute_fixed_trend_metrics(
    df: pd.DataFrame,
    far: int,
    near: int = 0,
) -> dict[str, int | float | str | None]:
    """Compute fixed-window log trend, volatility, path efficiency, and stability."""
    prices = _window_close_values(df, far, near)
    empty: dict[str, int | float | str | None] = {
        "observation_count": int(len(prices)),
        "log_slope_per_bar": None,
        "linearity_r2": None,
        "fitted_log_return": None,
        "actual_log_return": None,
        "realized_volatility_daily": None,
        "vol_adjusted_trend": None,
        "efficiency_ratio": None,
        "jackknife_slope_stability": None,
        "adjacent_slope_stability": None,
        "calculation_version": _CALCULATION_VERSION,
    }
    if not len(prices) or np.any(prices <= 0):
        return empty

    log_prices = np.log(prices)
    x = np.arange(len(log_prices), dtype=float)
    slope, _, r_value, _, _ = stats.linregress(x, log_prices)
    if not np.isfinite(slope) or not np.isfinite(r_value):
        return empty

    intervals = len(log_prices) - 1
    log_returns = np.diff(log_prices)
    realized_volatility = (
        float(np.std(log_returns, ddof=1)) if len(log_returns) >= 2 else None
    )
    if realized_volatility is not None and realized_volatility <= _EPSILON:
        realized_volatility = None
    total_path = float(np.abs(log_returns).sum())
    actual_log_return = float(log_prices[-1] - log_prices[0])
    efficiency_ratio = 0.0 if total_path <= _EPSILON else abs(actual_log_return) / total_path
    vol_adjusted_trend = None
    if realized_volatility is not None:
        vol_adjusted_trend = float(slope * np.sqrt(intervals) / realized_volatility)

    return {
        "observation_count": len(log_prices),
        "log_slope_per_bar": float(slope),
        "linearity_r2": float(r_value**2),
        "fitted_log_return": float(slope * intervals),
        "actual_log_return": actual_log_return,
        "realized_volatility_daily": realized_volatility,
        "vol_adjusted_trend": vol_adjusted_trend,
        "efficiency_ratio": float(np.clip(efficiency_ratio, 0.0, 1.0)),
        "jackknife_slope_stability": _jackknife_slope_stability(log_prices, float(slope)),
        "adjacent_slope_stability": _adjacent_slope_stability(df, far, near, float(slope)),
        "calculation_version": _CALCULATION_VERSION,
    }


def compute_trend(
    df: pd.DataFrame,
    far: int,
    near: int = 0,
) -> tuple[float | None, float | None]:
    """
    Linear regression on a slice of closing prices.

    near=0 uses the latest `far` bars. near>0 remains supported for explicit
    callers, but the production configuration only uses standard rolling windows.
    """
    y = _window_close_values(df, far, near)
    if not len(y):
        return None, None

    x = np.arange(len(y), dtype=float)
    slope, _, r_value, _, _ = stats.linregress(x, y)

    first_price = y[0]
    if first_price <= 0:
        return None, None

    return round(slope / first_price, 6), round(r_value**2, 4)


def _load_windows(params: dict[str, Any]) -> list[dict[str, int | str]]:
    raw_windows = params.get("windows")
    if not raw_windows:
        return [dict(w) for w in DEFAULT_TREND_WINDOWS]

    windows: list[dict[str, int | str]] = []
    for item in raw_windows:
        label = str(item["label"])
        far_bars = int(item["far_bars"])
        near_bars = int(item.get("near_bars", 0))
        windows.append({"label": label, "far_bars": far_bars, "near_bars": near_bars})
    return windows


def compute_trend_indicators(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compute one trend_daily row per configured trend window."""
    if df.empty:
        return []

    latest_date: date = df.index[-1].date()
    rows: list[dict[str, Any]] = []

    for window in _load_windows(params):
        label = str(window["label"])
        far_bars = int(window["far_bars"])
        near_bars = int(window["near_bars"])
        slope, r2 = compute_trend(df, far_bars, near_bars)
        fixed_metrics = compute_fixed_trend_metrics(df, far_bars, near_bars)
        rows.append(
            {
                "symbol": symbol,
                "date": latest_date,
                "window_label": label,
                "far_bars": far_bars,
                "near_bars": near_bars,
                "slope": slope,
                "r2": r2,
                "method": "linear_regression",
                **fixed_metrics,
            }
        )

    return rows
