from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import structlog
import yaml

from market_analysis.config import settings
from market_analysis.db.queries import fetch_ohlcv, upsert_signals
from market_analysis.strategies import STRATEGIES

log = structlog.get_logger(__name__)


def load_universe(universe_path: Path) -> list[str]:
    with universe_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    symbols: list[str] = data.get("symbols", [])
    if not symbols:
        raise ValueError(f"No symbols found in {universe_path}")
    return symbols


def run_symbol(
    symbol: str,
    strategy_params: dict[str, Any],
    source: str = "tiingo",
    min_bars: int = 200,
) -> list[dict[str, Any]]:
    df = fetch_ohlcv(symbol, source=source)
    if len(df) < min_bars:
        log.warning(
            "pipeline.skip.insufficient_bars", symbol=symbol, bars=len(df), required=min_bars
        )
        return []

    all_signals: list[dict[str, Any]] = []
    for name, fn in STRATEGIES.items():
        params = strategy_params.get(name, {})
        try:
            signals = fn(symbol=symbol, df=df, params=params)
            all_signals.extend(signals)
        except Exception:
            log.exception("pipeline.strategy.error", symbol=symbol, strategy=name)

    return all_signals


def run_pipeline(
    universe_path: Path,
    target_date: date | None = None,
) -> int:
    symbols = load_universe(universe_path)
    strategy_params: dict[str, Any] = settings.strategies
    pipeline_cfg: dict[str, Any] = settings.pipeline
    source: str = pipeline_cfg.get("source", "tiingo")
    min_bars: int = pipeline_cfg.get("min_bars", 200)

    log.info("pipeline.start", symbols=len(symbols), date=str(target_date or date.today()))

    total_signals = 0
    for symbol in symbols:
        signals = run_symbol(symbol, strategy_params, source=source, min_bars=min_bars)
        if signals:
            upsert_signals(signals)
            total_signals += len(signals)
        log.info("pipeline.symbol.done", symbol=symbol, signals=len(signals))

    log.info("pipeline.done", total_signals=total_signals)
    return total_signals
