"""批量生成并持久化自适应初分段快照。

本模块确定生产标的与有效回看窗口，检查已持久化结果以支持断点续跑，调用纯分段算法，
并将完整的摘要和分段明细写入数据库；同时负责窗口回退、强制重算、日志和进度展示。
"""

from __future__ import annotations

from datetime import date
from math import isclose
from time import monotonic
from typing import Any

import structlog
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn

from market_analysis.config import settings
from market_analysis.db.queries import (
    fetch_adaptive_close_window,
    fetch_adaptive_input_metadata,
    fetch_adaptive_resume_candidates,
    fetch_segmentation_analysis_symbols,
    upsert_trend_segmentation_daily,
)
from market_analysis.pipeline._progress import progress_log_fields
from market_analysis.segmentation.adaptive_segmentation import (
    ADAPTIVE_SEGMENTATION_CALCULATION_VERSION,
    ADAPTIVE_SEGMENTATION_METHOD,
    compute_adaptive_segmentation_snapshots,
    normalize_search_config,
    recommended_max_segments,
)

log = structlog.get_logger(__name__)

_DEFAULT_MIN_FALLBACK_BARS = 40
_MAX_REQUESTED_LOOKBACK_BARS = 500


def _analysis_symbols() -> list[str]:
    return fetch_segmentation_analysis_symbols()


def _resolve_effective_lookbacks(
    configured_lookbacks: list[int],
    available_bars: int,
    min_fallback_bars: int,
) -> list[int]:
    """Resolve configured windows against one symbol's available history."""
    return list(
        _resolve_requested_lookbacks(
            configured_lookbacks,
            available_bars,
            min_fallback_bars,
        )
    )


def _resolve_requested_lookbacks(
    configured_lookbacks: list[int],
    available_bars: int,
    min_fallback_bars: int,
) -> dict[int, int]:
    """Map each effective window to the largest configured window that produced it."""
    if available_bars < min_fallback_bars:
        return {}
    requested_by_effective: dict[int, int] = {}
    for requested_lookback in configured_lookbacks:
        effective_lookback = min(requested_lookback, available_bars)
        requested_by_effective[effective_lookback] = max(
            requested_lookback,
            requested_by_effective.get(effective_lookback, requested_lookback),
        )
    return requested_by_effective


def _expected_max_segments(lookback_bars: int, params: dict[str, Any]) -> int:
    fixed_max_segments = params.get("max_segments")
    if fixed_max_segments is not None:
        return int(fixed_max_segments)
    return recommended_max_segments(
        lookback_bars,
        int(params.get("max_segments_cap", 10)),
    )


def _resume_candidate_matches(
    candidate: dict[str, Any] | None,
    *,
    requested_lookback_bars: int,
    effective_lookback_bars: int,
    params: dict[str, Any],
    expected_search_config: dict[str, Any],
) -> bool:
    """Check persisted parameters and segment integrity without recomputing."""
    if candidate is None:
        return False
    segment_count = int(candidate["segment_count"])
    return (
        int(candidate["requested_lookback_bars"]) == requested_lookback_bars
        and int(candidate["lookback_bars"]) == effective_lookback_bars
        and int(candidate["observation_count"]) == effective_lookback_bars
        and int(candidate["min_segment_bars"])
        == int(params.get("min_segment_bars", 5))
        and int(candidate["max_segments"])
        == _expected_max_segments(effective_lookback_bars, params)
        and isclose(
            float(candidate["bic_penalty_multiplier"]),
            float(params.get("bic_penalty_multiplier", 3.0)),
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        and candidate["search_config"] == expected_search_config
        and str(candidate["method"]) == ADAPTIVE_SEGMENTATION_METHOD
        and str(candidate["calculation_version"])
        == ADAPTIVE_SEGMENTATION_CALCULATION_VERSION
        and int(candidate["persisted_segment_count"]) == segment_count
        and candidate["min_segment_index"] == 0
        and candidate["max_segment_index"] == segment_count - 1
        and bool(candidate["segment_identity_matches"])
    )


def run_adaptive_segmentation_pipeline(
    target_date: date | None = None,
    *,
    lookback_bars: int | None = None,
    force_recompute: bool = False,
    show_progress: bool = False,
) -> int:
    """Run adaptive segmentation separately from the fixed-window indicator pipeline."""
    params: dict[str, Any] = dict(settings.indicators.get("adaptive_segmentation", {}))
    if lookback_bars is not None and not (
        _DEFAULT_MIN_FALLBACK_BARS
        <= lookback_bars
        <= _MAX_REQUESTED_LOOKBACK_BARS
    ):
        raise ValueError(
            "lookback_bars must be between "
            f"{_DEFAULT_MIN_FALLBACK_BARS} and {_MAX_REQUESTED_LOOKBACK_BARS}."
        )
    lookbacks = (
        [int(lookback_bars)]
        if lookback_bars is not None
        else [int(value) for value in params.get("lookbacks", [250])]
    )
    if not lookbacks:
        raise ValueError("adaptive_segmentation.lookbacks must contain at least one window.")
    min_fallback_bars = int(
        params.get("min_fallback_bars", _DEFAULT_MIN_FALLBACK_BARS)
    )
    if min_fallback_bars < 2:
        raise ValueError("adaptive_segmentation.min_fallback_bars must be at least 2.")
    source = str(settings.pipeline.get("source", "tiingo"))
    symbols = _analysis_symbols()
    if not symbols:
        raise ValueError("No symbols found for adaptive trend experiment.")
    input_metadata = fetch_adaptive_input_metadata(
        symbols,
        source=source,
        target_date=target_date,
        max_lookback_bars=max(lookbacks),
    )
    expected_search_config = normalize_search_config(dict(params.get("search", {})))
    resume_candidates = (
        []
        if force_recompute
        else fetch_adaptive_resume_candidates(
            symbols,
            lookbacks,
            target_date=target_date,
            calculation_version=ADAPTIVE_SEGMENTATION_CALCULATION_VERSION,
        )
    )
    candidates_by_key = {
        (
            str(row["symbol"]),
            row["date"],
            int(row["requested_lookback_bars"]),
            int(row["lookback_bars"]),
        ): row
        for row in resume_candidates
    }

    log.info(
        "adaptive_segmentation.start",
        symbols=len(symbols),
        lookbacks=lookbacks,
        min_fallback_bars=min_fallback_bars,
        date=str(target_date or date.today()),
        force_recompute=force_recompute,
        resume_candidates=len(resume_candidates),
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
    skipped_existing_symbols = 0
    skipped_existing_snapshots = 0
    fallback_symbols = 0
    insufficient_symbols = 0
    failed_symbols = 0
    progress_started_at = monotonic()
    if progress is not None:
        progress.start()
    try:
        for processed, symbol in enumerate(symbols, start=1):
            if progress is not None and progress_task is not None:
                progress.update(
                    progress_task,
                    description=f"Adaptive segmentation: {symbol}",
                )
            try:
                snapshot_date, available_bars = input_metadata.get(symbol, (None, 0))
                requested_by_effective = _resolve_requested_lookbacks(
                    lookbacks,
                    available_bars,
                    min_fallback_bars,
                )
                effective_lookbacks = list(requested_by_effective)
                if not effective_lookbacks:
                    insufficient_symbols += 1
                    log.warning(
                        "adaptive_segmentation.skip.insufficient_bars",
                        symbol=symbol,
                        available=available_bars,
                        minimum=min_fallback_bars,
                        **progress_log_fields(
                            processed,
                            len(symbols),
                            monotonic() - progress_started_at,
                        ),
                    )
                    continue
                if effective_lookbacks != lookbacks:
                    fallback_symbols += 1
                    log.warning(
                        "adaptive_segmentation.lookback.fallback",
                        symbol=symbol,
                        configured=lookbacks,
                        available=available_bars,
                        effective=effective_lookbacks,
                    )
                if not force_recompute and snapshot_date is not None and all(
                    _resume_candidate_matches(
                        candidates_by_key.get(
                            (
                                symbol,
                                snapshot_date,
                                requested_lookback,
                                effective_lookback,
                            )
                        ),
                        requested_lookback_bars=requested_lookback,
                        effective_lookback_bars=effective_lookback,
                        params=params,
                        expected_search_config=expected_search_config,
                    )
                    for effective_lookback, requested_lookback in (
                        requested_by_effective.items()
                    )
                ):
                    skipped_existing_symbols += 1
                    skipped_existing_snapshots += len(effective_lookbacks)
                    log.info(
                        "adaptive_segmentation.symbol.skip.existing",
                        symbol=symbol,
                        date=str(snapshot_date),
                        lookbacks=effective_lookbacks,
                        **progress_log_fields(
                            processed,
                            len(symbols),
                            monotonic() - progress_started_at,
                        ),
                    )
                    continue
                df = fetch_adaptive_close_window(
                    symbol,
                    source=source,
                    target_date=target_date,
                    observation_count=max(effective_lookbacks),
                )
                symbol_params = {**params, "lookbacks": effective_lookbacks}
                try:
                    summaries, segments = compute_adaptive_segmentation_snapshots(
                        symbol,
                        df,
                        symbol_params,
                        requested_lookbacks_by_effective=requested_by_effective,
                    )
                    upsert_trend_segmentation_daily(summaries, segments)
                except Exception:
                    failed_symbols += 1
                    log.exception(
                        "adaptive_segmentation.symbol.error",
                        symbol=symbol,
                        **progress_log_fields(
                            processed,
                            len(symbols),
                            monotonic() - progress_started_at,
                        ),
                    )
                    continue
                completed += 1
                log.info(
                    "adaptive_segmentation.symbol.done",
                    symbol=symbol,
                    lookbacks=len(summaries),
                    segments=len(segments),
                    **progress_log_fields(
                        processed,
                        len(symbols),
                        monotonic() - progress_started_at,
                    ),
                )
            finally:
                if progress is not None and progress_task is not None:
                    progress.advance(progress_task)
    finally:
        if progress is not None:
            progress.stop()

    log.info(
        "adaptive_segmentation.done",
        symbols_completed=completed,
        symbols_skipped_existing=skipped_existing_symbols,
        snapshots_skipped_existing=skipped_existing_snapshots,
        symbols_fallback=fallback_symbols,
        symbols_skipped_insufficient=insufficient_symbols,
        symbols_failed=failed_symbols,
    )
    return completed
