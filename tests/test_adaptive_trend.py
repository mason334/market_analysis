from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from market_analysis.indicators.adaptive_trend import (
    _best_boundaries,
    _boundary_candidates,
    _fit_continuous_piecewise,
    candidate_count,
    compute_adaptive_segmentation,
    compute_adaptive_trend_experiment,
    recommended_max_segments,
    reconstruct_adaptive_fit,
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
    assert summary["method"] == "continuous_piecewise_log_linear_deterministic_hybrid_bic"
    assert summary["calculation_version"] == "adaptive_trend_v3"
    assert summary["search_mode"] == "exact"
    assert summary["is_global_optimum"] is True


def test_continuous_fit_does_not_absorb_boundary_jump_as_free_intercept() -> None:
    log_prices = np.concatenate((np.zeros(12), np.full(15, np.log(1.13))))

    coefficients, fitted, _, rss = _fit_continuous_piecewise(log_prices, (0, 12, 27))

    assert rss > 0
    knot = 11.0 / 26.0
    left_value = coefficients[0] + coefficients[1] * knot
    right_value = left_value + coefficients[2] * max(knot - 11.0 / 26.0, 0.0)
    assert left_value == pytest.approx(right_value)
    assert not np.allclose(fitted[:12], 0.0) or not np.allclose(
        fitted[12:], np.log(1.13)
    )


def test_batched_breakpoint_search_matches_direct_least_squares() -> None:
    x = np.arange(20, dtype=float)
    log_prices = 4.0 + 0.01 * x + 0.08 * np.maximum(x - 8.0, 0.0)
    result = _best_boundaries(log_prices, segment_count=3, min_segment_bars=5)

    assert result is not None
    selected_boundaries, _, _, selected_rss = result
    direct = [
        (rss, boundaries)
        for boundaries in _boundary_candidates(20, 3, 5)
        for _, _, _, rss in [_fit_continuous_piecewise(log_prices, boundaries)]
    ]
    expected_rss, expected_boundaries = min(direct)
    assert selected_boundaries == expected_boundaries
    assert selected_rss == pytest.approx(expected_rss, abs=1e-10)


def test_segment_returns_cover_every_window_return_once() -> None:
    down = 4.5 - 0.02 * np.arange(23)
    up = down[-1] + 0.03 * np.arange(1, 18)
    log_prices = np.concatenate((down, up))
    df = _frame(log_prices)

    summary, segments = compute_adaptive_segmentation("TEST", df, 40)

    assert summary is not None
    assert sum(float(row["actual_log_return"]) for row in segments) == pytest.approx(
        log_prices[-1] - log_prices[0]
    )
    for previous, current in zip(segments[:-1], segments[1:]):
        assert previous["end_bar_index"] + 1 == current["start_bar_index"]


def test_largest_move_diagnostic_keeps_signed_boundary_return() -> None:
    log_prices = np.zeros(40)
    log_prices[12:] = np.log(1.13)
    df = _frame(log_prices)

    summary, segments = compute_adaptive_segmentation("TEST", df, 40)

    assert summary is not None
    largest = max(segments, key=lambda row: abs(float(row["largest_move_log_return"])))
    assert largest["largest_move_log_return"] == pytest.approx(np.log(1.13))
    assert largest["largest_move_bar_index"] == 12
    assert largest["largest_move_date"] == df.index[12].date()
    assert largest["largest_move_path_share"] == pytest.approx(1.0)


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


def test_recommended_max_segments_and_candidate_counts() -> None:
    assert recommended_max_segments(40, 10) == 4
    assert recommended_max_segments(60, 10) == 5
    assert recommended_max_segments(250, 10) == 10
    assert candidate_count(40, 4, 5) == 1_771
    assert candidate_count(60, 5, 5) == 82_251


def test_experiment_uses_capped_recommendation_when_fixed_max_is_absent() -> None:
    df = _frame(4.0 + 0.005 * np.arange(80))

    summaries, _ = compute_adaptive_trend_experiment(
        "TEST",
        df,
        {"lookbacks": [40, 60], "min_segment_bars": 5, "max_segments_cap": 10},
    )

    assert [row["max_segments"] for row in summaries] == [4, 5]


def test_forced_hybrid_search_is_deterministic_and_audited() -> None:
    x = np.arange(60, dtype=float)
    log_prices = 4.0 + 0.01 * x + 0.04 * np.maximum(x - 18.0, 0.0)
    log_prices -= 0.06 * np.maximum(x - 39.0, 0.0)
    df = _frame(log_prices)
    search = {
        "exact_candidate_budget": 100,
        "beam_width": 4,
        "deterministic_seed_count": 4,
        "max_refinement_passes": 5,
    }

    first = compute_adaptive_segmentation(
        "TEST", df, 60, max_segments=4, search_params=search
    )
    second = compute_adaptive_segmentation(
        "TEST", df, 60, max_segments=4, search_params=search
    )

    assert first[0] is not None
    assert first[0] == second[0]
    assert first[1] == second[1]
    assert first[0]["search_mode"] == "hybrid_approximate"
    assert first[0]["is_global_optimum"] is False
    assert first[0]["candidates_evaluated"] > 100
    assert any(
        row["search_mode"] == "hybrid_approximate"
        for row in first[0]["search_diagnostics"]
    )


def test_reconstruct_fit_matches_segmentation_window() -> None:
    down = 4.5 - 0.02 * np.arange(23)
    up = down[-1] + 0.03 * np.arange(1, 18)
    df = _frame(np.concatenate((down, up)))
    summary, segments = compute_adaptive_segmentation("TEST", df, 40)

    assert summary is not None
    fit = reconstruct_adaptive_fit(df, 40, segments)

    assert list(fit.columns) == [
        "close",
        "log_close",
        "fitted_close",
        "fitted_log_close",
        "residual_log",
    ]
    assert len(fit) == 40
    assert np.isfinite(fit.to_numpy()).all()
    assert all(np.isfinite(float(row["fitted_anchor_log_price"])) for row in segments)


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
