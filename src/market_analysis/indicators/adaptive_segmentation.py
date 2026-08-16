from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import date
from itertools import combinations
from math import comb
from typing import Any

import numpy as np
import pandas as pd

DEFAULT_LOOKBACKS: tuple[int, ...] = (250,)
_METHOD = "continuous_piecewise_log_linear_deterministic_hybrid_bic"
_CALCULATION_VERSION = "adaptive_segmentation_v3"
_EPSILON = 1e-12


@dataclass(frozen=True)
class SearchConfig:
    exact_candidate_budget: int = 100_000
    exhaustive_batch_size: int = 2_048
    beam_width: int = 8
    deterministic_seed_count: int = 4
    max_refinement_passes: int = 10
    rss_improvement_tolerance: float = 1e-10
    pair_refinement_enabled: bool = True
    pair_refinement_radius: int = 5


@dataclass(frozen=True)
class _SearchOutcome:
    boundaries: tuple[int, ...]
    fitted: np.ndarray
    slopes: np.ndarray
    rss: float
    search_mode: str
    candidates_evaluated: int
    converged: bool
    refinement_passes: int
    top_boundaries: tuple[tuple[int, ...], ...]


def recommended_max_segments(lookback_bars: int, max_segments_cap: int = 10) -> int:
    """Return the capped dashboard/production recommendation for a lookback."""
    if lookback_bars < 2 or max_segments_cap < 1:
        raise ValueError("lookback_bars and max_segments_cap are outside valid ranges.")
    uncapped = 4 if lookback_bars < 40 else 4 + (lookback_bars - 40) // 20
    return min(max_segments_cap, uncapped)


def candidate_count(size: int, segment_count: int, min_segment_bars: int) -> int:
    """Count legal half-open segmentations without enumerating their boundaries."""
    if size < 1 or segment_count < 1 or min_segment_bars < 1:
        return 0
    if segment_count * min_segment_bars > size:
        return 0
    return comb(size - segment_count * min_segment_bars + segment_count - 1, segment_count - 1)


def _normalize_search_config(params: dict[str, Any] | None) -> SearchConfig:
    values = dict(params or {})
    config = SearchConfig(
        exact_candidate_budget=int(values.get("exact_candidate_budget", 100_000)),
        exhaustive_batch_size=int(values.get("exhaustive_batch_size", 2_048)),
        beam_width=int(values.get("beam_width", 8)),
        deterministic_seed_count=int(values.get("deterministic_seed_count", 4)),
        max_refinement_passes=int(values.get("max_refinement_passes", 10)),
        rss_improvement_tolerance=float(values.get("rss_improvement_tolerance", 1e-10)),
        pair_refinement_enabled=bool(values.get("pair_refinement_enabled", True)),
        pair_refinement_radius=int(values.get("pair_refinement_radius", 5)),
    )
    if (
        config.exact_candidate_budget < 1
        or config.exhaustive_batch_size < 1
        or config.beam_width < 1
        or config.deterministic_seed_count < 1
        or config.max_refinement_passes < 1
        or config.rss_improvement_tolerance < 0
        or config.pair_refinement_radius < 1
    ):
        raise ValueError("Adaptive trend search parameters are outside valid ranges.")
    return config


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
        if _valid_boundaries(boundaries, min_segment_bars):
            yield boundaries


def _valid_boundaries(boundaries: tuple[int, ...], min_segment_bars: int) -> bool:
    return all(
        end - start >= min_segment_bars
        for start, end in zip(boundaries[:-1], boundaries[1:])
    )


def _continuous_design(size: int, boundaries: tuple[int, ...]) -> np.ndarray:
    """Build a continuous linear-spline design for half-open segment boundaries."""
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


def _batched_rss(
    log_prices: np.ndarray,
    candidates: list[tuple[int, ...]],
) -> np.ndarray:
    if not candidates:
        return np.array([], dtype=float)
    if len(candidates[0]) == 2:
        return np.array([_fit_continuous_piecewise(log_prices, candidates[0])[3]])

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
    try:
        coefficients = np.linalg.solve(gram, rhs[..., None])[..., 0]
    except np.linalg.LinAlgError:
        return np.array(
            [_fit_continuous_piecewise(log_prices, boundaries)[3] for boundaries in candidates]
        )
    return np.maximum(
        float(np.dot(log_prices, log_prices))
        - np.einsum("cp,cp->c", coefficients, rhs, optimize=True),
        0.0,
    )


def _rank_boundary_pool(
    log_prices: np.ndarray,
    candidates: Iterable[tuple[int, ...]],
    batch_size: int,
    limit: int,
) -> tuple[list[tuple[float, tuple[int, ...]]], int]:
    unique = sorted(set(candidates))
    ranked: list[tuple[float, tuple[int, ...]]] = []
    evaluated = 0
    for start in range(0, len(unique), batch_size):
        batch = unique[start : start + batch_size]
        rss_values = _batched_rss(log_prices, batch)
        evaluated += len(batch)
        ranked.extend((float(rss), boundaries) for rss, boundaries in zip(rss_values, batch))
        ranked = sorted(ranked, key=lambda item: (item[0], item[1]))[:limit]
    return ranked, evaluated


def _exact_search(
    log_prices: np.ndarray,
    segment_count: int,
    min_segment_bars: int,
    config: SearchConfig,
    top_n: int,
) -> _SearchOutcome | None:
    ranked, evaluated = _rank_boundary_pool(
        log_prices,
        _boundary_candidates(len(log_prices), segment_count, min_segment_bars),
        config.exhaustive_batch_size,
        top_n,
    )
    if not ranked:
        return None
    boundaries = ranked[0][1]
    _, fitted, slopes, rss = _fit_continuous_piecewise(log_prices, boundaries)
    return _SearchOutcome(
        boundaries=boundaries,
        fitted=fitted,
        slopes=slopes,
        rss=rss,
        search_mode="exact",
        candidates_evaluated=evaluated,
        converged=True,
        refinement_passes=0,
        top_boundaries=tuple(item[1] for item in ranked),
    )


def _best_boundaries(
    log_prices: np.ndarray,
    segment_count: int,
    min_segment_bars: int,
) -> tuple[tuple[int, ...], np.ndarray, np.ndarray, float] | None:
    """Return the exact minimum-RSS fit using bounded-memory batches."""
    outcome = _exact_search(
        log_prices,
        segment_count,
        min_segment_bars,
        SearchConfig(),
        top_n=1,
    )
    if outcome is None:
        return None
    return outcome.boundaries, outcome.fitted, outcome.slopes, outcome.rss


def _expanded_boundaries(
    previous: Iterable[tuple[int, ...]],
    size: int,
    min_segment_bars: int,
) -> Iterator[tuple[int, ...]]:
    for boundaries in previous:
        existing = boundaries[1:-1]
        for position in range(min_segment_bars, size - min_segment_bars + 1):
            if position in existing:
                continue
            candidate = (0, *sorted((*existing, position)), size)
            if _valid_boundaries(candidate, min_segment_bars):
                yield candidate


def _deterministic_seeds(
    size: int,
    segment_count: int,
    min_segment_bars: int,
    seed_count: int,
) -> tuple[tuple[int, ...], ...]:
    if segment_count == 1:
        return ((0, size),)
    base_internal = tuple(round(index * size / segment_count) for index in range(1, segment_count))
    delta = max(1, min_segment_bars // 2)
    variants = [
        base_internal,
        tuple(value - delta for value in base_internal),
        tuple(value + delta for value in base_internal),
        tuple(
            value + (delta if index % 2 == 0 else -delta)
            for index, value in enumerate(base_internal)
        ),
    ]
    seeds: list[tuple[int, ...]] = []
    for internal in variants:
        candidate = (0, *internal, size)
        if _valid_boundaries(candidate, min_segment_bars) and candidate not in seeds:
            seeds.append(candidate)
        if len(seeds) >= seed_count:
            break
    if not seeds:
        first = next(_boundary_candidates(size, segment_count, min_segment_bars), None)
        if first is not None:
            seeds.append(first)
    return tuple(seeds)


def _strictly_improves(new_rss: float, old_rss: float, tolerance: float) -> bool:
    return new_rss < old_rss - tolerance * max(1.0, old_rss)


def _single_boundary_refinement(
    log_prices: np.ndarray,
    initial: tuple[int, ...],
    min_segment_bars: int,
    config: SearchConfig,
) -> tuple[tuple[int, ...], float, bool, int, int]:
    current = initial
    current_rss = _fit_continuous_piecewise(log_prices, current)[3]
    evaluated = 1
    passes = 0
    for pass_index in range(1, config.max_refinement_passes + 1):
        changed = False
        for boundary_index in range(1, len(current) - 1):
            candidates = []
            for position in range(
                current[boundary_index - 1] + min_segment_bars,
                current[boundary_index + 1] - min_segment_bars + 1,
            ):
                if position == current[boundary_index]:
                    continue
                candidate = (*current[:boundary_index], position, *current[boundary_index + 1 :])
                candidates.append(candidate)
            ranked, count = _rank_boundary_pool(
                log_prices,
                candidates,
                config.exhaustive_batch_size,
                1,
            )
            evaluated += count
            if ranked and _strictly_improves(
                ranked[0][0], current_rss, config.rss_improvement_tolerance
            ):
                current_rss, current = ranked[0]
                changed = True
        passes = pass_index
        if not changed:
            return current, current_rss, True, evaluated, passes
    return current, current_rss, False, evaluated, passes


def _pair_refinement(
    log_prices: np.ndarray,
    initial: tuple[int, ...],
    min_segment_bars: int,
    config: SearchConfig,
) -> tuple[tuple[int, ...], float, bool, int]:
    current = initial
    current_rss = _fit_continuous_piecewise(log_prices, current)[3]
    evaluated = 1
    moved = False
    radius = config.pair_refinement_radius
    for left_index in range(1, len(current) - 2):
        candidates = []
        left_current = current[left_index]
        right_current = current[left_index + 1]
        for left in range(left_current - radius, left_current + radius + 1):
            for right in range(right_current - radius, right_current + radius + 1):
                candidate = (
                    *current[:left_index],
                    left,
                    right,
                    *current[left_index + 2 :],
                )
                if candidate == current or not _valid_boundaries(candidate, min_segment_bars):
                    continue
                candidates.append(candidate)
        ranked, count = _rank_boundary_pool(
            log_prices,
            candidates,
            config.exhaustive_batch_size,
            1,
        )
        evaluated += count
        if ranked and _strictly_improves(
            ranked[0][0], current_rss, config.rss_improvement_tolerance
        ):
            current_rss, current = ranked[0]
            moved = True
    return current, current_rss, moved, evaluated


def _hybrid_search(
    log_prices: np.ndarray,
    segment_count: int,
    min_segment_bars: int,
    previous_beam: tuple[tuple[int, ...], ...],
    config: SearchConfig,
) -> _SearchOutcome | None:
    size = len(log_prices)
    expanded, evaluated = _rank_boundary_pool(
        log_prices,
        _expanded_boundaries(previous_beam, size, min_segment_bars),
        config.exhaustive_batch_size,
        config.beam_width,
    )
    initial = [item[1] for item in expanded]
    initial.extend(
        _deterministic_seeds(
            size,
            segment_count,
            min_segment_bars,
            config.deterministic_seed_count,
        )
    )
    initial = list(dict.fromkeys(initial))
    if not initial:
        return None

    refined: list[tuple[float, tuple[int, ...], bool, int]] = []
    total_passes = 0
    for seed in initial:
        boundaries, rss, converged, count, passes = _single_boundary_refinement(
            log_prices,
            seed,
            min_segment_bars,
            config,
        )
        evaluated += count
        total_passes += passes
        if config.pair_refinement_enabled:
            boundaries, rss, moved, pair_count = _pair_refinement(
                log_prices,
                boundaries,
                min_segment_bars,
                config,
            )
            evaluated += pair_count
            if moved:
                boundaries, rss, post_converged, post_count, post_passes = (
                    _single_boundary_refinement(
                        log_prices,
                        boundaries,
                        min_segment_bars,
                        config,
                    )
                )
                evaluated += post_count
                total_passes += post_passes
                converged = converged and post_converged
        refined.append((rss, boundaries, converged, passes))

    ranked = sorted(refined, key=lambda item: (item[0], item[1]))
    best_rss, best_boundaries, _, _ = ranked[0]
    _, fitted, slopes, rss = _fit_continuous_piecewise(log_prices, best_boundaries)
    return _SearchOutcome(
        boundaries=best_boundaries,
        fitted=fitted,
        slopes=slopes,
        rss=rss,
        search_mode="hybrid_approximate",
        candidates_evaluated=evaluated,
        converged=all(item[2] for item in ranked),
        refinement_passes=total_passes,
        top_boundaries=tuple(item[1] for item in ranked[: config.beam_width]),
    )


def _bic(
    observation_count: int,
    rss: float,
    segment_count: int,
    penalty_multiplier: float,
) -> float:
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
        "fitted_anchor_log_price": float(fitted[anchor]),
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
    search_params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Select a deterministic exact-or-hybrid continuous piecewise fit using BIC."""
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
    try:
        search_config = _normalize_search_config(search_params)
    except ValueError:
        return None, []

    window = df.iloc[-lookback_bars:]
    prices = window["close"].to_numpy(dtype=float)
    if not np.isfinite(prices).all() or np.any(prices <= 0):
        return None, []

    log_prices = np.log(prices)
    feasible_max = min(max_segments, lookback_bars // min_segment_bars)
    candidates: list[tuple[float, int, _SearchOutcome]] = []
    diagnostics: list[dict[str, Any]] = []
    previous_beam: tuple[tuple[int, ...], ...] = ()
    for segment_count in range(1, feasible_max + 1):
        space = candidate_count(lookback_bars, segment_count, min_segment_bars)
        if space <= search_config.exact_candidate_budget:
            outcome = _exact_search(
                log_prices,
                segment_count,
                min_segment_bars,
                search_config,
                top_n=search_config.beam_width,
            )
        else:
            outcome = _hybrid_search(
                log_prices,
                segment_count,
                min_segment_bars,
                previous_beam,
                search_config,
            )
        if outcome is None:
            continue
        previous_beam = outcome.top_boundaries
        bic = _bic(lookback_bars, outcome.rss, segment_count, bic_penalty_multiplier)
        candidates.append((bic, segment_count, outcome))
        diagnostics.append(
            {
                "segment_count": segment_count,
                "candidate_space": space,
                "candidates_evaluated": outcome.candidates_evaluated,
                "search_mode": outcome.search_mode,
                "converged": outcome.converged,
                "refinement_passes": outcome.refinement_passes,
                "rss": outcome.rss,
                "bic": bic,
                "boundaries": list(outcome.boundaries),
            }
        )
    if not candidates:
        return None, []

    selected_bic, segment_count, selected = min(
        candidates,
        key=lambda candidate: (candidate[0], candidate[1], candidate[2].boundaries),
    )
    single_bic, _, single = candidates[0]
    all_exact = all(outcome.search_mode == "exact" for _, _, outcome in candidates)
    latest_date: date = window.index[-1].date()
    summary = {
        "symbol": symbol,
        "date": latest_date,
        "lookback_bars": lookback_bars,
        "observation_count": lookback_bars,
        "segment_count": segment_count,
        "change_point_count": segment_count - 1,
        "selected_rss": selected.rss,
        "single_segment_rss": single.rss,
        "selected_bic": selected_bic,
        "single_segment_bic": single_bic,
        "bic_improvement": float(single_bic - selected_bic),
        "min_segment_bars": min_segment_bars,
        "max_segments": max_segments,
        "bic_penalty_multiplier": bic_penalty_multiplier,
        "search_mode": "exact" if all_exact else "hybrid_approximate",
        "is_global_optimum": all_exact,
        "candidates_evaluated": sum(
            outcome.candidates_evaluated for _, _, outcome in candidates
        ),
        "refinement_converged": all(outcome.converged for _, _, outcome in candidates),
        "search_config": asdict(search_config),
        "search_diagnostics": diagnostics,
        "method": _METHOD,
        "calculation_version": _CALCULATION_VERSION,
    }

    segments: list[dict[str, Any]] = []
    for segment_index, (start, end) in enumerate(
        zip(selected.boundaries[:-1], selected.boundaries[1:])
    ):
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
                    selected.fitted,
                    float(selected.slopes[segment_index]),
                    start,
                    end,
                    window,
                ),
                "method": _METHOD,
                "calculation_version": _CALCULATION_VERSION,
            }
        )
    return summary, segments


def compute_adaptive_segmentation_snapshots(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute all configured adaptive segmentation lookbacks."""
    lookbacks = [int(value) for value in params.get("lookbacks", DEFAULT_LOOKBACKS)]
    min_segment_bars = int(params.get("min_segment_bars", 5))
    fixed_max_segments = params.get("max_segments")
    max_segments_cap = int(params.get("max_segments_cap", fixed_max_segments or 10))
    bic_penalty_multiplier = float(params.get("bic_penalty_multiplier", 3.0))
    search_params = dict(params.get("search", {}))
    summaries: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for lookback_bars in lookbacks:
        max_segments = (
            int(fixed_max_segments)
            if fixed_max_segments is not None
            else recommended_max_segments(lookback_bars, max_segments_cap)
        )
        summary, rows = compute_adaptive_segmentation(
            symbol,
            df,
            lookback_bars=lookback_bars,
            min_segment_bars=min_segment_bars,
            max_segments=max_segments,
            bic_penalty_multiplier=bic_penalty_multiplier,
            search_params=search_params,
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
