from __future__ import annotations

import hashlib
import warnings
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import structlog

from market_analysis.models import IndicatorSnapshot

log = structlog.get_logger(__name__)

_MIN_ROWS = 100  # minimum rows for meaningful Markov fit


def markov(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[list[IndicatorSnapshot], list[dict[str, Any]]]:
    """
    马尔可夫体制切换策略（双体制 AR(1)，方差切换）。

    识别股票价格处于高波动体制（bearish/risk signal）还是低波动体制（bullish）。
    当体制切换时触发信号。

    Returns (snapshots, signals).
    Snapshot: markov_regime_prob — 当前处于高波动体制的过滤概率。
    Signals: 当体制概率越过 regime_threshold 时触发。
    """
    fit_window: int = params.get("fit_window", 252)
    regime_thr: float = params.get("regime_threshold", 0.75)
    order: int = params.get("order", 1)

    if len(df) < _MIN_ROWS:
        log.debug("markov.skip.insufficient_data", symbol=symbol, rows=len(df))
        return [], []

    latest_date: date = df.index[-1].date()

    prices = df["close"].values[-fit_window:]
    log_ret = np.log(prices[1:] / prices[:-1])

    try:
        import statsmodels.api as sm

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mod = sm.tsa.MarkovAutoregression(
                log_ret,
                k_regimes=2,
                order=order,
                switching_variance=True,
            )
            res = mod.fit(disp=False, maxiter=200)

        # filtered probabilities: shape (nobs, k_regimes)
        fps = np.asarray(res.filtered_marginal_probabilities)

        # Identify high-volatility regime by weighted variance
        # fps has shape (nobs - order, k_regimes); trim log_ret to match
        log_ret_sq = log_ret[order:] ** 2
        wv0 = float((fps[:, 0] * log_ret_sq).sum() / (fps[:, 0].sum() + 1e-10))
        wv1 = float((fps[:, 1] * log_ret_sq).sum() / (fps[:, 1].sum() + 1e-10))
        high_vol_regime = 1 if wv1 > wv0 else 0

        current_prob = float(fps[-1, high_vol_regime])
        prev_prob = float(fps[-2, high_vol_regime]) if len(fps) >= 2 else current_prob

    except Exception as exc:
        log.warning("markov.fit.failed", symbol=symbol, error=str(exc))
        return [], []

    snapshots = [
        IndicatorSnapshot(symbol, latest_date, "markov_regime_prob", round(current_prob, 4))
    ]

    signals: list[dict[str, Any]] = []
    entered_high_vol = current_prob > regime_thr and prev_prob <= regime_thr
    entered_low_vol = current_prob < (1 - regime_thr) and prev_prob >= (1 - regime_thr)

    if entered_high_vol:
        detail = {
            "regime_prob": round(current_prob, 4),
            "prev_prob": round(prev_prob, 4),
            "event": "entered_high_vol",
        }
        signals = [_make_signal(symbol, latest_date, "bearish", detail)]
        log.info("markov.signal.bearish", symbol=symbol, date=str(latest_date),
                 regime_prob=detail["regime_prob"], prev_prob=detail["prev_prob"], regime_event=detail["event"])
    elif entered_low_vol:
        detail = {
            "regime_prob": round(current_prob, 4),
            "prev_prob": round(prev_prob, 4),
            "event": "entered_low_vol",
        }
        signals = [_make_signal(symbol, latest_date, "bullish", detail)]
        log.info("markov.signal.bullish", symbol=symbol, date=str(latest_date),
                 regime_prob=detail["regime_prob"], prev_prob=detail["prev_prob"], regime_event=detail["event"])

    return snapshots, signals


def markov_history(df: pd.DataFrame, params: dict[str, Any]) -> pd.DataFrame:
    """Markov regime probability history for the detail page (on-demand, no DB).

    Fits the model once on the full dataset and extracts filtered probabilities
    for the entire time series — much faster than re-fitting per date.

    Returns DataFrame with columns: date, indicator, value.
    """
    fit_window: int = params.get("fit_window", 252)
    order: int = params.get("order", 1)

    if len(df) < _MIN_ROWS:
        return pd.DataFrame(columns=["date", "indicator", "value"])

    prices = df["close"].values[-fit_window:]
    log_ret = np.log(prices[1:] / prices[:-1])
    # dates aligned with log_ret (one shorter than prices)
    dates = df.index[-len(log_ret) :]

    try:
        import statsmodels.api as sm

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mod = sm.tsa.MarkovAutoregression(
                log_ret,
                k_regimes=2,
                order=order,
                switching_variance=True,
            )
            res = mod.fit(disp=False, maxiter=200)

        fps = np.asarray(res.filtered_marginal_probabilities)
        # fps has shape (nobs - order, k_regimes); trim log_ret to match
        log_ret_sq = log_ret[order:] ** 2
        wv0 = float((fps[:, 0] * log_ret_sq).sum() / (fps[:, 0].sum() + 1e-10))
        wv1 = float((fps[:, 1] * log_ret_sq).sum() / (fps[:, 1].sum() + 1e-10))
        high_vol_regime = 1 if wv1 > wv0 else 0

        aligned_dates = dates[order:]
        records = [
            {
                "date": aligned_dates[i].date(),
                "indicator": "markov_regime_prob",
                "value": round(float(fps[i, high_vol_regime]), 4),
            }
            for i in range(len(fps))
        ]
        return pd.DataFrame(records)

    except Exception as exc:
        log.warning("markov_history.fit.failed", error=str(exc))
        return pd.DataFrame(columns=["date", "indicator", "value"])


def _make_signal(
    symbol: str, d: date, signal_type: str, detail: dict[str, Any]
) -> dict[str, Any]:
    raw = f"{symbol}|{d}|markov"
    signal_id = hashlib.sha1(raw.encode()).hexdigest()[:16]
    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "date": d,
        "strategy": "markov",
        "signal_type": signal_type,
        "detail_json": detail,
    }
