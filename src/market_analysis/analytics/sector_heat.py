from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import structlog

log = structlog.get_logger(__name__)

_MIN_BARS_MA = 21    # minimum bars to compute MA20 (need 20 + 1 extra)
_ZSCORE_WINDOW = 60  # rolling window for z-score


def compute_sector_heat(
    universe_ticker: str,
    wide_df: pd.DataFrame,
) -> dict[str, Any] | None:
    """
    Compute sector heat snapshot for the latest date in wide_df.

    Args:
        universe_ticker: sector identifier (e.g. "XLK")
        wide_df: index=DatetimeIndex, columns=stock_ticker, values=close*volume (turnover)

    Returns:
        dict matching sector_heat_daily schema, or None if insufficient data.
    """
    if wide_df.empty or len(wide_df) < _MIN_BARS_MA:
        log.warning(
            "sector_heat.insufficient_data",
            ticker=universe_ticker,
            bars=len(wide_df),
            required=_MIN_BARS_MA,
        )
        return None

    # Sum across constituents per day; NaN only if ALL stocks are NaN on that day
    sector_ts = wide_df.sum(axis=1, min_count=1)
    constituent_counts = wide_df.notna().sum(axis=1)

    sector_ts = sector_ts.dropna()
    if len(sector_ts) < _MIN_BARS_MA:
        log.warning(
            "sector_heat.insufficient_data_after_dropna",
            ticker=universe_ticker,
            bars=len(sector_ts),
        )
        return None

    latest_date_ts = sector_ts.index[-1]
    latest_turnover = float(sector_ts.iloc[-1])

    # constituent_count for the latest date
    if latest_date_ts in constituent_counts.index:
        latest_count = int(constituent_counts.loc[latest_date_ts])
    else:
        latest_count = 0

    # MA20
    ma20_val = sector_ts.rolling(20).mean().iloc[-1]
    ma20: float | None = float(ma20_val) if pd.notna(ma20_val) else None

    # turnover_ratio = today / MA20
    ratio: float | None = None
    if ma20 is not None and ma20 > 0:
        ratio = latest_turnover / ma20

    # Z-score over 60-day window
    zscore: float | None = None
    if len(sector_ts) >= _ZSCORE_WINDOW:
        window = sector_ts.iloc[-_ZSCORE_WINDOW:]
        std_val = float(window.std())
        if std_val > 0:
            zscore = float((latest_turnover - float(window.mean())) / std_val)

    latest_date_val: date = (
        latest_date_ts.date() if hasattr(latest_date_ts, "date") else latest_date_ts
    )

    return {
        "universe_ticker": universe_ticker,
        "date": latest_date_val,
        "sector_turnover": latest_turnover,
        "constituent_count": latest_count,
        "turnover_ma20": ma20,
        "turnover_ratio": ratio,
        "turnover_zscore": zscore,
    }


def compute_sector_heat_history(
    universe_ticker: str,
    wide_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Compute rolling heat metrics for ALL dates in wide_df.

    Use this for the detail page historical charts. Pass extra warm-up data
    (e.g. 90 calendar days before the display start) so that MA20 and z-score
    are meaningful from the first displayed date.

    Args:
        universe_ticker: sector identifier
        wide_df: index=DatetimeIndex, columns=stock_ticker, values=close*volume

    Returns:
        DataFrame with columns [universe_ticker, date, sector_turnover,
        constituent_count, turnover_ma20, turnover_ratio, turnover_zscore].
        Empty DataFrame if insufficient data.
    """
    if wide_df.empty:
        return pd.DataFrame()

    sector_ts = wide_df.sum(axis=1, min_count=1).dropna()
    constituent_counts = wide_df.notna().sum(axis=1).reindex(sector_ts.index).fillna(0)

    if len(sector_ts) < _MIN_BARS_MA:
        return pd.DataFrame()

    ma20 = sector_ts.rolling(20).mean()
    # Avoid division by zero: where ma20 == 0, ratio stays NaN
    ratio = sector_ts.div(ma20.replace(0, float("nan")))

    rolling_mean = sector_ts.rolling(_ZSCORE_WINDOW).mean()
    rolling_std = sector_ts.rolling(_ZSCORE_WINDOW).std().replace(0, float("nan"))
    zscore = (sector_ts - rolling_mean) / rolling_std

    return pd.DataFrame(
        {
            "universe_ticker": universe_ticker,
            "date": sector_ts.index,
            "sector_turnover": sector_ts.values,
            "constituent_count": constituent_counts.values.astype(int),
            "turnover_ma20": ma20.values,
            "turnover_ratio": ratio.values,
            "turnover_zscore": zscore.values,
        }
    ).reset_index(drop=True)
