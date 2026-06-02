from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import structlog

from market_analysis.models import IndicatorSnapshot

log = structlog.get_logger(__name__)


def hurst(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[list[IndicatorSnapshot], list[dict[str, Any]]]:
    """
    滚动 Hurst 指数策略（方差缩放法）。

    H = 0.5 : 随机游走
    H > 0.5 : 正自相关，趋势性（动量）
    H < 0.5 : 负自相关，均值回归

    Returns (snapshots, signals).
    Snapshot: hurst_60d — always computed when window sufficient.
    Signals:
      H > bullish_threshold → bullish (strong trend)
      H < bearish_threshold → bearish (strong mean reversion)
    """
    window: int = params.get("window", 60)
    bullish_thr: float = params.get("bullish_threshold", 0.65)
    bearish_thr: float = params.get("bearish_threshold", 0.35)

    if len(df) < window + 1:
        log.debug("hurst.skip.insufficient_data", symbol=symbol, rows=len(df))
        return [], []

    latest_date: date = df.index[-1].date()
    prices = df["close"].values[-(window + 1):]
    log_ret = np.log(prices[1:] / prices[:-1])

    h = _hurst_variance_scaling(log_ret)
    snapshots = [IndicatorSnapshot(symbol, latest_date, "hurst_60d", round(h, 4))]

    signals: list[dict[str, Any]] = []
    if h > bullish_thr:
        detail = {"hurst": round(h, 4), "window": window, "interpretation": "trending"}
        signals = [_make_signal(symbol, latest_date, "bullish", detail)]
        log.info("hurst.signal.bullish", symbol=symbol, date=str(latest_date), hurst=round(h, 4))
    elif h < bearish_thr:
        detail = {"hurst": round(h, 4), "window": window, "interpretation": "mean_reverting"}
        signals = [_make_signal(symbol, latest_date, "bearish", detail)]
        log.info("hurst.signal.bearish", symbol=symbol, date=str(latest_date), hurst=round(h, 4))

    return snapshots, signals


def _hurst_variance_scaling(log_ret: np.ndarray) -> float:
    """
    Estimate Hurst exponent via variance scaling.
    Var(k-step returns) ∝ k^(2H)  →  log(Var) = 2H * log(k) + const
    """
    n = len(log_ret)
    lags = [lag for lag in [2, 4, 8, 16, 32] if lag < n // 2]
    if len(lags) < 2:
        return 0.5

    log_vars: list[float] = []
    valid_lags: list[int] = []
    for lag in lags:
        k_rets = np.convolve(log_ret, np.ones(lag), "valid")
        if len(k_rets) < 4:
            break
        v = float(np.var(k_rets))
        if v > 0:
            log_vars.append(np.log(v))
            valid_lags.append(lag)

    if len(valid_lags) < 2:
        return 0.5

    poly = np.polyfit(np.log(valid_lags), log_vars, 1)
    h = float(poly[0] / 2.0)
    return float(np.clip(h, 0.0, 1.0))


def hurst_history(df: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Rolling Hurst indicator history for the detail page (on-demand, no DB).

    Returns DataFrame with columns: date, indicator, value.
    """
    window: int = params.get("window", 60)

    if len(df) < window + 1:
        return pd.DataFrame(columns=["date", "indicator", "value"])

    log_prices = np.log(df["close"].values)
    records: list[dict] = []

    for i in range(window, len(df)):
        log_ret = np.diff(log_prices[i - window : i + 1])
        d = df.index[i].date()
        h = _hurst_variance_scaling(log_ret)
        records.append({"date": d, "indicator": "hurst_60d", "value": round(h, 4)})

    return pd.DataFrame(records)


def _make_signal(
    symbol: str, d: date, signal_type: str, detail: dict[str, Any]
) -> dict[str, Any]:
    raw = f"{symbol}|{d}|hurst"
    signal_id = hashlib.sha1(raw.encode()).hexdigest()[:16]
    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "date": d,
        "strategy": "hurst",
        "signal_type": signal_type,
        "detail_json": detail,
    }
