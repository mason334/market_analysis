from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from scipy import stats

DEFAULT_TREND_WINDOWS: tuple[dict[str, int | str], ...] = (
    {"label": "5d", "far_bars": 5, "near_bars": 0},
    {"label": "10d", "far_bars": 10, "near_bars": 0},
    {"label": "20d", "far_bars": 20, "near_bars": 0},
    {"label": "40d", "far_bars": 40, "near_bars": 0},
    {"label": "60d", "far_bars": 60, "near_bars": 0},
    {"label": "11_20d", "far_bars": 20, "near_bars": 10},
    {"label": "20_40d", "far_bars": 40, "near_bars": 20},
    {"label": "40_60d", "far_bars": 60, "near_bars": 40},
)


def compute_trend(
    df: pd.DataFrame,
    far: int,
    near: int = 0,
) -> tuple[float | None, float | None]:
    """
    Linear regression on a slice of closing prices.

    near=0 uses the latest `far` bars. near>0 uses df[-far:-near], which
    represents an older segment such as 11_20d.
    """
    if len(df) < far:
        return None, None

    if near == 0:
        y = df["close"].iloc[-far:].values.astype(float)
    else:
        y = df["close"].iloc[-far:-near].values.astype(float)

    if len(y) < 2:
        return None, None

    x = np.arange(len(y), dtype=float)
    slope, _, r_value, _, _ = stats.linregress(x, y)

    first_price = y[0]
    if first_price <= 0:
        return None, None

    return round(slope / first_price, 6), round(r_value**2, 4)


def _load_windows(params: dict[str, Any]) -> list[dict[str, int | str]]:
    raw_windows = params.get("windows")
    if not raw_windows:
        return [dict(w) for w in DEFAULT_TREND_WINDOWS]

    windows: list[dict[str, int | str]] = []
    for item in raw_windows:
        label = str(item["label"])
        far_bars = int(item["far_bars"])
        near_bars = int(item.get("near_bars", 0))
        windows.append({"label": label, "far_bars": far_bars, "near_bars": near_bars})
    return windows


def compute_trend_indicators(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compute one trend_daily row per configured trend window."""
    if df.empty:
        return []

    latest_date: date = df.index[-1].date()
    rows: list[dict[str, Any]] = []

    for window in _load_windows(params):
        label = str(window["label"])
        far_bars = int(window["far_bars"])
        near_bars = int(window["near_bars"])
        slope, r2 = compute_trend(df, far_bars, near_bars)
        rows.append(
            {
                "symbol": symbol,
                "date": latest_date,
                "window_label": label,
                "far_bars": far_bars,
                "near_bars": near_bars,
                "slope": slope,
                "r2": r2,
                "method": "linear_regression",
            }
        )

    return rows
