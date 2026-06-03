from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import structlog
import yaml

from market_analysis.config import settings
from market_analysis.db.queries import fetch_ohlcv, upsert_indicators_daily
from market_analysis.strategies import STRATEGIES

log = structlog.get_logger(__name__)

_USER_PREFS_FILE = Path(__file__).parent.parent.parent.parent / "config" / "user_prefs.yaml"


def _load_sr_params() -> dict[str, Any]:
    """Load SR params: settings.yaml defaults overridden by user_prefs.yaml sr section."""
    base: dict[str, Any] = dict(settings.strategies.get("support_resistance", {}))
    if _USER_PREFS_FILE.exists():
        with _USER_PREFS_FILE.open(encoding="utf-8") as f:
            prefs = yaml.safe_load(f) or {}
        base.update(prefs.get("sr", {}))
    return base


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
) -> dict[str, Any] | None:
    df = fetch_ohlcv(symbol, source=source)
    if len(df) < min_bars:
        log.warning(
            "pipeline.skip.insufficient_bars", symbol=symbol, bars=len(df), required=min_bars
        )
        return None

    for name, fn in STRATEGIES.items():
        params = strategy_params.get(name, {})
        try:
            row = fn(symbol=symbol, df=df, params=params)
            if row is not None:
                return row
        except Exception:
            log.exception("pipeline.strategy.error", symbol=symbol, strategy=name)

    return None


def run_pipeline(
    universe_path: Path,
    target_date: date | None = None,
) -> int:
    symbols = load_universe(universe_path)
    sr_params = _load_sr_params()
    strategy_params: dict[str, Any] = {"support_resistance": sr_params}
    pipeline_cfg: dict[str, Any] = settings.pipeline
    source: str = pipeline_cfg.get("source", "tiingo")
    min_bars: int = pipeline_cfg.get("min_bars", 200)

    log.info("pipeline.start", symbols=len(symbols), date=str(target_date or date.today()))

    total = 0
    for symbol in symbols:
        row = run_symbol(symbol, strategy_params, source=source, min_bars=min_bars)
        if row is not None:
            upsert_indicators_daily(row)
            total += 1
            log.info("pipeline.symbol.done", symbol=symbol, sr_status=row.get("sr_status"))
        else:
            log.info("pipeline.symbol.skipped", symbol=symbol)

    log.info("pipeline.done", total_upserted=total)
    return total
