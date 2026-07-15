from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

DEFAULT_LOOKBACKS: tuple[int, ...] = (40, 60)
_METHOD = "piecewise_log_linear_dp_bic"
_CALCULATION_VERSION = "adaptive_trend_v1"
_EPSILON = 1e-12


def _linear_fit(values: np.ndarray) -> tuple[float, float, float]:
    x = np.arange(len(values), dtype=float)
    slope, intercept, r_value, _, _ = stats.linregress(x, values)
    residuals = values - (intercept + slope * x)
    rss = float(np.dot(residuals, residuals))
    return float(slope), float(r_value**2), max(rss, 0.0)


def _segment_costs(log_prices: np.ndarray, min_segment_bars: int) -> np.ndarray:
    size = len(log_prices)
    costs = np.full((size + 1, size + 1), np.inf, dtype=float)
    x = np.arange(size, dtype=float)

    def prefix(values: np.ndarray) -> np.ndarray:
        return np.concatenate(([0.0], np.cumsum(values, dtype=float)))

    prefix_x = prefix(x)
    prefix_xx = prefix(x * x)
    prefix_y = prefix(log_prices)
    prefix_yy = prefix(log_prices * log_prices)
    prefix_xy = prefix(x * log_prices)

    for start in range(size):
        for end in range(start + min_segment_bars, size + 1):
            count = end - start
            sum_x = prefix_x[end] - prefix_x[start]
            sum_xx = prefix_xx[end] - prefix_xx[start]
            sum_y = prefix_y[end] - prefix_y[start]
            sum_yy = prefix_yy[end] - prefix_yy[start]
            sum_xy = prefix_xy[end] - prefix_xy[start]
            denominator = count * sum_xx - sum_x * sum_x
            slope = (count * sum_xy - sum_x * sum_y) / denominator
            intercept = (sum_y - slope * sum_x) / count
            rss = sum_yy - intercept * sum_y - slope * sum_xy
            costs[start, end] = max(float(rss), 0.0)
    return costs


def _optimal_boundaries(
    costs: np.ndarray,
    segment_count: int,
    min_segment_bars: int,
) -> tuple[list[int], float] | None:
    """Return exact minimum-RSS boundaries for a fixed number of segments."""
    size = costs.shape[0] - 1
    if segment_count * min_segment_bars > size:
        return None

    dynamic = np.full((segment_count + 1, size + 1), np.inf, dtype=float)
    previous = np.full((segment_count + 1, size + 1), -1, dtype=int)
    dynamic[0, 0] = 0.0

    for count in range(1, segment_count + 1):
        min_end = count * min_segment_bars
        for end in range(min_end, size + 1):
            first_start = (count - 1) * min_segment_bars
            last_start = end - min_segment_bars
            for start in range(first_start, last_start + 1):
                candidate = dynamic[count - 1, start] + costs[start, end]
                if candidate < dynamic[count, end]:
                    dynamic[count, end] = candidate
                    previous[count, end] = start

    rss = float(dynamic[segment_count, size])
    if not np.isfinite(rss):
        return None

    boundaries = [size]
    end = size
    for count in range(segment_count, 0, -1):
        start = int(previous[count, end])
        if start < 0:
            return None
        boundaries.append(start)
        end = start
    boundaries.reverse()
    return boundaries, rss


def _bic(
    observation_count: int,
    rss: float,
    segment_count: int,
    penalty_multiplier: float,
) -> float:
    # Each segment has slope + intercept; each internal boundary adds one degree of freedom.
    parameter_count = 2 * segment_count + (segment_count - 1)
    scaled_rss = max(rss / observation_count, _EPSILON)
    return float(
        observation_count * np.log(scaled_rss)
        + penalty_multiplier * parameter_count * np.log(observation_count)
    )


def _segment_metrics(log_prices: np.ndarray) -> dict[str, float | None]:
    slope, r2, _ = _linear_fit(log_prices)
    log_returns = np.diff(log_prices)
    intervals = len(log_prices) - 1
    actual_return = float(log_prices[-1] - log_prices[0])
    fitted_return = float(slope * intervals)
    total_path = float(np.abs(log_returns).sum())
    efficiency = 0.0 if total_path <= _EPSILON else abs(actual_return) / total_path
    volatility = float(np.std(log_returns, ddof=1)) if len(log_returns) >= 2 else None
    if volatility is not None and volatility <= _EPSILON:
        volatility = None
    vol_adjusted = None
    if volatility is not None:
        vol_adjusted = float(slope * np.sqrt(intervals) / volatility)
    return {
        "log_slope_per_bar": slope,
        "linearity_r2": r2,
        "fitted_log_return": fitted_return,
        "actual_log_return": actual_return,
        "realized_volatility_daily": volatility,
        "vol_adjusted_trend": vol_adjusted,
        "efficiency_ratio": float(np.clip(efficiency, 0.0, 1.0)),
    }


def compute_adaptive_segmentation(
    symbol: str,
    df: pd.DataFrame,
    lookback_bars: int,
    min_segment_bars: int = 5,
    max_segments: int = 4,
    bic_penalty_multiplier: float = 3.0,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Select an exact piecewise log-linear segmentation using BIC."""
    if (
        df.empty
        or "close" not in df
        or lookback_bars < 2
        or min_segment_bars < 2
        or max_segments < 1
        or bic_penalty_multiplier <= 0
        or len(df) < lookback_bars
    ):
        return None, []

    window = df.iloc[-lookback_bars:]
    prices = window["close"].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or np.any(prices <= 0):
        return None, []

    log_prices = np.log(prices)
    costs = _segment_costs(log_prices, min_segment_bars)
    candidates: list[tuple[float, int, list[int], float]] = []
    feasible_max = min(max_segments, lookback_bars // min_segment_bars)
    for segment_count in range(1, feasible_max + 1):
        result = _optimal_boundaries(costs, segment_count, min_segment_bars)
        if result is None:
            continue
        boundaries, rss = result
        candidates.append(
            (
                _bic(lookback_bars, rss, segment_count, bic_penalty_multiplier),
                segment_count,
                boundaries,
                rss,
            )
        )
    if not candidates:
        return None, []

    selected_bic, segment_count, boundaries, selected_rss = min(
        candidates, key=lambda candidate: (candidate[0], candidate[1])
    )
    single_bic, _, _, single_rss = candidates[0]
    latest_date: date = window.index[-1].date()
    summary = {
        "symbol": symbol,
        "date": latest_date,
        "lookback_bars": lookback_bars,
        "observation_count": lookback_bars,
        "segment_count": segment_count,
        "change_point_count": segment_count - 1,
        "selected_rss": selected_rss,
        "single_segment_rss": single_rss,
        "selected_bic": selected_bic,
        "single_segment_bic": single_bic,
        "bic_improvement": float(single_bic - selected_bic),
        "min_segment_bars": min_segment_bars,
        "max_segments": max_segments,
        "bic_penalty_multiplier": bic_penalty_multiplier,
        "method": _METHOD,
        "calculation_version": _CALCULATION_VERSION,
    }

    segments: list[dict[str, Any]] = []
    for segment_index, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:])):
        segment_logs = log_prices[start:end]
        segments.append(
            {
                "symbol": symbol,
                "date": latest_date,
                "lookback_bars": lookback_bars,
                "segment_index": segment_index,
                "start_date": window.index[start].date(),
                "end_date": window.index[end - 1].date(),
                "start_bar_index": start,
                "end_bar_index": end - 1,
                "observation_count": end - start,
                **_segment_metrics(segment_logs),
                "method": _METHOD,
                "calculation_version": _CALCULATION_VERSION,
            }
        )
    return summary, segments


def compute_adaptive_trend_experiment(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute all configured adaptive lookbacks without touching fixed-window results."""
    lookbacks = [int(value) for value in params.get("lookbacks", DEFAULT_LOOKBACKS)]
    min_segment_bars = int(params.get("min_segment_bars", 5))
    max_segments = int(params.get("max_segments", 4))
    bic_penalty_multiplier = float(params.get("bic_penalty_multiplier", 3.0))
    summaries: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for lookback_bars in lookbacks:
        summary, rows = compute_adaptive_segmentation(
            symbol,
            df,
            lookback_bars=lookback_bars,
            min_segment_bars=min_segment_bars,
            max_segments=max_segments,
            bic_penalty_multiplier=bic_penalty_multiplier,
        )
        if summary is not None:
            summaries.append(summary)
            segments.extend(rows)
    return summaries, segments
