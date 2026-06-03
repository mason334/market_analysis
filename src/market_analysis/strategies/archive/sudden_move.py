from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import pandas as pd
import structlog

from market_analysis.models import IndicatorSnapshot

log = structlog.get_logger(__name__)


def sudden_move(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[list[IndicatorSnapshot], list[dict[str, Any]]]:
    """
    突变检测策略：n天窗口内：
      sm_up_{n}d = (close_latest - close_min) / close_min  >= 0，从低点的反弹幅度
      sm_dn_{n}d = (close_latest - close_max) / close_max  <= 0，从高点的跌落幅度

    两个方向均超过 threshold 时分别触发 bullish / bearish 信号。

    Returns (snapshots, signals).
    """
    n: int = params.get("window", 10)
    threshold: float = params.get("threshold", 0.10)

    if len(df) < n:
        log.debug("sudden_move.skip.insufficient_data", symbol=symbol, rows=len(df))
        return [], []

    latest_date: date = df.index[-1].date()
    window = df.iloc[-n:]

    close_min = float(window["close"].min())
    close_max = float(window["close"].max())
    close_latest = float(window["close"].iloc[-1])

    if close_min <= 0 or close_max <= 0:
        return [], []

    chg_up = (close_latest - close_min) / close_min   # >= 0
    chg_dn = (close_latest - close_max) / close_max   # <= 0

    snapshots = [
        IndicatorSnapshot(symbol, latest_date, f"sm_up_{n}d", round(chg_up, 4)),
        IndicatorSnapshot(symbol, latest_date, f"sm_dn_{n}d", round(chg_dn, 4)),
    ]

    signals: list[dict[str, Any]] = []

    if chg_up >= threshold:
        min_date = window["close"].idxmin().date()
        detail: dict[str, Any] = {
            "window": n,
            "chg_from_min": round(chg_up, 4),
            "close_min": round(close_min, 4),
            "close_latest": round(close_latest, 4),
            "min_date": str(min_date),
        }
        log.info("sudden_move.bullish", symbol=symbol, date=str(latest_date), **detail)
        signals.append({
            "signal_id": _make_id(symbol, latest_date, "sudden_move_bullish"),
            "symbol": symbol,
            "date": latest_date,
            "strategy": "sudden_move",
            "signal_type": "bullish",
            "detail_json": detail,
        })

    if abs(chg_dn) >= threshold:
        max_date = window["close"].idxmax().date()
        detail = {
            "window": n,
            "chg_from_max": round(chg_dn, 4),
            "close_max": round(close_max, 4),
            "close_latest": round(close_latest, 4),
            "max_date": str(max_date),
        }
        log.info("sudden_move.bearish", symbol=symbol, date=str(latest_date), **detail)
        signals.append({
            "signal_id": _make_id(symbol, latest_date, "sudden_move_bearish"),
            "symbol": symbol,
            "date": latest_date,
            "strategy": "sudden_move",
            "signal_type": "bearish",
            "detail_json": detail,
        })

    return snapshots, signals


def sudden_move_history(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Rolling history of sm_up and sm_dn for the detail page chart (on-demand, no DB).

    Returns DataFrame with columns: date, indicator, value.
    """
    if len(df) < n:
        return pd.DataFrame(columns=["date", "indicator", "value"])

    up_key = f"sm_up_{n}d"
    dn_key = f"sm_dn_{n}d"
    records: list[dict] = []

    for i in range(n, len(df) + 1):
        window = df.iloc[i - n : i]
        close_min = float(window["close"].min())
        close_max = float(window["close"].max())
        close_latest = float(window["close"].iloc[-1])
        if close_min <= 0 or close_max <= 0:
            continue
        d = df.index[i - 1].date()
        chg_up = (close_latest - close_min) / close_min
        chg_dn = (close_latest - close_max) / close_max
        records.append({"date": d, "indicator": up_key, "value": round(chg_up, 4)})
        records.append({"date": d, "indicator": dn_key, "value": round(chg_dn, 4)})

    return pd.DataFrame(records)


def _make_id(symbol: str, d: date, strategy: str) -> str:
    raw = f"{symbol}|{d}|{strategy}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]
