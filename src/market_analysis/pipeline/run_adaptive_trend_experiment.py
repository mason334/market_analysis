from __future__ import annotations

from datetime import date
from typing import Any

import structlog

from market_analysis.config import settings
from market_analysis.db.queries import (
    fetch_constituents_for_ticker,
    fetch_ohlcv,
    fetch_universe_ticker_list,
    upsert_trend_segmentation_daily,
)
from market_analysis.indicators.adaptive_trend import compute_adaptive_trend_experiment

log = structlog.get_logger(__name__)

_ANALYSIS_UNIVERSE_TICKER = "OPTIONS_ACTIVE"


def _analysis_symbols() -> list[str]:
    candidates = fetch_constituents_for_ticker(
        _ANALYSIS_UNIVERSE_TICKER
    ) + fetch_universe_ticker_list()
    return list(dict.fromkeys(candidates))


def run_adaptive_trend_experiment_pipeline(target_date: date | None = None) -> int:
    """Run adaptive segmentation separately from the production fixed-window pipeline."""
    params: dict[str, Any] = dict(settings.indicators.get("adaptive_trend", {}))
    lookbacks = [int(value) for value in params.get("lookbacks", [60])]
    if not lookbacks:
        raise ValueError("adaptive_trend.lookbacks must contain at least one window.")
    source = str(settings.pipeline.get("source", "tiingo"))
    symbols = _analysis_symbols()
    if not symbols:
        raise ValueError("No symbols found for adaptive trend experiment.")

    log.info(
        "adaptive_trend_experiment.start",
        symbols=len(symbols),
        lookbacks=lookbacks,
        date=str(target_date or date.today()),
    )
    completed = 0
    for symbol in symbols:
        df = fetch_ohlcv(symbol, source=source)
        if len(df) < max(lookbacks):
            log.warning(
                "adaptive_trend_experiment.skip.insufficient_bars",
                symbol=symbol,
                bars=len(df),
                required=max(lookbacks),
            )
            continue
        try:
            summaries, segments = compute_adaptive_trend_experiment(symbol, df, params)
            upsert_trend_segmentation_daily(summaries, segments)
        except Exception:
            log.exception("adaptive_trend_experiment.symbol.error", symbol=symbol)
            continue
        completed += 1
        log.info(
            "adaptive_trend_experiment.symbol.done",
            symbol=symbol,
            lookbacks=len(summaries),
            segments=len(segments),
        )

    log.info("adaptive_trend_experiment.done", symbols_completed=completed)
    return completed
