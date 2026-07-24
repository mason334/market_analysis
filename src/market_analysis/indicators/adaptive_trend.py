from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_LOOKBACKS: tuple[int, ...] = (40, 60)
_METHOD = "continuous_piecewise_log_linear_exhaustive_bic"
_CALCULATION_VERSION = "adaptive_trend_v2"
_EPSILON = 1e-12


def _boundary_candidates(
    size: int,
    segment_count: int,
    min_segment_bars: int,
) -> Iterator[tuple[int, ...]]:
    """Yield half-open boundaries whose owned observations meet the minimum size."""
    if segment_count * min_segment_bars > size:
        return
    if segment_count == 1:
        yield (0, size)
        return

    internal_count = segment_count - 1
    possible = range(min_segment_bars, size - min_segment_bars + 1)
    for internal in combinations(possible, internal_count):
        boundaries = (0, *internal, size)
        if all(
            end - start >= min_segment_bars
            for start, end in zip(boundaries[:-1], boundaries[1:])
        ):
            yield boundaries


def _continuous_design(size: int, boundaries: tuple[int, ...]) -> np.ndarray:
    """Build a continuous linear-spline design for half-open segment boundaries.

    An internal boundary ``start`` assigns the incoming ``start - 1 -> start``
    price move to the later segment, so the slope knot is placed at ``start - 1``.
    """
    scale = float(max(size - 1, 1))
    x = np.arange(size, dtype=float) / scale
    columns = [np.ones(size, dtype=float), x]
    columns.extend(
        np.maximum(x - float(boundary - 1) / scale, 0.0)
        for boundary in boundaries[1:-1]
    )
    return np.column_stack(columns)


def _fit_continuous_piecewise(
    log_prices: np.ndarray,
    boundaries: tuple[int, ...],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """Fit one global continuous piecewise-linear OLS model."""
    design = _continuous_design(len(log_prices), boundaries)
    coefficients, _, _, _ = np.linalg.lstsq(design, log_prices, rcond=None)
    fitted = design @ coefficients
    residuals = log_prices - fitted
    rss = max(float(np.dot(residuals, residuals)), 0.0)
    slope_changes = coefficients[2:]
    slopes = coefficients[1] + np.concatenate(
        (np.array([0.0]), np.cumsum(slope_changes, dtype=float))
    )
    slopes /= float(max(len(log_prices) - 1, 1))
    return coefficients, fitted, slopes.astype(float), rss


def _best_boundaries(
    log_prices: np.ndarray,
    segment_count: int,
    min_segment_bars: int,
) -> tuple[tuple[int, ...], np.ndarray, np.ndarray, float] | None:
    """Return the exact minimum-RSS continuous fit for a fixed segment count."""
    candidates = list(
        _boundary_candidates(len(log_prices), segment_count, min_segment_bars)
    )
    if not candidates:
        return None
    if segment_count == 1:
        boundaries = candidates[0]
        _, fitted, slopes, rss = _fit_continuous_piecewise(log_prices, boundaries)
        return boundaries, fitted, slopes, rss

    size = len(log_prices)
    scale = float(max(size - 1, 1))
    x = np.arange(size, dtype=float) / scale
    base = np.column_stack((np.ones(size, dtype=float), x))
    knots = (np.asarray([row[1:-1] for row in candidates], dtype=float) - 1.0) / scale
    hinges = np.maximum(x[None, :, None] - knots[:, None, :], 0.0)
    repeated_base = np.broadcast_to(base, (len(candidates), size, 2))
    designs = np.concatenate((repeated_base, hinges), axis=2)
    gram = np.einsum("cnp,cnq->cpq", designs, designs, optimize=True)
    rhs = np.einsum("cnp,n->cp", designs, log_prices, optimize=True)
    coefficients = np.linalg.solve(gram, rhs[..., None])[..., 0]
    rss_values = np.maximum(
        float(np.dot(log_prices, log_prices))
        - np.einsum("cp,cp->c", coefficients, rhs, optimize=True),
        0.0,
    )
    best_index = int(np.argmin(rss_values))
    boundaries = candidates[best_index]
    _, fitted, slopes, rss = _fit_continuous_piecewise(log_prices, boundaries)
    return boundaries, fitted, slopes, rss


def _bic(
    observation_count: int,
    rss: float,
    segment_count: int,
    penalty_multiplier: float,
) -> float:
    # A continuous K-segment spline has K+1 coefficients and K-1 knot locations.
    parameter_count = 2 * segment_count
    scaled_rss = max(rss / observation_count, _EPSILON)
    return float(
        observation_count * np.log(scaled_rss)
        + penalty_multiplier * parameter_count * np.log(observation_count)
    )


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
    start: int,
    end: int,
    window: pd.DataFrame,
) -> dict[str, Any]:
    # A later half-open segment owns its incoming boundary return.
    anchor = start if start == 0 else start - 1
    actual_segment = log_prices[anchor:end]
    fitted_segment = fitted[anchor:end]
    log_returns = np.diff(actual_segment)
    intervals = len(log_returns)
    actual_return = float(actual_segment[-1] - actual_segment[0])
    fitted_return = float(fitted_segment[-1] - fitted_segment[0])
    total_path = float(np.abs(log_returns).sum())
    efficiency = 0.0 if total_path <= _EPSILON else abs(actual_return) / total_path
    volatility = float(np.std(log_returns, ddof=1)) if len(log_returns) >= 2 else None
    if volatility is not None and volatility <= _EPSILON:
        volatility = None
    vol_adjusted = None
    if volatility is not None and intervals > 0:
        vol_adjusted = float(slope * np.sqrt(intervals) / volatility)

    largest_move = None
    largest_move_date = None
    largest_move_bar_index = None
    largest_move_path_share = None
    if log_returns.size:
        move_offset = int(np.argmax(np.abs(log_returns)))
        largest_move = float(log_returns[move_offset])
        largest_move_bar_index = anchor + move_offset + 1
        largest_move_date = window.index[largest_move_bar_index].date()
        largest_move_path_share = (
            0.0 if total_path <= _EPSILON else abs(largest_move) / total_path
        )

    return {
        "log_slope_per_bar": float(slope),
        "linearity_r2": _linearity_r2(actual_segment, fitted_segment),
        "fitted_log_return": fitted_return,
        "actual_log_return": actual_return,
        "realized_volatility_daily": volatility,
        "vol_adjusted_trend": vol_adjusted,
        "efficiency_ratio": float(np.clip(efficiency, 0.0, 1.0)),
        "largest_move_log_return": largest_move,
        "largest_move_date": largest_move_date,
        "largest_move_bar_index": largest_move_bar_index,
        "largest_move_path_share": largest_move_path_share,
    }


def compute_adaptive_segmentation(
    symbol: str,
    df: pd.DataFrame,
    lookback_bars: int,
    min_segment_bars: int = 5,
    max_segments: int = 4,
    bic_penalty_multiplier: float = 3.0,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Select an exact continuous piecewise log-linear OLS fit using BIC."""
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
    candidates: list[
        tuple[float, int, tuple[int, ...], np.ndarray, np.ndarray, float]
    ] = []
    feasible_max = min(max_segments, lookback_bars // min_segment_bars)
    for segment_count in range(1, feasible_max + 1):
        result = _best_boundaries(log_prices, segment_count, min_segment_bars)
        if result is None:
            continue
        boundaries, fitted, slopes, rss = result
        candidates.append(
            (
                _bic(lookback_bars, rss, segment_count, bic_penalty_multiplier),
                segment_count,
                boundaries,
                fitted,
                slopes,
                rss,
            )
        )
    if not candidates:
        return None, []

    selected_bic, segment_count, boundaries, fitted, slopes, selected_rss = min(
        candidates, key=lambda candidate: (candidate[0], candidate[1], candidate[2])
    )
    single_bic, _, _, _, _, single_rss = candidates[0]
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
                **_segment_metrics(
                    log_prices,
                    fitted,
                    float(slopes[segment_index]),
                    start,
                    end,
                    window,
                ),
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


def reconstruct_adaptive_fit(
    df: pd.DataFrame,
    lookback_bars: int,
    segments: list[dict[str, Any]],
) -> pd.DataFrame:
    """Reconstruct fitted values for diagnostics without changing persisted rows."""
    if not segments or len(df) < lookback_bars or "close" not in df:
        return pd.DataFrame()

    window = df.iloc[-lookback_bars:]
    log_prices = np.log(window["close"].to_numpy(dtype=float))
    ordered = sorted(segments, key=lambda row: int(row["segment_index"]))
    boundaries = (0, *(int(row["start_bar_index"]) for row in ordered[1:]), lookback_bars)
    _, fitted, _, _ = _fit_continuous_piecewise(log_prices, boundaries)
    return pd.DataFrame(
        {
            "close": np.exp(log_prices),
            "log_close": log_prices,
            "fitted_close": np.exp(fitted),
            "fitted_log_close": fitted,
            "residual_log": log_prices - fitted,
        },
        index=window.index,
    )
