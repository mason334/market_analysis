from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from market_analysis.pipeline import run_trend_pattern_v4


def _snapshot() -> tuple[
    date,
    list[dict[str, Any]],
    list[dict[str, Any]],
    pd.DataFrame,
]:
    snapshot_date = date(2026, 7, 15)
    index = pd.bdate_range(end=snapshot_date, periods=40)
    closes = pd.DataFrame({"close": np.linspace(100.0, 120.0, 40)}, index=index)
    method = "continuous_piecewise_log_linear_exhaustive_bic"
    version = "adaptive_trend_v2"
    summaries = [
        {
            "symbol": "TEST",
            "date": snapshot_date,
            "lookback_bars": 40,
            "observation_count": 40,
            "segment_count": 1,
            "method": method,
            "calculation_version": version,
        }
    ]
    segments = [
        {
            "symbol": "TEST",
            "date": snapshot_date,
            "lookback_bars": 40,
            "segment_index": 0,
            "start_date": index[0].date(),
            "end_date": index[-1].date(),
            "start_bar_index": 0,
            "end_bar_index": 39,
            "fitted_log_return": 0.18,
            "linearity_r2": 0.99,
            "vol_adjusted_trend": 2.0,
            "method": method,
            "calculation_version": version,
        }
    ]
    return snapshot_date, summaries, segments, closes


def test_v4_pipeline_builds_structure_metrics_and_upserts(monkeypatch) -> None:
    snapshot_date, summaries, segments, closes = _snapshot()
    written: list[dict[str, Any]] = []
    monkeypatch.setattr(
        run_trend_pattern_v4,
        "fetch_latest_trend_segmentation_date",
        lambda: snapshot_date,
    )
    monkeypatch.setattr(
        run_trend_pattern_v4,
        "fetch_trend_segmentation_snapshot",
        lambda _: summaries,
    )
    monkeypatch.setattr(
        run_trend_pattern_v4,
        "fetch_trend_segment_snapshot",
        lambda _: segments,
    )
    monkeypatch.setattr(
        run_trend_pattern_v4,
        "fetch_split_adjusted_close_window",
        lambda symbol, target_date, count: closes,
    )
    monkeypatch.setattr(
        run_trend_pattern_v4,
        "upsert_trend_pattern_v4_daily",
        lambda rows: written.extend(rows),
    )

    total = run_trend_pattern_v4.run_trend_pattern_v4_pipeline()

    assert total == 1
    assert written[0]["structure_code"] == "L1"
    assert written[0]["start_direction"] == "up"
    assert written[0]["effective_leg_count"] == 1
    assert written[0]["terminal_leg_start_position"] == 0.0
    assert written[0]["observation_count"] == 40
    assert written[0]["calculation_version"] == "trend_pattern_v4_1"


def test_v4_pipeline_keeps_raw_metrics_when_no_effective_leg(monkeypatch) -> None:
    snapshot_date, summaries, segments, closes = _snapshot()
    segments[0]["fitted_log_return"] = 0.005
    written: list[dict[str, Any]] = []
    monkeypatch.setattr(
        run_trend_pattern_v4,
        "fetch_trend_segmentation_snapshot",
        lambda _: summaries,
    )
    monkeypatch.setattr(
        run_trend_pattern_v4,
        "fetch_trend_segment_snapshot",
        lambda _: segments,
    )
    monkeypatch.setattr(
        run_trend_pattern_v4,
        "fetch_split_adjusted_close_window",
        lambda symbol, target_date, count: closes,
    )
    monkeypatch.setattr(
        run_trend_pattern_v4,
        "upsert_trend_pattern_v4_daily",
        lambda rows: written.extend(rows),
    )

    total = run_trend_pattern_v4.run_trend_pattern_v4_pipeline(snapshot_date)

    assert total == 1
    assert written[0]["effective_leg_count"] == 0
    assert written[0]["structure_code"] is None
    assert written[0]["terminal_leg_start_position"] is None
    assert written[0]["net_log_return"] is not None
