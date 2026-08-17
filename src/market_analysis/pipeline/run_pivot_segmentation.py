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
    classification_params = dict(adaptive_params.get("segment_classification", {}))
    source = str(settings.pipeline.get("source", "tiingo"))

    summaries = [
        row
        for row in fetch_trend_segmentation_snapshot(snapshot_date)
        if row.get("calculation_version") == _SOURCE_CALCULATION_VERSION
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
    summaries_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in summaries:
        summaries_by_symbol[str(row["symbol"])].append(row)

    log.info(
        "pivot_segmentation.start",
        date=str(snapshot_date),
        summaries=len(summaries),
        lookbacks=sorted({int(row["lookback_bars"]) for row in summaries}),
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

    snapshots_written = 0
    symbols_completed = 0
    symbols_failed = 0
    lookbacks_failed = 0
    snapshots_discarded = 0
    if progress is not None:
        progress.start()
    try:
        for symbol, symbol_summaries in summaries_by_symbol.items():
            ordered_summaries = sorted(
                symbol_summaries,
                key=lambda row: int(row["lookback_bars"]),
            )
            lookbacks = [int(row["lookback_bars"]) for row in ordered_summaries]
            try:
                frame = fetch_ohlcv(symbol, source=source)
                if not frame.empty:
                    frame = frame[frame.index.date <= snapshot_date]
            except Exception:
                symbols_failed += 1
                lookbacks_failed += len(ordered_summaries)
                log.exception(
                    "pivot_segmentation.symbol.error",
                    symbol=symbol,
                    lookbacks=lookbacks,
                    stage="fetch_ohlcv",
                )
                if progress is not None and progress_task is not None:
                    for _ in ordered_summaries:
                        progress.advance(progress_task)
                continue

            computed_summaries: list[dict[str, Any]] = []
            computed_segments: list[dict[str, Any]] = []
            failed_lookback: int | None = None
            for source_summary in ordered_summaries:
                lookback_bars = int(source_summary["lookback_bars"])
                if progress is not None and progress_task is not None:
                    progress.update(
                        progress_task,
                        description=(
                            f"Pivot segmentation: {symbol} ({lookback_bars} bars)"
                        ),
                    )
                source_segments = segments_by_key.get((symbol, lookback_bars), [])
                try:
                    summary, segments = compute_pivot_segmentation(
                        symbol,
                        frame,
                        source_summary,
                        source_segments,
                        search_radius_bars=int(
                            pivot_params.get("search_radius_bars", 5)
                        ),
                        min_segment_bars=int(pivot_params.get("min_segment_bars", 5)),
                        classification_params=classification_params,
                    )
                except Exception:
                    failed_lookback = lookback_bars
                    lookbacks_failed += 1
                    log.exception(
                        "pivot_segmentation.lookback.error",
                        symbol=symbol,
                        lookback_bars=lookback_bars,
                    )
                else:
                    computed_summaries.append(summary)
                    computed_segments.extend(segments)
                    log.info(
                        "pivot_segmentation.lookback.computed",
                        symbol=symbol,
                        lookback_bars=lookback_bars,
                        pivots=summary["pivot_count"],
                        segments=summary["segment_count"],
                    )
                finally:
                    if progress is not None and progress_task is not None:
                        progress.advance(progress_task)
                if failed_lookback is not None:
                    break

            if failed_lookback is not None:
                symbols_failed += 1
                snapshots_discarded += len(computed_summaries)
                unprocessed_count = len(ordered_summaries) - len(computed_summaries) - 1
                if progress is not None and progress_task is not None:
                    for _ in range(unprocessed_count):
                        progress.advance(progress_task)
                log.warning(
                    "pivot_segmentation.symbol.discarded",
                    symbol=symbol,
                    failed_lookback=failed_lookback,
                    computed_lookbacks=[
                        int(row["lookback_bars"]) for row in computed_summaries
                    ],
                )
                continue

            try:
                upsert_pivot_segmentation_daily(
                    computed_summaries,
                    computed_segments,
                )
            except Exception:
                symbols_failed += 1
                snapshots_discarded += len(computed_summaries)
                log.exception(
                    "pivot_segmentation.symbol.error",
                    symbol=symbol,
                    lookbacks=lookbacks,
                    stage="upsert",
                )
                continue

            symbols_completed += 1
            snapshots_written += len(computed_summaries)
            log.info(
                "pivot_segmentation.symbol.done",
                symbol=symbol,
                lookbacks=lookbacks,
                snapshots=len(computed_summaries),
                segments=len(computed_segments),
            )
    finally:
        if progress is not None:
            progress.stop()
    log.info(
        "pivot_segmentation.done",
        symbols_completed=symbols_completed,
        symbols_failed=symbols_failed,
        snapshots_written=snapshots_written,
        lookbacks_failed=lookbacks_failed,
        snapshots_discarded=snapshots_discarded,
    )
    return snapshots_written
