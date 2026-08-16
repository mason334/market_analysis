from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from market_analysis.config import settings
from market_analysis.pipeline import validate_adaptive_segmentation as validation


def _frame(size: int = 24) -> pd.DataFrame:
    x = np.arange(size, dtype=float)
    log_prices = 4.0 + 0.01 * x + 0.025 * np.maximum(x - 11, 0.0)
    return pd.DataFrame(
        {
            "open": np.exp(log_prices),
            "high": np.exp(log_prices) * 1.01,
            "low": np.exp(log_prices) * 0.99,
            "close": np.exp(log_prices),
            "volume": 1_000,
        },
        index=pd.bdate_range("2026-01-02", periods=size),
    )


def test_breakpoint_similarity_matches_with_tolerance() -> None:
    assert validation._breakpoint_match_count([], [], 2) == 0
    assert validation._breakpoint_match_count([10, 20], [11, 24], 2) == 1
    assert validation._breakpoint_match_count([3, 5], [1, 4], 2) == 2


def test_tolerance_list_is_supported_as_backward_compatible_input() -> None:
    assert validation._configured_tolerances(
        {"breakpoint_tolerance_bars": [2, 3]}
    ) == (2, [2, 3])


def test_anchor_offsets_generate_explicit_local_comparison_pairs() -> None:
    base_offsets, pairs, calculation_offsets = validation._configured_anchor_pairs(
        {"anchor_offsets": [0, 10, 20], "anchor_comparison_step_bars": 2}
    )

    assert base_offsets == [0, 10, 20]
    assert pairs == [(0, 2), (10, 12), (20, 22)]
    assert calculation_offsets == [0, 2, 10, 12, 20, 22]


def test_production_parameters_default_to_recommended_segment_cap(monkeypatch) -> None:
    monkeypatch.setattr(settings, "indicators", {"adaptive_segmentation": {}})

    assert validation._production_parameters() == validation.ParameterSet(3.0, 5, 10)


def test_default_output_dir_uses_run_timestamp() -> None:
    started_at = datetime(
        2026,
        7,
        20,
        15,
        30,
        12,
        123456,
        tzinfo=timezone(timedelta(hours=8)),
    )

    output_dir = validation._default_output_dir(date(2026, 7, 17), started_at)

    assert output_dir.parts[-2:] == (
        "2026-07-17",
        "run_20260720_153012_123456",
    )


def test_local_stability_excludes_illegal_overlap_edges_and_no_breakpoints() -> None:
    parameter_set = validation.ParameterSet(3.0, 5, 4)
    summaries = pd.DataFrame(
        [
            {
                "symbol": "TEST",
                "lookback_bars": 60,
                "date": date(2026, 3, 31),
                "anchor_offset": 0,
                "segment_count": 2,
                "global_window_start_index": 10,
                "global_window_end_index": 69,
            },
            {
                "symbol": "TEST",
                "lookback_bars": 60,
                "date": date(2026, 3, 17),
                "anchor_offset": 2,
                "segment_count": 2,
                "global_window_start_index": 0,
                "global_window_end_index": 59,
            },
        ]
    )
    edge_segments = pd.DataFrame(
        [
            {"symbol": "TEST", "lookback_bars": 60, "date": date(2026, 3, 31),
             "anchor_offset": 0, "segment_index": 0, "global_start_index": 10},
            {"symbol": "TEST", "lookback_bars": 60, "date": date(2026, 3, 31),
             "anchor_offset": 0, "segment_index": 1, "global_start_index": 13},
            {"symbol": "TEST", "lookback_bars": 60, "date": date(2026, 3, 17),
             "anchor_offset": 2, "segment_index": 0, "global_start_index": 0},
            {"symbol": "TEST", "lookback_bars": 60, "date": date(2026, 3, 17),
             "anchor_offset": 2, "segment_index": 1, "global_start_index": 12},
        ]
    )

    metrics = validation._local_breakpoint_stability_metrics(
        parameter_set, summaries, edge_segments, [2], [(0, 2)]
    )

    assert metrics["local_breakpoint_set_stability_tol_2"] is None
    assert metrics["both_no_comparable_breakpoints_rate"] == 1.0

    right_edge_segments = edge_segments.copy()
    right_edge_segments.loc[
        right_edge_segments["segment_index"] == 1, "global_start_index"
    ] = 55
    right_edge_metrics = validation._local_breakpoint_stability_metrics(
        parameter_set, summaries, right_edge_segments, [2], [(0, 2)]
    )

    assert right_edge_metrics["local_breakpoint_set_stability_tol_2"] == 1.0


def test_full_grid_validation_writes_report_files(tmp_path, monkeypatch) -> None:
    frame = _frame()
    monkeypatch.setattr(
        settings,
        "indicators",
        {
            "adaptive_segmentation": {
                "lookbacks": [20],
                "min_segment_bars": 4,
                "max_segments": 2,
                "bic_penalty_multiplier": 2.0,
            }
        },
    )
    monkeypatch.setattr(settings, "pipeline", {"source": "test"})
    monkeypatch.setattr(
        settings,
        "validation",
        {
            "adaptive_segmentation": {
                "symbols": ["TEST"],
                "anchor_offsets": [0],
                "anchor_comparison_step_bars": 10,
                "bic_penalty_grid": [2.0],
                "min_segment_bars_grid": [4],
                "max_segments_grid": [2],
                "breakpoint_tolerance_bars": 1,
                "breakpoint_tolerance_sensitivity_bars": [1, 2],
                "chart_parameter_sets": 1,
            }
        },
    )
    monkeypatch.setattr(validation, "fetch_ohlcv", lambda symbol, source="": frame)

    report = validation.run_adaptive_segmentation_validation(
        target_date=date(2026, 2, 6),
        output_dir=tmp_path,
        full_grid=True,
    )

    assert report == tmp_path / "index.html"
    assert report.exists()
    assert (tmp_path / "summary.csv").exists()
    assert (tmp_path / "segments.csv").exists()
    assert (tmp_path / "parameter_comparison.csv").exists()
    comparison = pd.read_csv(tmp_path / "parameter_comparison.csv")
    assert "fit_complexity_score" in comparison
    assert "diagnostic_score_v2" not in comparison
    assert "screening_score" not in comparison
    assert "local_breakpoint_set_stability_tol_1" in comparison
    assert "local_breakpoint_set_stability_tol_2" in comparison
    assert "local_breakpoint_set_stability" in comparison
    assert "breakpoint_persistence" not in comparison
    assert "near_min_segment_length_rate" in comparison
    assert "max_segment_count_hit_rate" in comparison
    assert "short_segment_rate" not in comparison
    assert "max_segment_rate" not in comparison
    assert comparison.loc[0, "ranking_status"] == "ranked"
    assert pd.isna(comparison.loc[0, "local_breakpoint_set_stability"])
    assert pd.notna(comparison.loc[0, "fit_complexity_score"])
    expected_score = (
        0.35 * comparison.loc[0, "median_rss_reduction_ratio"]
        + 0.25 * comparison.loc[0, "weighted_segment_linearity_r2"]
        + 0.20 * (1.0 - comparison.loc[0, "max_segment_count_hit_rate"])
        + 0.15 * (1.0 - comparison.loc[0, "near_min_segment_length_rate"])
        + 0.05 * (1.0 - comparison.loc[0, "jump_dominated_rate"])
    )
    assert np.isclose(comparison.loc[0, "fit_complexity_score"], expected_score)
    html = report.read_text(encoding="utf-8")
    assert "Adaptive segmentation validation" in html
    assert "bic=2|min=4|max=2" in html
    assert "展开查看 Parameter comparison 字段与算法说明" in html
    assert "local_breakpoint_set_stability" in html
    assert "fit_complexity_score" in html
    assert "<details class=\"field-guide\">" in html


def test_parameter_progress_advances_once_per_executed_parameter_set() -> None:
    class ProgressRecorder:
        def __init__(self) -> None:
            self.total = 0
            self.advanced = 0

        def add_task(self, description: str, total: int) -> int:
            self.total = total
            return 1

        def update(self, task_id: int, description: str) -> None:
            assert task_id == 1
            assert "bic=2|min=4|max=2" in description

        def advance(self, task_id: int) -> None:
            assert task_id == 1
            self.advanced += 1

    progress = ProgressRecorder()
    cache: dict[str, tuple[list[dict[str, object]], list[dict[str, object]]]] = {}
    validation._run_parameter_sets(
        {"TEST": _frame()},
        [validation.ParameterSet(2.0, 4, 2)],
        [20],
        date(2026, 2, 6),
        [0],
        "full_grid",
        cache,  # type: ignore[arg-type]
        progress=progress,  # type: ignore[arg-type]
    )

    assert progress.total == 1
    assert progress.advanced == 1
