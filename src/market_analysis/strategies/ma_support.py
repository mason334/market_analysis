from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import pandas as pd
import structlog

log = structlog.get_logger(__name__)


def ma_support(
    symbol: str,
    df: pd.DataFrame,   # 完整历史 OHLCV，index = date (DatetimeIndex)，已排序
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    均线支撑/压力策略：当收盘价距某均线 ≤ proximity_pct，触发信号。
    - 价格在均线上方且接近：support（支撑）
    - 价格在均线下方且接近：resistance（压力）
    """
    periods: list[int] = params.get("periods", [20, 50, 200])
    proximity_pct: float = params.get("proximity_pct", 0.02)
    directions: list[str] = params.get("directions", ["support", "resistance"])

    latest_date: date = df.index[-1].date()
    close: float = float(df["close"].iloc[-1])

    signals: list[dict[str, Any]] = []

    for period in periods:
        if len(df) < period:
            continue

        ma_value = float(df["close"].iloc[-period:].mean())
        dist_pct = abs(close - ma_value) / ma_value

        if dist_pct > proximity_pct:
            continue

        direction = "support" if close >= ma_value else "resistance"
        if direction not in directions:
            continue

        signal_type = "bullish" if direction == "support" else "bearish"
        detail = {
            "ma_period": period,
            "ma_value": round(ma_value, 4),
            "close": round(close, 4),
            "proximity_pct": round(dist_pct, 4),
            "direction": direction,
        }
        signal_id = _make_id(symbol, latest_date, f"ma_support_{period}")

        log.info(
            "ma_support.signal",
            symbol=symbol,
            date=str(latest_date),
            **detail,
        )
        signals.append(
            {
                "signal_id": signal_id,
                "symbol": symbol,
                "date": latest_date,
                "strategy": "ma_support",
                "signal_type": signal_type,
                "detail_json": detail,
            }
        )

    # Keep only the closest MA when multiple periods trigger
    if len(signals) > 1:
        signals.sort(key=lambda s: s["detail_json"]["proximity_pct"])
        signals = [signals[0]]

    return signals


def _make_id(symbol: str, d: date, strategy: str) -> str:
    raw = f"{symbol}|{d}|{strategy}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]
