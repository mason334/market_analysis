from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import pandas as pd
import structlog

from market_analysis.models import IndicatorSnapshot

log = structlog.get_logger(__name__)


def ma_support(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[list[IndicatorSnapshot], list[dict[str, Any]]]:
    """
    均线支撑/压力策略：当收盘价距某均线 ≤ proximity_pct，触发信号。
    - 价格在均线上方且接近：support（支撑）
    - 价格在均线下方且接近：resistance（压力）

    Returns (snapshots, signals).
    Snapshots: ma_dist_<period> for each computable period — always written.
    Signals: only when proximity threshold met (closest MA only).
    """
    periods: list[int] = params.get("periods", [20, 50, 200])
    proximity_pct: float = params.get("proximity_pct", 0.02)
    directions: list[str] = params.get("directions", ["support", "resistance"])

    latest_date: date = df.index[-1].date()
    close: float = float(df["close"].iloc[-1])

    snapshots: list[IndicatorSnapshot] = []
    candidates: list[dict[str, Any]] = []

    for period in periods:
        if len(df) < period:
            continue

        ma_value = float(df["close"].iloc[-period:].mean())
        dist_pct = abs(close - ma_value) / ma_value

        snapshots.append(
            IndicatorSnapshot(symbol, latest_date, f"ma_dist_{period}", round(dist_pct, 6))
        )

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
        candidates.append(
            {
                "signal_id": _make_id(symbol, latest_date, f"ma_support_{period}"),
                "symbol": symbol,
                "date": latest_date,
                "strategy": "ma_support",
                "signal_type": signal_type,
                "detail_json": detail,
            }
        )

    # Keep only the closest MA when multiple periods trigger
    signals: list[dict[str, Any]] = []
    if candidates:
        candidates.sort(key=lambda s: s["detail_json"]["proximity_pct"])
        signals = [candidates[0]]
        log.info(
            "ma_support.signal",
            symbol=symbol,
            date=str(latest_date),
            **signals[0]["detail_json"],
        )

    return snapshots, signals


def ma_support_history(df: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Rolling indicator history for the detail page (on-demand, no DB).

    Returns DataFrame with columns: date, indicator, value.
    """
    periods: list[int] = params.get("periods", [20, 50, 200])

    records: list[dict] = []
    for period in periods:
        if len(df) < period:
            continue
        ma = df["close"].rolling(period).mean()
        dist = (df["close"] - ma).abs() / ma
        for idx in df.index[period - 1 :]:
            v = dist.loc[idx]
            if pd.isna(v):
                continue
            records.append(
                {"date": idx.date(), "indicator": f"ma_dist_{period}", "value": round(float(v), 6)}
            )

    return pd.DataFrame(records) if records else pd.DataFrame(columns=["date", "indicator", "value"])


def _make_id(symbol: str, d: date, strategy: str) -> str:
    raw = f"{symbol}|{d}|{strategy}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]
