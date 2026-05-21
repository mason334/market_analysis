from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import pandas as pd
import structlog

log = structlog.get_logger(__name__)

# Minimum rows needed to run this strategy
_MIN_ROWS = 31  # lookback_days(5) + volume_ma(30) - 1, worst case


def sudden_surge(
    symbol: str,
    df: pd.DataFrame,   # 完整历史 OHLCV，index = date (DatetimeIndex)，已排序
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    近期暴涨策略：最近 lookback_days 交易日内涨幅超过 min_return，
    且成交量是近30日均量的 volume_ratio_min 倍。
    """
    if len(df) < _MIN_ROWS:
        log.debug("sudden_surge.skip.insufficient_data", symbol=symbol, rows=len(df))
        return []

    lookback: int = params.get("lookback_days", 5)
    min_return: float = params.get("min_return", 0.08)
    vol_ratio_min: float = params.get("volume_ratio_min", 1.5)

    latest_date: date = df.index[-1].date()
    window = df.iloc[-lookback:]
    prior = df.iloc[-(lookback + 30): -lookback]

    if len(prior) < 1:
        return []

    period_return = (window["close"].iloc[-1] - window["close"].iloc[0]) / window["close"].iloc[0]
    avg_volume = prior["volume"].mean()
    trigger_day_return = (
        (df["close"].iloc[-1] - df["close"].iloc[-2]) / df["close"].iloc[-2]
        if len(df) >= 2
        else 0.0
    )
    volume_ratio = df["volume"].iloc[-1] / avg_volume if avg_volume > 0 else 0.0

    if period_return < min_return or volume_ratio < vol_ratio_min:
        return []

    signal_type = "bullish"
    detail = {
        "return_5d": round(float(period_return), 4),
        "volume_ratio": round(float(volume_ratio), 2),
        "trigger_day_return": round(float(trigger_day_return), 4),
    }
    signal_id = _make_id(symbol, latest_date, "sudden_surge")

    log.info(
        "sudden_surge.signal",
        symbol=symbol,
        date=str(latest_date),
        **detail,
    )
    return [
        {
            "signal_id": signal_id,
            "symbol": symbol,
            "date": latest_date,
            "strategy": "sudden_surge",
            "signal_type": signal_type,
            "detail_json": detail,
        }
    ]


def _make_id(symbol: str, d: date, strategy: str) -> str:
    raw = f"{symbol}|{d}|{strategy}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]
