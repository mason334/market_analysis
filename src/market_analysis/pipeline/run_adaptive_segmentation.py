from __future__ import annotations

from datetime import date
from typing import Any

import structlog
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn

from market_analysis.config import settings
from market_analysis.db.queries import (
    fetch_constituents_for_ticker,
    fetch_ohlcv,
    fetch_universe_ticker_list,
    upsert_trend_segmentation_daily,
)
from market_analysis.indicators.adaptive_segmentation import (
    compute_adaptive_segmentation_snapshots,
)

log = structlog.get_logger(__name__)

_ANALYSIS_UNIVERSE_TICKER = "OPTIONS_ACTIVE"


def _analysis_symbols() -> list[str]:
    candidates = fetch_constituents_for_ticker(
        _ANALYSIS_UNIVERSE_TICKER
    ) + fetch_universe_ticker_list()
    return list(dict.fromkeys(candidates))


def run_adaptive_segmentation_pipeline(
    target_date: date | None = None,
    *,
    show_progress: bool = False,
) -> int:
    """Run adaptive segmentation separately from the fixed-window indicator pipeline."""
    params: dict[str, Any] = dict(settings.indicators.get("adaptive_segmentation", {}))
    lookbacks = [int(value) for value in params.get("lookbacks", [250])]
    if not lookbacks:
        raise ValueError("adaptive_segmentation.lookbacks must contain at least one window.")
    source = str(settings.pipeline.get("source", "tiingo"))
    symbols = _analysis_symbols()
    if not symbols:
        raise ValueError("No symbols found for adaptive trend experiment.")

    log.info(
        "adaptive_segmentation.start",
        symbols=len(symbols),
        lookbacks=lookbacks,
        date=str(target_date or date.today()),
    )
    progress = (
        Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.completed:.0f}/{task.total:.0f} symbols"),
            TimeRemainingColumn(),
        )
        if show_progress
        else None
    )
    progress_task = (
        progress.add_task("Adaptive segmentation: preparing", total=len(symbols))
        if progress is not None
        else None
    )

    completed = 0
    if progress is not None:
        progress.start()
    try:
        for symbol in symbols:
            if progress is not None and progress_task is not None:
                progress.update(
                    progress_task,
                    description=f"Adaptive segmentation: {symbol}",
                )
            try:
                df = fetch_ohlcv(symbol, source=source)
                if target_date is not None and not df.empty:
                    df = df[df.index.date <= target_date]
                if len(df) < max(lookbacks):
                    log.warning(
                        "adaptive_segmentation.skip.insufficient_bars",
                        symbol=symbol,
                        bars=len(df),
                        required=max(lookbacks),
                    )
                    continue
                try:
                    summaries, segments = compute_adaptive_segmentation_snapshots(
                        symbol, df, params
                    )
                    upsert_trend_segmentation_daily(summaries, segments)
                except Exception:
                    log.exception("adaptive_segmentation.symbol.error", symbol=symbol)
                    continue
                completed += 1
                log.info(
                    "adaptive_segmentation.symbol.done",
                    symbol=symbol,
                    lookbacks=len(summaries),
                    segments=len(segments),
                )
            finally:
                if progress is not None and progress_task is not None:
                    progress.advance(progress_task)
    finally:
        if progress is not None:
            progress.stop()

    log.info("adaptive_segmentation.done", symbols_completed=completed)
    return completed
