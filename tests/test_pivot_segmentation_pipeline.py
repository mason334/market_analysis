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
        "requested_lookback_bars": 250,
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
                "lookbacks": [250],
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
    logger = MagicMock()
    monkeypatch.setattr(pipeline, "log", logger)

    completed = pipeline.run_pivot_segmentation_pipeline(show_progress=True)

    assert completed == 1
    assert len(written) == 1
    assert written[0][0][0]["requested_lookback_bars"] == 250
    assert written[0][0][0]["calculation_version"] == "pivot_refined_segmentation_v3"
    assert written[0][0][0]["window_close_min"] == float(close.min())
    assert written[0][0][0]["window_close_max"] == float(close.max())
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
    logger.info.assert_any_call(
        "pivot_segmentation.symbol.done",
        symbol="TEST",
        lookbacks=[30],
        snapshots=1,
        segments=3,
        progress="[1/1]",
        completion="100.0%",
        eta="0sec",
    )


def _source_summary(
    snapshot_date: date,
    symbol: str,
    lookback_bars: int,
    requested_lookback_bars: int,
) -> dict:
    return {
        "symbol": symbol,
        "date": snapshot_date,
        "requested_lookback_bars": requested_lookback_bars,
        "lookback_bars": lookback_bars,
        "segment_count": 1,
        "method": "source_method",
        "calculation_version": "adaptive_segmentation_v3",
    }


def _computed_result(source_summary: dict) -> tuple[dict, list[dict]]:
    summary = {
        "symbol": source_summary["symbol"],
        "date": source_summary["date"],
        "requested_lookback_bars": source_summary["requested_lookback_bars"],
        "lookback_bars": source_summary["lookback_bars"],
        "pivot_count": 0,
        "segment_count": 1,
    }
    segments = [
        {
            "symbol": source_summary["symbol"],
            "date": source_summary["date"],
            "lookback_bars": source_summary["lookback_bars"],
            "segment_index": 0,
        }
    ]
    return summary, segments


def _patch_pipeline_inputs(monkeypatch, snapshot_date: date, summaries: list[dict]) -> None:
    monkeypatch.setattr(
        settings,
        "indicators",
        {
            "adaptive_segmentation": {"segment_classification": {}},
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
        lambda _date: summaries,
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_trend_segment_snapshot",
        lambda _date: [],
    )


def test_pipeline_upserts_all_symbol_lookbacks_together(monkeypatch) -> None:
    snapshot_date = date(2026, 8, 16)
    summaries = [
        _source_summary(snapshot_date, "TEST", 250, 250),
        _source_summary(snapshot_date, "TEST", 65, 250),
    ]
    _patch_pipeline_inputs(monkeypatch, snapshot_date, summaries)
    frame = pd.DataFrame(
        {"close": np.linspace(100.0, 120.0, 300)},
        index=pd.bdate_range("2025-06-02", periods=300),
    )
    fetch_ohlcv = MagicMock(return_value=frame)
    monkeypatch.setattr(pipeline, "fetch_ohlcv", fetch_ohlcv)
    computed_lookbacks: list[int] = []

    def compute(
        _symbol: str,
        _frame: pd.DataFrame,
        source_summary: dict,
        _source_segments: list[dict],
        **_params: object,
    ) -> tuple[dict, list[dict]]:
        computed_lookbacks.append(int(source_summary["lookback_bars"]))
        return _computed_result(source_summary)

    monkeypatch.setattr(pipeline, "compute_pivot_segmentation", compute)
    writes: list[tuple[list[dict], list[dict]]] = []
    monkeypatch.setattr(
        pipeline,
        "upsert_pivot_segmentation_daily",
        lambda output_summaries, output_segments: writes.append(
            (output_summaries, output_segments)
        ),
    )

    completed = pipeline.run_pivot_segmentation_pipeline(snapshot_date)

    assert completed == 2
    assert computed_lookbacks == [65, 250]
    fetch_ohlcv.assert_called_once_with("TEST", source="test")
    assert len(writes) == 1
    assert [row["lookback_bars"] for row in writes[0][0]] == [65, 250]
    assert [row["lookback_bars"] for row in writes[0][1]] == [65, 250]


def test_pipeline_discards_symbol_batch_when_one_lookback_fails(monkeypatch) -> None:
    snapshot_date = date(2026, 8, 16)
    summaries = [
        _source_summary(snapshot_date, "BAD", 65, 250),
        _source_summary(snapshot_date, "BAD", 250, 250),
        _source_summary(snapshot_date, "GOOD", 100, 250),
    ]
    _patch_pipeline_inputs(monkeypatch, snapshot_date, summaries)
    frame = pd.DataFrame(
        {"close": np.linspace(100.0, 120.0, 300)},
        index=pd.bdate_range("2025-06-02", periods=300),
    )
    fetch_ohlcv = MagicMock(return_value=frame)
    monkeypatch.setattr(pipeline, "fetch_ohlcv", fetch_ohlcv)

    def compute(
        symbol: str,
        _frame: pd.DataFrame,
        source_summary: dict,
        _source_segments: list[dict],
        **_params: object,
    ) -> tuple[dict, list[dict]]:
        if symbol == "BAD" and source_summary["lookback_bars"] == 250:
            raise ValueError("expected failure")
        return _computed_result(source_summary)

    monkeypatch.setattr(pipeline, "compute_pivot_segmentation", compute)
    writes: list[tuple[list[dict], list[dict]]] = []
    monkeypatch.setattr(
        pipeline,
        "upsert_pivot_segmentation_daily",
        lambda output_summaries, output_segments: writes.append(
            (output_summaries, output_segments)
        ),
    )
    progress = MagicMock()
    progress.add_task.return_value = 1
    monkeypatch.setattr(pipeline, "Progress", lambda *_columns: progress)

    completed = pipeline.run_pivot_segmentation_pipeline(
        snapshot_date,
        show_progress=True,
    )

    assert completed == 1
    assert fetch_ohlcv.call_count == 2
    assert len(writes) == 1
    assert [row["symbol"] for row in writes[0][0]] == ["GOOD"]
    assert [row["lookback_bars"] for row in writes[0][0]] == [100]
    progress.add_task.assert_called_once_with(
        "Pivot segmentation: preparing",
        total=2,
    )
    assert progress.advance.call_count == 2
