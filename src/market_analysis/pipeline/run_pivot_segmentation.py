from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

import structlog
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn

from market_analysis.config import settings
from market_analysis.db.queries import (
    fetch_latest_adaptive_segmentation_date,
    fetch_ohlcv,
    fetch_trend_segment_snapshot,
    fetch_trend_segmentation_snapshot,
    upsert_pivot_segmentation_daily,
)
from market_analysis.indicators.pivot_segmentation import compute_pivot_segmentation

log = structlog.get_logger(__name__)

_SOURCE_CALCULATION_VERSION = "adaptive_segmentation_v3"


def run_pivot_segmentation_pipeline(
    target_date: date | None = None,
    *,
    show_progress: bool = False,
) -> int:
    """Build close-pivot segment snapshots from persisted adaptive segmentation."""
    snapshot_date = target_date or fetch_latest_adaptive_segmentation_date()
    if snapshot_date is None:
        raise ValueError("No adaptive segmentation snapshot is available.")
    adaptive_params: dict[str, Any] = dict(
        settings.indicators.get("adaptive_segmentation", {})
    )
    pivot_params: dict[str, Any] = dict(settings.indicators.get("pivot_refinement", {}))
    configured_lookbacks = {
        int(value) for value in adaptive_params.get("lookbacks", [250])
    }
    classification_params = dict(adaptive_params.get("segment_classification", {}))
    source = str(settings.pipeline.get("source", "tiingo"))

    summaries = [
        row
        for row in fetch_trend_segmentation_snapshot(snapshot_date)
        if row.get("calculation_version") == _SOURCE_CALCULATION_VERSION
        and int(row["lookback_bars"]) in configured_lookbacks
    ]
    if not summaries:
        raise ValueError(
            f"No {_SOURCE_CALCULATION_VERSION} summaries found for {snapshot_date}."
        )
    segments_by_key: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in fetch_trend_segment_snapshot(snapshot_date):
        if row.get("calculation_version") != _SOURCE_CALCULATION_VERSION:
            continue
        key = (str(row["symbol"]), int(row["lookback_bars"]))
        segments_by_key[key].append(row)

    log.info(
        "pivot_segmentation.start",
        date=str(snapshot_date),
        summaries=len(summaries),
        lookbacks=sorted(configured_lookbacks),
    )
    progress = (
        Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.completed:.0f}/{task.total:.0f} snapshots"),
            TimeRemainingColumn(),
        )
        if show_progress
        else None
    )
    progress_task = (
        progress.add_task("Pivot segmentation: preparing", total=len(summaries))
        if progress is not None
        else None
    )

    completed = 0
    if progress is not None:
        progress.start()
    try:
        for source_summary in summaries:
            symbol = str(source_summary["symbol"])
            lookback_bars = int(source_summary["lookback_bars"])
            if progress is not None and progress_task is not None:
                progress.update(
                    progress_task,
                    description=f"Pivot segmentation: {symbol} ({lookback_bars} bars)",
                )
            source_segments = segments_by_key.get((symbol, lookback_bars), [])
            try:
                frame = fetch_ohlcv(symbol, source=source)
                if not frame.empty:
                    frame = frame[frame.index.date <= snapshot_date]
                summary, segments = compute_pivot_segmentation(
                    symbol,
                    frame,
                    source_summary,
                    source_segments,
                    search_radius_bars=int(pivot_params.get("search_radius_bars", 5)),
                    min_segment_bars=int(pivot_params.get("min_segment_bars", 5)),
                    classification_params=classification_params,
                )
                upsert_pivot_segmentation_daily([summary], segments)
            except Exception:
                log.exception(
                    "pivot_segmentation.symbol.error",
                    symbol=symbol,
                    lookback_bars=lookback_bars,
                )
                continue
            finally:
                if progress is not None and progress_task is not None:
                    progress.advance(progress_task)
            completed += 1
            log.info(
                "pivot_segmentation.symbol.done",
                symbol=symbol,
                lookback_bars=lookback_bars,
                pivots=summary["pivot_count"],
                segments=summary["segment_count"],
            )
    finally:
        if progress is not None:
            progress.stop()
    log.info("pivot_segmentation.done", snapshots_completed=completed)
    return completed
