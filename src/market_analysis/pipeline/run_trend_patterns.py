from __future__ import annotations

from datetime import date
from typing import Any

import structlog

from market_analysis.config import settings
from market_analysis.db.queries import (
    fetch_latest_trend_segmentation_date,
    fetch_trend_segment_snapshot,
    fetch_trend_segmentation_snapshot,
    upsert_trend_pattern_daily,
)
from market_analysis.indicators.trend_pattern import (
    classify_trend_patterns,
    summarize_pattern_quality,
)

log = structlog.get_logger(__name__)


def run_trend_pattern_pipeline(target_date: date | None = None) -> int:
    """Classify persisted adaptive segments and log distribution quality checks."""
    snapshot_date = target_date or fetch_latest_trend_segmentation_date()
    if snapshot_date is None:
        raise ValueError("No adaptive trend segmentation snapshot is available.")

    summaries = fetch_trend_segmentation_snapshot(snapshot_date)
    segments = fetch_trend_segment_snapshot(snapshot_date)
    if not summaries:
        raise ValueError(f"No adaptive trend segmentation rows found for {snapshot_date}.")

    params: dict[str, Any] = dict(settings.indicators.get("trend_pattern", {}))
    patterns = classify_trend_patterns(summaries, segments, params)
    upsert_trend_pattern_daily(patterns)

    for quality in summarize_pattern_quality(patterns):
        log.info("trend_pattern.quality", date=str(snapshot_date), **quality)
    log.info("trend_pattern.done", date=str(snapshot_date), rows=len(patterns))
    return len(patterns)
