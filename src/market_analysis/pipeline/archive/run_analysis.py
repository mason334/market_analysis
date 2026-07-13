from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import structlog
import yaml

from market_analysis.config import settings
from market_analysis.db.queries import (
    fetch_ohlcv,
    upsert_support_resistance_daily,
    upsert_trend_daily,
)
from market_analysis.indicators.support_resistance import support_resistance
from market_analysis.indicators.trend import compute_trend_indicators

log = structlog.get_logger(__name__)

_USER_PREFS_FILE = Path(__file__).parents[4] / "config" / "user_prefs.yaml"


def _load_sr_params() -> dict[str, Any]:
    """Load SR params: settings.yaml defaults overridden by user_prefs.yaml sr section."""
    base: dict[str, Any] = dict(
        settings.indicators.get(
            "support_resistance",
            settings.strategies.get("support_resistance", {}),
        )
    )
    if _USER_PREFS_FILE.exists():
        with _USER_PREFS_FILE.open(encoding="utf-8") as f:
            prefs = yaml.safe_load(f) or {}
        base.update(prefs.get("sr", {}))
    return base


def _load_trend_params() -> dict[str, Any]:
    return dict(settings.indicators.get("trend", {}))


def load_universe(universe_path: Path) -> list[str]:
    with universe_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if "symbols" in data:
        symbols: list[str] = data["symbols"]
    else:
        symbols = data.get("etfs", []) + data.get("stocks", [])
    if not symbols:
        raise ValueError(f"No symbols found in {universe_path}")
    return symbols


def run_symbol(
    symbol: str,
    sr_params: dict[str, Any],
    trend_params: dict[str, Any],
    source: str = "tiingo",
    min_bars: int = 200,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    df = fetch_ohlcv(symbol, source=source)
    if len(df) < min_bars:
        log.warning(
            "pipeline.skip.insufficient_bars", symbol=symbol, bars=len(df), required=min_bars
        )
        return None, []

    sr_row: dict[str, Any] | None = None
    trend_rows: list[dict[str, Any]] = []
    try:
        sr_row = support_resistance(symbol=symbol, df=df, params=sr_params)
    except Exception:
        log.exception("pipeline.indicator.error", symbol=symbol, indicator="support_resistance")

    try:
        trend_rows = compute_trend_indicators(symbol=symbol, df=df, params=trend_params)
    except Exception:
        log.exception("pipeline.indicator.error", symbol=symbol, indicator="trend")

    return sr_row, trend_rows


def run_pipeline(
    universe_path: Path,
    target_date: date | None = None,
) -> int:
    """Archived YAML-universe indicator pipeline; not used by CLI or dashboard."""
    symbols = load_universe(universe_path)
    sr_params = _load_sr_params()
    trend_params = _load_trend_params()
    pipeline_cfg: dict[str, Any] = settings.pipeline
    source: str = pipeline_cfg.get("source", "tiingo")
    min_bars: int = pipeline_cfg.get("min_bars", 200)

    log.info("pipeline.start", symbols=len(symbols), date=str(target_date or date.today()))

    total = 0
    for symbol in symbols:
        sr_row, trend_rows = run_symbol(
            symbol,
            sr_params,
            trend_params,
            source=source,
            min_bars=min_bars,
        )
        if sr_row is not None:
            upsert_support_resistance_daily(sr_row)
            upsert_trend_daily(trend_rows)
            total += 1
            log.info("pipeline.symbol.done", symbol=symbol, sr_status=sr_row.get("sr_status"))
        else:
            log.info("pipeline.symbol.skipped", symbol=symbol)

    log.info("pipeline.done", total_upserted=total)
    return total
