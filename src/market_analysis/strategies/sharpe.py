from __future__ import annotations

import hashlib
import math
from datetime import date
from typing import Any

import pandas as pd
import structlog

from market_analysis.models import IndicatorSnapshot

log = structlog.get_logger(__name__)

_ANNUALIZE = math.sqrt(252)


def sharpe_ratio(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[list[IndicatorSnapshot], list[dict[str, Any]]]:
    """
    滚动夏普比率：n天窗口内的日收益率均值/标准差，年化处理。

    sharpe_{n}d = mean(daily_returns) / std(daily_returns) * sqrt(252)

    无风险利率默认为 0（可通过 risk_free_rate 配置年化利率，自动折算为日利率）。

    Returns (snapshots, signals).
    Signal: bullish when sharpe >= bullish_threshold, bearish when sharpe <= bearish_threshold.
    """
    n: int = params.get("window", 60)
    risk_free_annual: float = params.get("risk_free_rate", 0.0)
    bullish_threshold: float = params.get("bullish_threshold", 1.0)
    bearish_threshold: float = params.get("bearish_threshold", -1.0)

    # need n+1 rows to compute n daily returns
    if len(df) < n + 1:
        log.debug("sharpe.skip.insufficient_data", symbol=symbol, rows=len(df))
        return [], []

    latest_date: date = df.index[-1].date()
    daily_rf = risk_free_annual / 252
    returns = df["close"].pct_change().iloc[-n:]

    std = float(returns.std())
    if std == 0:
        return [], []

    mean = float(returns.mean())
    sharpe = (mean - daily_rf) / std * _ANNUALIZE
    sharpe = round(sharpe, 4)

    indicator_key = f"sharpe_{n}d"
    snapshots = [IndicatorSnapshot(symbol, latest_date, indicator_key, sharpe)]

    signals: list[dict[str, Any]] = []

    if sharpe >= bullish_threshold:
        detail: dict[str, Any] = {
            "window": n,
            "sharpe": sharpe,
            "mean_daily_return": round(mean, 6),
            "std_daily_return": round(std, 6),
        }
        log.info("sharpe.bullish", symbol=symbol, date=str(latest_date), **detail)
        signals.append({
            "signal_id": _make_id(symbol, latest_date, "sharpe_bullish"),
            "symbol": symbol,
            "date": latest_date,
            "strategy": "sharpe",
            "signal_type": "bullish",
            "detail_json": detail,
        })
    elif sharpe <= bearish_threshold:
        detail = {
            "window": n,
            "sharpe": sharpe,
            "mean_daily_return": round(mean, 6),
            "std_daily_return": round(std, 6),
        }
        log.info("sharpe.bearish", symbol=symbol, date=str(latest_date), **detail)
        signals.append({
            "signal_id": _make_id(symbol, latest_date, "sharpe_bearish"),
            "symbol": symbol,
            "date": latest_date,
            "strategy": "sharpe",
            "signal_type": "bearish",
            "detail_json": detail,
        })

    return snapshots, signals


def sharpe_ratio_history(df: pd.DataFrame, n: int, risk_free_rate: float = 0.0) -> pd.DataFrame:
    """Rolling Sharpe ratio history for the detail page chart (on-demand, no DB).

    Returns DataFrame with columns: date, indicator, value.
    """
    if len(df) < n + 1:
        return pd.DataFrame(columns=["date", "indicator", "value"])

    indicator_key = f"sharpe_{n}d"
    daily_rf = risk_free_rate / 252
    returns = df["close"].pct_change()

    records: list[dict] = []
    # start from index n (inclusive) so each window has exactly n returns
    for i in range(n, len(df)):
        window_returns = returns.iloc[i - n + 1 : i + 1]
        std = float(window_returns.std())
        if std == 0:
            continue
        mean = float(window_returns.mean())
        sharpe = (mean - daily_rf) / std * _ANNUALIZE
        d = df.index[i].date()
        records.append({"date": d, "indicator": indicator_key, "value": round(sharpe, 4)})

    return pd.DataFrame(records)


def _make_id(symbol: str, d: date, strategy: str) -> str:
    raw = f"{symbol}|{d}|{strategy}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]
