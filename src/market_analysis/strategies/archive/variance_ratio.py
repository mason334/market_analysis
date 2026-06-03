from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import structlog

from market_analysis.models import IndicatorSnapshot

log = structlog.get_logger(__name__)

_MIN_ROWS = 60  # need enough data for VR(20) to be meaningful


def variance_ratio(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[list[IndicatorSnapshot], list[dict[str, Any]]]:
    """
    方差比检验策略（Lo-MacKinlay 1988）。

    VR(k) = Var(k-period returns) / (k * Var(1-period returns))
    - VR = 1 : random walk
    - VR > 1 : positive autocorrelation (momentum)
    - VR < 1 : negative autocorrelation (mean reversion)

    Returns (snapshots, signals).
    Snapshots: vr_5, vr_10, vr_20 — always computed.
    Signals: when min_agree lags exceed bullish/bearish threshold.
    """
    if len(df) < _MIN_ROWS:
        log.debug("variance_ratio.skip.insufficient_data", symbol=symbol, rows=len(df))
        return [], []

    lags: list[int] = params.get("lags", [5, 10, 20])
    bullish_thr: float = params.get("bullish_threshold", 1.1)
    bearish_thr: float = params.get("bearish_threshold", 0.9)
    min_agree: int = params.get("min_agree", 2)

    latest_date: date = df.index[-1].date()
    log_ret = np.log(df["close"].values[1:] / df["close"].values[:-1])

    snapshots: list[IndicatorSnapshot] = []
    vr_values: dict[int, float] = {}

    for k in lags:
        vr = _vr(log_ret, k)
        snapshots.append(IndicatorSnapshot(symbol, latest_date, f"vr_{k}", round(vr, 4)))
        vr_values[k] = vr

    bullish_count = sum(1 for v in vr_values.values() if v > bullish_thr)
    bearish_count = sum(1 for v in vr_values.values() if v < bearish_thr)

    signals: list[dict[str, Any]] = []
    if bullish_count >= min_agree:
        detail = {f"vr_{k}": round(v, 4) for k, v in vr_values.items()}
        detail["direction"] = "momentum"
        signals = [_make_signal(symbol, latest_date, "bullish", detail)]
        log.info("variance_ratio.signal.bullish", symbol=symbol, date=str(latest_date), **detail)
    elif bearish_count >= min_agree:
        detail = {f"vr_{k}": round(v, 4) for k, v in vr_values.items()}
        detail["direction"] = "mean_reversion"
        signals = [_make_signal(symbol, latest_date, "bearish", detail)]
        log.info("variance_ratio.signal.bearish", symbol=symbol, date=str(latest_date), **detail)

    return snapshots, signals


def _vr(log_ret: np.ndarray, k: int) -> float:
    """Lo-MacKinlay variance ratio for lag k using overlapping returns."""
    n = len(log_ret)
    if n < k * 4:
        return 1.0

    mu = log_ret.mean()

    # Variance of 1-period returns
    var1 = ((log_ret - mu) ** 2).sum() / (n - 1)
    if var1 <= 0:
        return 1.0

    # Overlapping k-period returns (np.convolve is significantly faster than a Python loop)
    k_rets = np.convolve(log_ret, np.ones(k), "valid")
    m = len(k_rets)
    var_k = ((k_rets - k * mu) ** 2).sum() / (m * k)

    return float(var_k / var1)


def variance_ratio_history(df: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Rolling VR indicator history for the detail page (on-demand, no DB).

    Each lag uses its own rolling window: window_k = k * 4 (minimum 20).
    This makes shorter lags more responsive to recent price action.
    Returns DataFrame with columns: date, indicator, value.
    """
    lags: list[int] = params.get("lags", [5, 10, 20])
    log_prices = np.log(df["close"].values)
    records: list[dict] = []

    for k in lags:
        window = max(k * 4, 20)
        if len(df) < window + 1:
            continue
        for i in range(window, len(df)):
            log_ret = np.diff(log_prices[i - window : i + 1])
            d = df.index[i].date()
            vr = _vr(log_ret, k)
            records.append({"date": d, "indicator": f"vr_{k}", "value": round(vr, 4)})

    return pd.DataFrame(records)


def _make_signal(
    symbol: str, d: date, signal_type: str, detail: dict[str, Any]
) -> dict[str, Any]:
    raw = f"{symbol}|{d}|variance_ratio"
    signal_id = hashlib.sha1(raw.encode()).hexdigest()[:16]
    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "date": d,
        "strategy": "variance_ratio",
        "signal_type": signal_type,
        "detail_json": detail,
    }
