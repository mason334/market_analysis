from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from market_analysis.config import settings
from market_analysis.pipeline import run_pivot_segmentation as pipeline


def test_pipeline_reads_canonical_source_and_upserts_result(monkeypatch) -> None:
    snapshot_date = date(2026, 2, 12)
    index = pd.bdate_range("2026-01-02", periods=30)
    close = np.concatenate(
        (
            np.linspace(100.0, 125.0, 12),
            np.linspace(122.0, 80.0, 10),
            np.linspace(84.0, 110.0, 8),
        )
    )
    frame = pd.DataFrame({"close": close}, index=index)
    source_summary = {
        "symbol": "TEST",
        "date": snapshot_date,
        "lookback_bars": 30,
        "segment_count": 3,
        "method": "source_method",
        "calculation_version": "adaptive_segmentation_v3",
    }
    source_segments = [
        {
            "symbol": "TEST",
            "date": snapshot_date,
            "lookback_bars": 30,
            "segment_index": index_value,
            "start_bar_index": start,
            "end_bar_index": end,
            "fitted_log_return": fitted_return,
            "linearity_r2": 0.95,
            "vol_adjusted_trend": 2.0 if fitted_return > 0.0 else -2.0,
            "calculation_version": "adaptive_segmentation_v3",
        }
        for index_value, start, end, fitted_return in (
            (0, 0, 9, 0.12),
            (1, 10, 19, -0.15),
            (2, 20, 29, 0.10),
        )
    ]
    written: list[tuple[list[dict], list[dict]]] = []
    monkeypatch.setattr(
        settings,
        "indicators",
        {
            "adaptive_segmentation": {
                "lookbacks": [30],
                "segment_classification": {},
            },
            "pivot_refinement": {
                "search_radius_bars": 5,
                "min_segment_bars": 5,
            },
        },
    )
    monkeypatch.setattr(settings, "pipeline", {"source": "test"})
    monkeypatch.setattr(
        pipeline,
        "fetch_latest_adaptive_segmentation_date",
        lambda: snapshot_date,
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_trend_segmentation_snapshot",
        lambda _date: [source_summary],
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_trend_segment_snapshot",
        lambda _date: source_segments,
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_ohlcv",
        lambda _symbol, source="": frame,
    )
    monkeypatch.setattr(
        pipeline,
        "upsert_pivot_segmentation_daily",
        lambda summaries, segments: written.append((summaries, segments)),
    )
    progress = MagicMock()
    progress.add_task.return_value = 1
    monkeypatch.setattr(pipeline, "Progress", lambda *_columns: progress)

    completed = pipeline.run_pivot_segmentation_pipeline(show_progress=True)

    assert completed == 1
    assert len(written) == 1
    assert written[0][0][0]["calculation_version"] == "pivot_refined_segmentation_v2"
    assert [row["end_point_type"] for row in written[0][1]] == [
        "high",
        "low",
        "window_end",
    ]
    progress.add_task.assert_called_once_with("Pivot segmentation: preparing", total=1)
    progress.update.assert_called_once_with(
        1,
        description="Pivot segmentation: TEST (30 bars)",
    )
    progress.advance.assert_called_once_with(1)
    progress.start.assert_called_once_with()
    progress.stop.assert_called_once_with()
