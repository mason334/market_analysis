from __future__ import annotations

from datetime import date
from typing import Any

from market_analysis.pipeline import run_trend_patterns


def test_pattern_pipeline_reads_latest_segments_and_upserts(monkeypatch) -> None:
    snapshot_date = date(2026, 7, 15)
    summaries: list[dict[str, Any]] = [
        {
            "symbol": "TEST",
            "date": snapshot_date,
            "lookback_bars": 40,
            "segment_count": 1,
        }
    ]
    segments: list[dict[str, Any]] = [
        {
            "symbol": "TEST",
            "date": snapshot_date,
            "lookback_bars": 40,
            "segment_index": 0,
            "fitted_log_return": 0.1,
            "log_slope_per_bar": 0.01,
            "linearity_r2": 0.9,
            "vol_adjusted_trend": 1.5,
        }
    ]
    written: list[dict[str, Any]] = []
    monkeypatch.setattr(
        run_trend_patterns, "fetch_latest_trend_segmentation_date", lambda: snapshot_date
    )
    monkeypatch.setattr(
        run_trend_patterns, "fetch_trend_segmentation_snapshot", lambda _: summaries
    )
    monkeypatch.setattr(
        run_trend_patterns, "fetch_trend_segment_snapshot", lambda _: segments
    )
    monkeypatch.setattr(
        run_trend_patterns, "upsert_trend_pattern_daily", lambda rows: written.extend(rows)
    )

    total = run_trend_patterns.run_trend_pattern_pipeline()

    assert total == 1
    assert written[0]["pattern"] == "uptrend"
