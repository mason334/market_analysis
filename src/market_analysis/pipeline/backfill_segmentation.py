"""按交易日幂等回填自适应与 Pivot 分段快照。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import structlog

from market_analysis.config import settings
from market_analysis.db.queries import (
    fetch_adaptive_input_metadata,
    fetch_segmentation_analysis_symbols,
    fetch_segmentation_backfill_counts,
    fetch_source_trading_dates,
)
from market_analysis.pipeline.run_adaptive_segmentation import (
    run_adaptive_segmentation_pipeline,
)
from market_analysis.pipeline.run_pivot_segmentation import run_pivot_segmentation_pipeline
from market_analysis.segmentation.adaptive_segmentation import (
    ADAPTIVE_SEGMENTATION_CALCULATION_VERSION,
)
from market_analysis.segmentation.pivot_segmentation import (
    PIVOT_SEGMENTATION_CALCULATION_VERSION,
)

log = structlog.get_logger(__name__)

BACKFILL_LOOKBACKS: tuple[int, ...] = (250, 125)


@dataclass(frozen=True)
class BackfillResult:
    trading_dates: int
    adaptive_dates_run: int
    pivot_windows_run: int
    pivot_windows_skipped: int


def _requested_lookback_counts(
    metadata: dict[str, tuple[date | None, int]],
    *,
    target_date: date,
    lookbacks: tuple[int, ...],
    min_fallback_bars: int,
) -> dict[int, int]:
    counts = {lookback: 0 for lookback in lookbacks}
    for snapshot_date, available_bars in metadata.values():
        if snapshot_date != target_date or available_bars < min_fallback_bars:
            continue
        requested_by_effective: dict[int, int] = {}
        for requested in lookbacks:
            effective = min(requested, available_bars)
            requested_by_effective[effective] = max(
                requested,
                requested_by_effective.get(effective, requested),
            )
        for requested in requested_by_effective.values():
            counts[requested] += 1
    return counts


def run_segmentation_backfill(
    start_date: date,
    end_date: date,
    *,
    calendar_symbol: str = "SPY",
    show_progress: bool = True,
) -> BackfillResult:
    """Backfill configured 125/250-bar v4 snapshots without replacing complete dates."""
    if end_date < start_date:
        raise ValueError("end_date must not be earlier than start_date.")

    params = dict(settings.indicators.get("adaptive_segmentation", {}))
    configured = tuple(int(value) for value in params.get("lookbacks", []))
    if set(configured) != set(BACKFILL_LOOKBACKS):
        raise ValueError(
            "adaptive_segmentation.lookbacks must contain exactly 125 and 250 for this backfill."
        )
    min_fallback_bars = int(params.get("min_fallback_bars", 40))
    source = str(settings.pipeline.get("source", "tiingo"))
    symbols = fetch_segmentation_analysis_symbols()
    if not symbols:
        raise ValueError("No symbols found for segmentation backfill.")

    trading_dates = fetch_source_trading_dates(
        start_date,
        end_date,
        source=source,
        calendar_symbol=calendar_symbol,
    )
    if not trading_dates:
        raise ValueError("No source trading dates found in the requested range.")

    adaptive_dates_run = 0
    pivot_windows_run = 0
    pivot_windows_skipped = 0

    for position, target_date in enumerate(trading_dates, start=1):
        metadata = fetch_adaptive_input_metadata(
            symbols,
            source=source,
            target_date=target_date,
            max_lookback_bars=max(BACKFILL_LOOKBACKS),
        )
        expected_counts = _requested_lookback_counts(
            metadata,
            target_date=target_date,
            lookbacks=BACKFILL_LOOKBACKS,
            min_fallback_bars=min_fallback_bars,
        )
        before_counts = {
            lookback: fetch_segmentation_backfill_counts(
                target_date,
                lookback,
                source=source,
                adaptive_calculation_version=ADAPTIVE_SEGMENTATION_CALCULATION_VERSION,
                pivot_calculation_version=PIVOT_SEGMENTATION_CALCULATION_VERSION,
            )
            for lookback in BACKFILL_LOOKBACKS
        }
        adaptive_complete = all(
            before_counts[lookback][0] == expected_counts[lookback]
            for lookback in BACKFILL_LOOKBACKS
        )
        log.info(
            "segmentation_backfill.date.start",
            date=str(target_date),
            date_index=position,
            date_total=len(trading_dates),
            expected=expected_counts,
            existing=before_counts,
            adaptive_complete=adaptive_complete,
        )

        if not adaptive_complete:
            run_adaptive_segmentation_pipeline(
                target_date,
                show_progress=show_progress,
            )
            adaptive_dates_run += 1

        for lookback in BACKFILL_LOOKBACKS:
            expected = expected_counts[lookback]
            adaptive_count, pivot_count = fetch_segmentation_backfill_counts(
                target_date,
                lookback,
                source=source,
                adaptive_calculation_version=ADAPTIVE_SEGMENTATION_CALCULATION_VERSION,
                pivot_calculation_version=PIVOT_SEGMENTATION_CALCULATION_VERSION,
            )
            if adaptive_count != expected:
                raise RuntimeError(
                    f"Adaptive backfill incomplete for {target_date} / {lookback}: "
                    f"expected {expected}, found {adaptive_count}."
                )
            if expected == 0 or pivot_count == adaptive_count:
                pivot_windows_skipped += 1
                log.info(
                    "segmentation_backfill.pivot.skip",
                    date=str(target_date),
                    lookback=lookback,
                    snapshots=pivot_count,
                )
                continue

            run_pivot_segmentation_pipeline(
                target_date,
                requested_lookback_bars=lookback,
                show_progress=show_progress,
            )
            pivot_windows_run += 1
            adaptive_count, pivot_count = fetch_segmentation_backfill_counts(
                target_date,
                lookback,
                source=source,
                adaptive_calculation_version=ADAPTIVE_SEGMENTATION_CALCULATION_VERSION,
                pivot_calculation_version=PIVOT_SEGMENTATION_CALCULATION_VERSION,
            )
            if pivot_count != adaptive_count:
                raise RuntimeError(
                    f"Pivot backfill incomplete for {target_date} / {lookback}: "
                    f"expected {adaptive_count}, found {pivot_count}."
                )

        log.info("segmentation_backfill.date.done", date=str(target_date))

    return BackfillResult(
        trading_dates=len(trading_dates),
        adaptive_dates_run=adaptive_dates_run,
        pivot_windows_run=pivot_windows_run,
        pivot_windows_skipped=pivot_windows_skipped,
    )
