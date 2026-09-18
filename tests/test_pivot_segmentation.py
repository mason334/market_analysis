from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from market_analysis.segmentation.pivot_segmentation import (
    classify_segment_direction,
    compute_pivot_segmentation,
    merge_classified_segments,
    reconstruct_pivot_fit,
)


def _source_segment(
    segment_index: int,
    start: int,
    end: int,
    fitted_return: float,
) -> dict[str, object]:
    return {
        "symbol": "TEST",
        "date": date(2026, 2, 12),
        "lookback_bars": 30,
        "segment_index": segment_index,
        "start_bar_index": start,
        "end_bar_index": end,
        "fitted_log_return": fitted_return,
        "linearity_r2": 0.95,
        "vol_adjusted_trend": 2.0 if fitted_return > 0.0 else -2.0,
    }


def _source() -> tuple[dict[str, object], list[dict[str, object]]]:
    summary: dict[str, object] = {
        "symbol": "TEST",
        "date": date(2026, 2, 12),
        "lookback_bars": 30,
        "segment_count": 3,
        "method": "continuous_piecewise_log_linear_deterministic_hybrid_bic",
        "calculation_version": "adaptive_segmentation_v3",
    }
    segments = [
        _source_segment(0, 0, 9, 0.12),
        _source_segment(1, 10, 19, -0.15),
        _source_segment(2, 20, 29, 0.10),
    ]
    return summary, segments


def _frame() -> pd.DataFrame:
    first = np.linspace(100.0, 125.0, 12)
    second = np.linspace(122.0, 80.0, 10)
    third = np.linspace(84.0, 110.0, 8)
    close = np.concatenate((first, second, third))
    return pd.DataFrame(
        {
            "close": close,
            "high": close + np.where(np.arange(30) == 7, 1_000.0, 1.0),
            "low": close - 1.0,
        },
        index=pd.bdate_range("2026-01-02", periods=30),
    )


def test_segment_classifier_uses_return_and_quality_gate() -> None:
    assert classify_segment_direction(
        {"fitted_log_return": 0.03, "linearity_r2": 0.8}
    ) == "up"
    assert classify_segment_direction(
        {"fitted_log_return": -0.03, "linearity_r2": 0.8}
    ) == "down"
    assert classify_segment_direction(
        {"fitted_log_return": 0.01, "linearity_r2": 1.0}
    ) == "flat"
    assert classify_segment_direction(
        {"fitted_log_return": 0.02, "linearity_r2": 0.35}
    ) == "up"


def test_adjacent_same_type_source_segments_are_merged() -> None:
    rows = [
        _source_segment(0, 0, 9, 0.08),
        _source_segment(1, 10, 19, 0.06),
        _source_segment(2, 20, 29, -0.08),
    ]

    merged = merge_classified_segments(rows)

    assert [row.direction for row in merged] == ["up", "down"]
    assert merged[0].source_segment_indices == (0, 1)
    assert merged[0].end_endpoint_bar_index == 19


def test_close_pivots_drive_independently_fitted_segments() -> None:
    source_summary, source_segments = _source()
    frame = _frame()

    summary, segments = compute_pivot_segmentation(
        "TEST",
        frame,
        source_summary,
        source_segments,
    )

    assert summary["method"] == "pivot_seeded_independent_piecewise_log_linear"
    assert summary["calculation_version"] == "pivot_refined_segmentation_v3"
    assert summary["window_close_min"] == pytest.approx(frame["close"].min())
    assert summary["window_close_max"] == pytest.approx(frame["close"].max())
    assert summary["pivot_count"] == 2
    assert [row["end_point_type"] for row in segments] == [
        "high",
        "low",
        "window_end",
    ]
    assert [row["end_endpoint_bar_index"] for row in segments] == [11, 21, 29]
    assert [row["pivot_seed_bar_index"] for row in segments[:-1]] == [9, 19]
    assert [row["pivot_displacement_bars"] for row in segments[:-1]] == [2, 2]
    assert all(int(row["observation_count"]) >= 5 for row in segments)
    assert segments[0]["fitted_end_log_price"] != pytest.approx(
        segments[1]["fitted_start_log_price"]
    )
    assert segments[1]["fitted_end_log_price"] != pytest.approx(
        segments[2]["fitted_start_log_price"]
    )
    expected_rss = 0.0
    for row in segments:
        start = int(row["start_endpoint_bar_index"])
        end = int(row["end_endpoint_bar_index"])
        actual = np.log(frame["close"].iloc[start : end + 1].to_numpy())
        coefficients = np.linalg.lstsq(
            np.column_stack((np.ones(len(actual)), np.arange(len(actual)))),
            actual,
            rcond=None,
        )[0]
        expected_fit = coefficients[0] + coefficients[1] * np.arange(len(actual))
        expected_rss += float(np.sum(np.square(actual - expected_fit)))
        assert row["log_slope_per_bar"] == pytest.approx(coefficients[1])
    assert summary["fit_rss"] == pytest.approx(expected_rss)
    assert sum(float(row["actual_log_return"]) for row in segments) == pytest.approx(
        np.log(frame["close"].iloc[-1]) - np.log(frame["close"].iloc[0])
    )

    reconstructed = reconstruct_pivot_fit(frame, summary, segments)
    assert len(reconstructed) == 30 + summary["pivot_count"]
    assert reconstructed["segment_index"].nunique() == summary["segment_count"]
    assert np.isfinite(reconstructed.to_numpy()).all()


def test_xle_shape_keeps_a_declining_pivot_interval_declining() -> None:
    source_summary, source_segments = _source()
    close = np.concatenate(
        (
            np.linspace(40.0, 62.56, 12),
            np.linspace(61.96, 55.02, 10),
            np.linspace(55.07, 57.0, 8),
        )
    )
    frame = pd.DataFrame(
        {"close": close},
        index=pd.bdate_range("2026-01-02", periods=30),
    )

    _, segments = compute_pivot_segmentation(
        "TEST", frame, source_summary, source_segments
    )

    middle = segments[1]
    assert middle["actual_log_return"] < 0.0
    assert middle["fitted_log_return"] < 0.0
    assert middle["log_slope_per_bar"] < 0.0
    assert middle["segment_type"] == "down"


def test_pivot_search_ignores_ohlc_extremes_and_is_deterministic() -> None:
    source_summary, source_segments = _source()
    frame = _frame()

    first = compute_pivot_segmentation("TEST", frame, source_summary, source_segments)
    altered = frame.assign(
        high=np.linspace(10_000.0, 20_000.0, len(frame)),
        low=np.linspace(1.0, 2.0, len(frame)),
    )
    second = compute_pivot_segmentation(
        "TEST", altered, source_summary, source_segments
    )

    assert first == second


def test_window_close_bounds_use_only_the_actual_fallback_window() -> None:
    source_summary, source_segments = _source()
    frame = _frame()
    earlier = pd.DataFrame(
        {"close": [1.0, 1_000.0]},
        index=pd.bdate_range(end=frame.index[0] - pd.Timedelta(days=1), periods=2),
    )

    summary, _ = compute_pivot_segmentation(
        "TEST",
        pd.concat([earlier, frame]),
        source_summary,
        source_segments,
    )

    assert summary["window_close_min"] == pytest.approx(frame["close"].min())
    assert summary["window_close_max"] == pytest.approx(frame["close"].max())


def test_wrong_source_version_is_rejected() -> None:
    source_summary, source_segments = _source()
    source_summary["calculation_version"] = "adaptive_trend_v3"

    with pytest.raises(ValueError, match="adaptive_segmentation_v3"):
        compute_pivot_segmentation(
            "TEST", _frame(), source_summary, source_segments
        )


@pytest.mark.parametrize(
    ("left_return", "right_return", "expected_type"),
    [
        (0.08, -0.08, "high"),
        (0.08, 0.0, "high"),
        (0.0, -0.08, "high"),
        (-0.08, 0.08, "low"),
        (-0.08, 0.0, "low"),
        (0.0, 0.08, "low"),
    ],
)
def test_all_six_direction_transitions_create_expected_pivot_type(
    left_return: float,
    right_return: float,
    expected_type: str,
) -> None:
    snapshot_date = date(2026, 1, 29)
    source_summary = {
        "symbol": "TEST",
        "date": snapshot_date,
        "lookback_bars": 20,
        "segment_count": 2,
        "method": "source_method",
        "calculation_version": "adaptive_segmentation_v3",
    }
    source_segments = [
        {
            **_source_segment(0, 0, 9, left_return),
            "date": snapshot_date,
            "lookback_bars": 20,
            "vol_adjusted_trend": None if left_return == 0.0 else 2.0,
        },
        {
            **_source_segment(1, 10, 19, right_return),
            "date": snapshot_date,
            "lookback_bars": 20,
            "vol_adjusted_trend": None if right_return == 0.0 else 2.0,
        },
    ]
    if expected_type == "high":
        close = np.concatenate((np.arange(10, dtype=float), np.arange(10, 0, -1))) + 100
    else:
        close = np.concatenate((np.arange(10, 0, -1), np.arange(10, dtype=float))) + 100
    frame = pd.DataFrame(
        {"close": close},
        index=pd.bdate_range("2026-01-02", periods=20),
    )

    summary, segments = compute_pivot_segmentation(
        "TEST", frame, source_summary, source_segments
    )

    assert summary["pivot_count"] == 1
    assert segments[0]["end_point_type"] == expected_type
    assert segments[-1]["end_point_type"] == "window_end"


@pytest.mark.parametrize(
    ("source_returns", "expected_type"),
    [((0.08, 0.0, -0.08), "high"), ((-0.08, 0.0, 0.08), "low")],
)
def test_platform_double_seed_collapses_to_one_pivot(
    source_returns: tuple[float, float, float],
    expected_type: str,
) -> None:
    source_summary, source_segments = _source()
    for row, fitted_return in zip(source_segments, source_returns):
        row["fitted_log_return"] = fitted_return
        row["vol_adjusted_trend"] = None if fitted_return == 0.0 else 2.0

    summary, segments = compute_pivot_segmentation(
        "TEST", _frame(), source_summary, source_segments
    )

    assert summary["resolution_diagnostics"]["raw_seed_count"] == 2
    assert summary["resolution_diagnostics"]["normalized_seed_count"] == 1
    assert summary["pivot_count"] == 1
    assert segments[0]["end_point_type"] == expected_type


def test_equal_close_extreme_uses_nearest_then_earlier_bar() -> None:
    snapshot_date = date(2026, 1, 29)
    summary = {
        "symbol": "TEST",
        "date": snapshot_date,
        "lookback_bars": 20,
        "segment_count": 2,
        "method": "source_method",
        "calculation_version": "adaptive_segmentation_v3",
    }
    segments = [
        {**_source_segment(0, 0, 9, 0.08), "date": snapshot_date, "lookback_bars": 20},
        {**_source_segment(1, 10, 19, -0.08), "date": snapshot_date, "lookback_bars": 20},
    ]
    close = np.full(20, 100.0)
    close[8] = close[10] = 120.0
    frame = pd.DataFrame(
        {"close": close},
        index=pd.bdate_range("2026-01-02", periods=20),
    )

    _, refined = compute_pivot_segmentation("TEST", frame, summary, segments)

    assert refined[0]["pivot_seed_bar_index"] == 9
    assert refined[0]["end_endpoint_bar_index"] == 8


def test_window_edge_extreme_falls_back_to_legal_segment_boundary() -> None:
    snapshot_date = date(2026, 1, 29)
    summary = {
        "symbol": "TEST",
        "date": snapshot_date,
        "lookback_bars": 20,
        "segment_count": 2,
        "method": "source_method",
        "calculation_version": "adaptive_segmentation_v3",
    }
    segments = [
        {**_source_segment(0, 0, 4, 0.08), "date": snapshot_date, "lookback_bars": 20},
        {**_source_segment(1, 5, 19, -0.08), "date": snapshot_date, "lookback_bars": 20},
    ]
    frame = pd.DataFrame(
        {"close": np.linspace(120.0, 80.0, 20)},
        index=pd.bdate_range("2026-01-02", periods=20),
    )

    _, refined = compute_pivot_segmentation("TEST", frame, summary, segments)

    assert refined[0]["end_endpoint_bar_index"] == 4
    assert refined[0]["observation_count"] == 5


def test_adjacent_pivot_conflict_discards_one_seed_with_audit_reason() -> None:
    snapshot_date = date(2026, 1, 29)
    summary = {
        "symbol": "TEST",
        "date": snapshot_date,
        "lookback_bars": 20,
        "segment_count": 3,
        "method": "source_method",
        "calculation_version": "adaptive_segmentation_v3",
    }
    segments = [
        {**_source_segment(0, 0, 9, 0.08), "date": snapshot_date, "lookback_bars": 20},
        {**_source_segment(1, 10, 11, -0.08), "date": snapshot_date, "lookback_bars": 20},
        {**_source_segment(2, 12, 19, 0.08), "date": snapshot_date, "lookback_bars": 20},
    ]
    frame = pd.DataFrame(
        {"close": 100.0 + np.sin(np.arange(20, dtype=float))},
        index=pd.bdate_range("2026-01-02", periods=20),
    )

    refined_summary, refined = compute_pivot_segmentation(
        "TEST",
        frame,
        summary,
        segments,
        search_radius_bars=1,
        min_segment_bars=5,
    )

    assert refined_summary["pivot_count"] == 1
    assert len(refined_summary["resolution_diagnostics"]["discarded_seed_groups"]) == 1
    assert refined_summary["resolution_diagnostics"]["discarded_seed_groups"][0][
        "reason"
    ] == "no_position_in_best_feasible_alternating_path"
    assert len(refined) == 2
