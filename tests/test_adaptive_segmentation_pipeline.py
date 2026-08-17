from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from market_analysis.config import settings
from market_analysis.pipeline import run_adaptive_segmentation as pipeline


class ProgressRecorder:
    instance: ProgressRecorder | None = None

    def __init__(self, *_columns: object) -> None:
        type(self).instance = self
        self.total = 0
        self.advanced = 0
        self.descriptions: list[str] = []
        self.started = False
        self.stopped = False

    def add_task(self, description: str, *, total: int) -> int:
        self.total = total
        self.descriptions.append(description)
        return 1

    def start(self) -> None:
        self.started = True

    def update(self, _task_id: int, *, description: str) -> None:
        self.descriptions.append(description)

    def advance(self, _task_id: int) -> None:
        self.advanced += 1

    def stop(self) -> None:
        self.stopped = True


def _patch_market_inputs(
    monkeypatch,
    frames: dict[str, pd.DataFrame],
) -> None:
    def metadata(
        symbols: list[str],
        *,
        source: str,
        target_date,
        max_lookback_bars: int,
    ) -> dict[str, tuple[object, int]]:
        del source
        result: dict[str, tuple[object, int]] = {}
        for symbol in symbols:
            frame = frames[symbol]
            if target_date is not None:
                frame = frame[frame.index.date <= target_date]
            result[symbol] = (
                frame.index[-1].date() if not frame.empty else None,
                min(len(frame), max_lookback_bars),
            )
        return result

    def close_window(
        symbol: str,
        *,
        source: str,
        target_date,
        observation_count: int,
    ) -> pd.DataFrame:
        del source
        frame = frames[symbol]
        if target_date is not None:
            frame = frame[frame.index.date <= target_date]
        return frame.iloc[-observation_count:]

    monkeypatch.setattr(pipeline, "fetch_adaptive_input_metadata", metadata)
    monkeypatch.setattr(pipeline, "fetch_adaptive_close_window", close_window)
    monkeypatch.setattr(
        pipeline,
        "fetch_adaptive_resume_candidates",
        lambda *_args, **_kwargs: [],
    )


def test_progress_advances_for_written_skipped_and_failed_symbols(monkeypatch) -> None:
    symbols = ["WRITTEN", "SKIPPED", "FAILED"]
    sufficient_frame = pd.DataFrame(
        {"close": np.linspace(100.0, 120.0, 5)},
        index=pd.bdate_range("2026-01-02", periods=5),
    )
    short_frame = sufficient_frame.iloc[:2]
    written: list[str] = []

    monkeypatch.setattr(
        settings,
        "indicators",
        {"adaptive_segmentation": {"lookbacks": [5], "min_fallback_bars": 3}},
    )
    monkeypatch.setattr(settings, "pipeline", {"source": "test"})
    monkeypatch.setattr(pipeline, "_analysis_symbols", lambda: symbols)
    _patch_market_inputs(
        monkeypatch,
        {
            "WRITTEN": sufficient_frame,
            "SKIPPED": short_frame,
            "FAILED": sufficient_frame,
        },
    )

    def compute(
        symbol: str,
        _frame: pd.DataFrame,
        _params: dict,
        *,
        requested_lookbacks_by_effective: dict[int, int],
    ) -> tuple[list[dict], list[dict]]:
        if symbol == "FAILED":
            raise RuntimeError("expected test failure")
        assert requested_lookbacks_by_effective == {5: 5}
        return ([{"symbol": symbol}], [{"symbol": symbol}])

    monkeypatch.setattr(pipeline, "compute_adaptive_segmentation_snapshots", compute)
    monkeypatch.setattr(
        pipeline,
        "upsert_trend_segmentation_daily",
        lambda summaries, _segments: written.append(summaries[0]["symbol"]),
    )
    monkeypatch.setattr(pipeline, "Progress", ProgressRecorder)

    completed = pipeline.run_adaptive_segmentation_pipeline(show_progress=True)

    recorder = ProgressRecorder.instance
    assert completed == 1
    assert written == ["WRITTEN"]
    assert recorder is not None
    assert recorder.total == 3
    assert recorder.advanced == 3
    assert recorder.started is True
    assert recorder.stopped is True
    assert recorder.descriptions == [
        "Adaptive segmentation: preparing",
        "Adaptive segmentation: WRITTEN",
        "Adaptive segmentation: SKIPPED",
        "Adaptive segmentation: FAILED",
    ]


def test_resolve_effective_lookbacks_uses_available_bars_and_deduplicates() -> None:
    assert pipeline._resolve_effective_lookbacks([250], 250, 40) == [250]
    assert pipeline._resolve_effective_lookbacks([250], 249, 40) == [249]
    assert pipeline._resolve_effective_lookbacks([40, 250, 300], 250, 40) == [40, 250]
    assert pipeline._resolve_effective_lookbacks([250], 40, 40) == [40]
    assert pipeline._resolve_effective_lookbacks([250], 39, 40) == []
    assert pipeline._resolve_requested_lookbacks([100, 250], 65, 40) == {65: 250}


def test_pipeline_uses_filtered_available_history_for_fallback(monkeypatch) -> None:
    symbol = "RECENT"
    frame = pd.DataFrame(
        {"close": np.linspace(100.0, 120.0, 60)},
        index=pd.bdate_range("2026-01-02", periods=60),
    )
    target_date = frame.index[39].date()
    computed: list[tuple[str, list[int], int]] = []
    written: list[str] = []

    monkeypatch.setattr(
        settings,
        "indicators",
        {
            "adaptive_segmentation": {
                "lookbacks": [250],
                "min_fallback_bars": 40,
            }
        },
    )
    monkeypatch.setattr(settings, "pipeline", {"source": "test"})
    monkeypatch.setattr(pipeline, "_analysis_symbols", lambda: [symbol])
    _patch_market_inputs(monkeypatch, {symbol: frame})

    def compute(
        computed_symbol: str,
        filtered_frame: pd.DataFrame,
        params: dict,
        *,
        requested_lookbacks_by_effective: dict[int, int],
    ) -> tuple[list[dict], list[dict]]:
        computed.append(
            (computed_symbol, list(params["lookbacks"]), len(filtered_frame))
        )
        assert requested_lookbacks_by_effective == {40: 250}
        return ([{"symbol": computed_symbol}], [{"symbol": computed_symbol}])

    monkeypatch.setattr(pipeline, "compute_adaptive_segmentation_snapshots", compute)
    monkeypatch.setattr(
        pipeline,
        "upsert_trend_segmentation_daily",
        lambda summaries, _segments: written.append(summaries[0]["symbol"]),
    )
    logger = MagicMock()
    monkeypatch.setattr(pipeline, "log", logger)

    completed = pipeline.run_adaptive_segmentation_pipeline(target_date)

    assert completed == 1
    assert computed == [(symbol, [40], 40)]
    assert written == [symbol]
    logger.warning.assert_called_once_with(
        "adaptive_segmentation.lookback.fallback",
        symbol=symbol,
        configured=[250],
        available=40,
        effective=[40],
    )
    logger.info.assert_any_call(
        "adaptive_segmentation.done",
        symbols_completed=1,
        symbols_skipped_existing=0,
        snapshots_skipped_existing=0,
        symbols_fallback=1,
        symbols_skipped_insufficient=0,
        symbols_failed=0,
    )


def test_pipeline_lookback_override_replaces_configured_windows(monkeypatch) -> None:
    symbol = "OVERRIDE"
    frame = pd.DataFrame(
        {"close": np.linspace(100.0, 120.0, 150)},
        index=pd.bdate_range("2026-01-02", periods=150),
    )
    computed: list[tuple[list[int], dict[int, int]]] = []

    monkeypatch.setattr(
        settings,
        "indicators",
        {
            "adaptive_segmentation": {
                "lookbacks": [250],
                "min_fallback_bars": 40,
            }
        },
    )
    monkeypatch.setattr(settings, "pipeline", {"source": "test"})
    monkeypatch.setattr(pipeline, "_analysis_symbols", lambda: [symbol])
    _patch_market_inputs(monkeypatch, {symbol: frame})

    def compute(
        _symbol: str,
        _frame: pd.DataFrame,
        params: dict,
        *,
        requested_lookbacks_by_effective: dict[int, int],
    ) -> tuple[list[dict], list[dict]]:
        computed.append((list(params["lookbacks"]), requested_lookbacks_by_effective))
        return ([{"symbol": symbol}], [{"symbol": symbol}])

    monkeypatch.setattr(pipeline, "compute_adaptive_segmentation_snapshots", compute)
    monkeypatch.setattr(
        pipeline,
        "upsert_trend_segmentation_daily",
        lambda _summaries, _segments: None,
    )

    completed = pipeline.run_adaptive_segmentation_pipeline(lookback_bars=120)

    assert completed == 1
    assert computed == [([120], {120: 120})]


def test_pipeline_skips_complete_matching_snapshot(monkeypatch) -> None:
    symbol = "MATCHED"
    frame = pd.DataFrame(
        {"close": np.linspace(100.0, 110.0, 40)},
        index=pd.bdate_range("2026-01-02", periods=40),
    )
    target_date = frame.index[-1].date()
    params = {
        "lookbacks": [250],
        "min_fallback_bars": 40,
        "min_segment_bars": 5,
        "max_segments_cap": 10,
        "bic_penalty_multiplier": 3.0,
    }
    candidate = {
        "symbol": symbol,
        "date": target_date,
        "requested_lookback_bars": 250,
        "lookback_bars": 40,
        "observation_count": 40,
        "segment_count": 2,
        "min_segment_bars": 5,
        "max_segments": 4,
        "bic_penalty_multiplier": 3.0,
        "search_config": pipeline.normalize_search_config({}),
        "method": pipeline.ADAPTIVE_SEGMENTATION_METHOD,
        "calculation_version": pipeline.ADAPTIVE_SEGMENTATION_CALCULATION_VERSION,
        "persisted_segment_count": 2,
        "min_segment_index": 0,
        "max_segment_index": 1,
        "segment_identity_matches": True,
    }

    monkeypatch.setattr(settings, "indicators", {"adaptive_segmentation": params})
    monkeypatch.setattr(settings, "pipeline", {"source": "test"})
    monkeypatch.setattr(pipeline, "_analysis_symbols", lambda: [symbol])
    _patch_market_inputs(monkeypatch, {symbol: frame})
    monkeypatch.setattr(
        pipeline,
        "fetch_adaptive_resume_candidates",
        lambda *_args, **_kwargs: [candidate],
    )
    compute = MagicMock()
    write = MagicMock()
    monkeypatch.setattr(pipeline, "compute_adaptive_segmentation_snapshots", compute)
    monkeypatch.setattr(pipeline, "upsert_trend_segmentation_daily", write)
    logger = MagicMock()
    monkeypatch.setattr(pipeline, "log", logger)

    completed = pipeline.run_adaptive_segmentation_pipeline(target_date)

    assert completed == 0
    compute.assert_not_called()
    write.assert_not_called()
    logger.info.assert_any_call(
        "adaptive_segmentation.symbol.skip.existing",
        symbol=symbol,
        date=str(target_date),
        lookbacks=[40],
    )
    logger.info.assert_any_call(
        "adaptive_segmentation.done",
        symbols_completed=0,
        symbols_skipped_existing=1,
        snapshots_skipped_existing=1,
        symbols_fallback=1,
        symbols_skipped_insufficient=0,
        symbols_failed=0,
    )


def test_pipeline_force_recomputes_matching_snapshot(monkeypatch) -> None:
    symbol = "FORCED"
    frame = pd.DataFrame(
        {"close": np.linspace(100.0, 110.0, 40)},
        index=pd.bdate_range("2026-01-02", periods=40),
    )
    monkeypatch.setattr(
        settings,
        "indicators",
        {"adaptive_segmentation": {"lookbacks": [40], "min_fallback_bars": 40}},
    )
    monkeypatch.setattr(settings, "pipeline", {"source": "test"})
    monkeypatch.setattr(pipeline, "_analysis_symbols", lambda: [symbol])
    _patch_market_inputs(monkeypatch, {symbol: frame})
    resume_fetch = MagicMock()
    monkeypatch.setattr(pipeline, "fetch_adaptive_resume_candidates", resume_fetch)
    monkeypatch.setattr(
        pipeline,
        "compute_adaptive_segmentation_snapshots",
        lambda *_args, **_kwargs: ([{"symbol": symbol}], [{"symbol": symbol}]),
    )
    write = MagicMock()
    monkeypatch.setattr(pipeline, "upsert_trend_segmentation_daily", write)

    completed = pipeline.run_adaptive_segmentation_pipeline(force_recompute=True)

    assert completed == 1
    resume_fetch.assert_not_called()
    write.assert_called_once()


@pytest.mark.parametrize("lookback", [39, 501])
def test_pipeline_rejects_out_of_range_lookback_override(lookback: int) -> None:
    with pytest.raises(ValueError, match="lookback_bars must be between 40 and 500"):
        pipeline.run_adaptive_segmentation_pipeline(lookback_bars=lookback)


def test_pipeline_skips_symbol_below_fallback_minimum(monkeypatch) -> None:
    symbol = "TOO_SHORT"
    frame = pd.DataFrame(
        {"close": np.linspace(100.0, 110.0, 39)},
        index=pd.bdate_range("2026-01-02", periods=39),
    )

    monkeypatch.setattr(
        settings,
        "indicators",
        {
            "adaptive_segmentation": {
                "lookbacks": [250],
                "min_fallback_bars": 40,
            }
        },
    )
    monkeypatch.setattr(settings, "pipeline", {"source": "test"})
    monkeypatch.setattr(pipeline, "_analysis_symbols", lambda: [symbol])
    _patch_market_inputs(monkeypatch, {symbol: frame})
    compute = MagicMock()
    monkeypatch.setattr(pipeline, "compute_adaptive_segmentation_snapshots", compute)
    logger = MagicMock()
    monkeypatch.setattr(pipeline, "log", logger)

    completed = pipeline.run_adaptive_segmentation_pipeline()

    assert completed == 0
    compute.assert_not_called()
    logger.warning.assert_called_once_with(
        "adaptive_segmentation.skip.insufficient_bars",
        symbol=symbol,
        available=39,
        minimum=40,
    )
    logger.info.assert_any_call(
        "adaptive_segmentation.done",
        symbols_completed=0,
        symbols_skipped_existing=0,
        snapshots_skipped_existing=0,
        symbols_fallback=0,
        symbols_skipped_insufficient=1,
        symbols_failed=0,
    )
