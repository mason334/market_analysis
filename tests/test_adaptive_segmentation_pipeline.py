from __future__ import annotations

import numpy as np
import pandas as pd

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


def test_progress_advances_for_written_skipped_and_failed_symbols(monkeypatch) -> None:
    symbols = ["WRITTEN", "SKIPPED", "FAILED"]
    sufficient_frame = pd.DataFrame(
        {"close": np.linspace(100.0, 120.0, 5)},
        index=pd.bdate_range("2026-01-02", periods=5),
    )
    short_frame = sufficient_frame.iloc[:2]
    written: list[str] = []

    monkeypatch.setattr(settings, "indicators", {"adaptive_segmentation": {"lookbacks": [5]}})
    monkeypatch.setattr(settings, "pipeline", {"source": "test"})
    monkeypatch.setattr(pipeline, "_analysis_symbols", lambda: symbols)
    monkeypatch.setattr(
        pipeline,
        "fetch_ohlcv",
        lambda symbol, source="": short_frame if symbol == "SKIPPED" else sufficient_frame,
    )

    def compute(symbol: str, _frame: pd.DataFrame, _params: dict) -> tuple[list[dict], list[dict]]:
        if symbol == "FAILED":
            raise RuntimeError("expected test failure")
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
