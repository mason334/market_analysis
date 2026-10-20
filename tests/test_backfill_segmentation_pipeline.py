from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from market_analysis.pipeline import backfill_segmentation as pipeline


def test_requested_lookback_counts_deduplicates_shared_fallback() -> None:
    target_date = date(2026, 9, 25)
    metadata = {
        "FULL": (target_date, 300),
        "MID": (target_date, 180),
        "SHORT": (target_date, 100),
        "STALE": (date(2026, 9, 24), 300),
        "INSUFFICIENT": (target_date, 39),
    }

    result = pipeline._requested_lookback_counts(
        metadata,
        target_date=target_date,
        lookbacks=(250, 125),
        min_fallback_bars=40,
    )

    assert result == {250: 3, 125: 2}


def test_backfill_skips_complete_dates(monkeypatch) -> None:
    target_date = date(2026, 9, 25)
    monkeypatch.setattr(
        pipeline,
        "settings",
        SimpleNamespace(
            indicators={
                "adaptive_segmentation": {
                    "lookbacks": [250, 125],
                    "min_fallback_bars": 40,
                }
            },
            pipeline={"source": "tiingo"},
        ),
    )
    monkeypatch.setattr(pipeline, "fetch_segmentation_analysis_symbols", lambda: ["A", "B"])
    monkeypatch.setattr(
        pipeline,
        "fetch_source_trading_dates",
        lambda *args, **kwargs: [target_date],
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_adaptive_input_metadata",
        lambda *args, **kwargs: {"A": (target_date, 300), "B": (target_date, 100)},
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_segmentation_backfill_counts",
        lambda target_date, lookback, **kwargs: (2, 2) if lookback == 250 else (1, 1),
    )
    adaptive_calls: list[date] = []
    pivot_calls: list[tuple[date, int]] = []
    monkeypatch.setattr(
        pipeline,
        "run_adaptive_segmentation_pipeline",
        lambda target_date, **kwargs: adaptive_calls.append(target_date),
    )
    monkeypatch.setattr(
        pipeline,
        "run_pivot_segmentation_pipeline",
        lambda target_date, requested_lookback_bars, **kwargs: pivot_calls.append(
            (target_date, requested_lookback_bars)
        ),
    )

    result = pipeline.run_segmentation_backfill(
        target_date,
        target_date,
        show_progress=False,
    )

    assert adaptive_calls == []
    assert pivot_calls == []
    assert result.adaptive_dates_run == 0
    assert result.pivot_windows_run == 0
    assert result.pivot_windows_skipped == 2


def test_backfill_runs_missing_adaptive_and_pivot_windows(monkeypatch) -> None:
    target_date = date(2026, 9, 25)
    monkeypatch.setattr(
        pipeline,
        "settings",
        SimpleNamespace(
            indicators={
                "adaptive_segmentation": {
                    "lookbacks": [250, 125],
                    "min_fallback_bars": 40,
                }
            },
            pipeline={"source": "tiingo"},
        ),
    )
    monkeypatch.setattr(pipeline, "fetch_segmentation_analysis_symbols", lambda: ["A", "B"])
    monkeypatch.setattr(
        pipeline,
        "fetch_source_trading_dates",
        lambda *args, **kwargs: [target_date],
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_adaptive_input_metadata",
        lambda *args, **kwargs: {"A": (target_date, 300), "B": (target_date, 100)},
    )

    adaptive_complete = False
    completed_pivots: set[int] = set()

    def counts(target_date, lookback, **kwargs):
        del target_date, kwargs
        expected = 2 if lookback == 250 else 1
        return (
            expected if adaptive_complete else 0,
            expected if lookback in completed_pivots else 0,
        )

    def run_adaptive(target_date, **kwargs):
        del target_date, kwargs
        nonlocal adaptive_complete
        adaptive_complete = True
        return 2

    def run_pivot(target_date, requested_lookback_bars, **kwargs):
        del target_date, kwargs
        completed_pivots.add(requested_lookback_bars)
        return 2

    monkeypatch.setattr(pipeline, "fetch_segmentation_backfill_counts", counts)
    monkeypatch.setattr(pipeline, "run_adaptive_segmentation_pipeline", run_adaptive)
    monkeypatch.setattr(pipeline, "run_pivot_segmentation_pipeline", run_pivot)

    result = pipeline.run_segmentation_backfill(
        target_date,
        target_date,
        show_progress=False,
    )

    assert completed_pivots == {125, 250}
    assert result.adaptive_dates_run == 1
    assert result.pivot_windows_run == 2
    assert result.pivot_windows_skipped == 0
