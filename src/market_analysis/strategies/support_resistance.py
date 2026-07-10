from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import structlog
from scipy import stats
from scipy.signal import argrelextrema

log = structlog.get_logger(__name__)


def _compute_atr(df: pd.DataFrame, period: int) -> float:
    """Wilder ATR (RMA): alpha = 1/period, adjust=False."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return float(tr.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1])


def support_resistance(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> dict[str, Any] | None:
    """
    SR daily snapshot.

    Computes SR levels and returns a dict matching sr_daily table columns:
      nearest_support / nearest_resistance   center price of nearest level
      dist_support_pct / dist_resistance_pct (close - zone_edge) / close
      dist_support_atr / dist_resistance_atr (close - zone_edge) / ATR14
      atr_14
      sr_status   normal | watch | warning | at_support | at_resistance
      breakout_5d break_up | break_down | None
      breakout_level  price of the broken SR level (None if no breakout)
      trend_slope_5d  normalized slope (slope / first_price)
      trend_r2_5d     R-squared of the fit

    Returns None if insufficient data.
    """

    atr_period: int = params.get("atr_period", 14)
    watch_mult: float = params.get("watch_atr_mult", 1.5)
    warn_mult: float = params.get("warn_atr_mult", 0.5)
    breakout_window: int = params.get("breakout_window", 5)
    trend_window: int = params.get("trend_window", 5)

    latest_date: date = df.index[-1].date()
    current_close = float(df["close"].iloc[-1])

    levels = compute_sr_levels(df, params)

    # --- ATR ---
    atr = 0.0
    if len(df) >= atr_period + 1:
        atr = _compute_atr(df, atr_period)

    # --- Nearest support / resistance ---
    # level_type "both" means price is inside the zone; classify by center price
    supports: list[dict[str, Any]] = []
    resistances: list[dict[str, Any]] = []
    for lv in levels:
        lt = lv["level_type"]
        if lt == "support":
            supports.append(lv)
        elif lt == "resistance":
            resistances.append(lv)
        else:  # "both" — price is inside or right at the zone
            direction = _zone_entry_direction(df, lv, current_close)
            if direction == "support":
                supports.append(lv)
            else:
                resistances.append(lv)

    nearest_sup = max(supports, key=lambda x: x["price"]) if supports else None
    nearest_res = min(resistances, key=lambda x: x["price"]) if resistances else None

    # Distance: measured from zone edge (zone_high for support, zone_low for resistance)
    # Negative value = price is already inside the zone
    if nearest_sup is not None:
        d_sup = current_close - nearest_sup["zone_high"]
        dist_support_pct = round(d_sup / current_close, 4)
        dist_support_atr = round(d_sup / atr, 4) if atr > 0 else None
    else:
        dist_support_pct = None
        dist_support_atr = None

    if nearest_res is not None:
        d_res = nearest_res["zone_low"] - current_close
        dist_resistance_pct = round(d_res / current_close, 4)
        dist_resistance_atr = round(d_res / atr, 4) if atr > 0 else None
    else:
        dist_resistance_pct = None
        dist_resistance_atr = None

    # --- SR status ---
    sr_status = _compute_sr_status(
        current_close, df, levels, atr, warn_mult, watch_mult
    )

    # --- Breakout detection (last breakout_window candles, most recent first) ---
    breakout_5d, breakout_level = _detect_breakout(df, levels, breakout_window)

    # --- Linear regression trend: cumulative windows (last N bars) ---
    trend_slope_5d,  trend_r2_5d  = _compute_trend(df, trend_window)
    trend_slope_10d, trend_r2_10d = _compute_trend(df, 10)
    trend_slope_20d, trend_r2_20d = _compute_trend(df, 20)
    trend_slope_40d, trend_r2_40d = _compute_trend(df, 40)
    trend_slope_60d, trend_r2_60d = _compute_trend(df, 60)
    # --- Linear regression trend: historical segments ---
    trend_slope_11_20d, trend_r2_11_20d = _compute_trend(df, 20, 10)   # d11-d20
    trend_slope_20_40d, trend_r2_20_40d = _compute_trend(df, 40, 20)   # d20-d40
    trend_slope_40_60d, trend_r2_40_60d = _compute_trend(df, 60, 40)   # d40-d60

    log.debug(
        "support_resistance.done",
        symbol=symbol,
        date=str(latest_date),
        sr_status=sr_status,
        breakout_5d=breakout_5d,
    )

    return {
        "symbol": symbol,
        "date": latest_date,
        "nearest_support": nearest_sup["price"] if nearest_sup else None,
        "nearest_resistance": nearest_res["price"] if nearest_res else None,
        "dist_support_pct": dist_support_pct,
        "dist_support_atr": dist_support_atr,
        "dist_resistance_pct": dist_resistance_pct,
        "dist_resistance_atr": dist_resistance_atr,
        "atr_14": round(atr, 4) if atr else None,
        "sr_status": sr_status,
        "breakout_5d": breakout_5d,
        "breakout_level": breakout_level,
        "trend_slope_5d": trend_slope_5d,
        "trend_r2_5d": trend_r2_5d,
        "trend_slope_10d": trend_slope_10d,
        "trend_r2_10d": trend_r2_10d,
        "trend_slope_20d": trend_slope_20d,
        "trend_r2_20d": trend_r2_20d,
        "trend_slope_40d": trend_slope_40d,
        "trend_r2_40d": trend_r2_40d,
        "trend_slope_60d": trend_slope_60d,
        "trend_r2_60d": trend_r2_60d,
        "trend_slope_11_20d": trend_slope_11_20d,
        "trend_r2_11_20d": trend_r2_11_20d,
        "trend_slope_20_40d": trend_slope_20_40d,
        "trend_r2_20_40d": trend_r2_20_40d,
        "trend_slope_40_60d": trend_slope_40_60d,
        "trend_r2_40_60d": trend_r2_40_60d,
    }


def _zone_entry_direction(
    df: pd.DataFrame,
    lv: dict[str, Any],
    close: float,
    lookback: int = 10,
) -> str:
    """
    Determine whether price entered a zone from above (support) or below (resistance).

    Scans back up to `lookback` bars for the most recent close that was clearly
    outside the zone (> zone_high*1.005 or < zone_low*0.995).  If no such bar
    is found, falls back to close position relative to zone centre.
    """
    n = min(len(df), lookback + 1)
    for i in range(-1, -(n + 1), -1):
        past = float(df["close"].iloc[i])
        if past > lv["zone_high"] * 1.005:
            return "support"     # was above → fell into zone → zone is support
        if past < lv["zone_low"] * 0.995:
            return "resistance"  # was below → rose into zone → zone is resistance
    # All recent closes within zone: use position relative to centre as fallback
    return "support" if close >= lv["price"] else "resistance"


def _compute_sr_status(
    close: float,
    df: pd.DataFrame,
    levels: list[dict[str, Any]],
    atr: float,
    warn_mult: float,
    watch_mult: float,
) -> str:
    """
    Determine price status relative to SR levels.

    Returns: normal | watch | warning | at_support | at_resistance

    When price is inside a zone (alert level 3), entry direction is determined
    by scanning back up to 10 bars for the last close clearly outside the zone.
    """
    if not levels or atr <= 0:
        return "normal"

    best_alert = 0
    best_dist_atr = float("inf")
    best_lv: dict[str, Any] | None = None

    for lv in levels:
        if lv["zone_low"] <= close <= lv["zone_high"]:
            dist_abs = 0.0
            alert = 3
        else:
            dist_abs = min(abs(close - lv["zone_low"]), abs(close - lv["zone_high"]))
            dist_atr_val = dist_abs / atr
            if dist_atr_val <= warn_mult:
                alert = 2
            elif dist_atr_val <= watch_mult:
                alert = 1
            else:
                alert = 0

        dist_atr_val = dist_abs / atr if atr > 0 else float("inf")
        if alert > best_alert or (alert == best_alert and dist_atr_val < best_dist_atr):
            best_alert = alert
            best_dist_atr = dist_atr_val
            best_lv = lv

    if best_alert == 0 or best_lv is None:
        return "normal"
    if best_alert == 1:
        return "watch"
    if best_alert == 2:
        return "warning"

    # alert == 3: price is inside zone — scan back to find entry direction
    direction = _zone_entry_direction(df, best_lv, close)
    return f"at_{direction}"


def _detect_breakout(
    df: pd.DataFrame,
    levels: list[dict[str, Any]],
    window: int,
) -> tuple[str | None, float | None]:
    """
    Scan the last `window` candles (most recent first) for a close crossing
    through any SR zone boundary. Returns the most recent event.

    Returns (breakout_type, level_price) or (None, None).
    """
    if not levels or len(df) < window + 1:
        return None, None

    for i in range(-1, -(window + 1), -1):
        c_prev = float(df["close"].iloc[i - 1])
        c_curr = float(df["close"].iloc[i])
        for lv in levels:
            if c_prev <= lv["zone_high"] and c_curr > lv["zone_high"]:
                return "break_up", lv["price"]
            if c_prev >= lv["zone_low"] and c_curr < lv["zone_low"]:
                return "break_down", lv["price"]

    return None, None


def _compute_trend(
    df: pd.DataFrame,
    far: int,
    near: int = 0,
) -> tuple[float | None, float | None]:
    """
    Linear regression on a slice of closing prices.

    near=0 (default): uses the last `far` bars → df[-far:]
    near>0:           uses df[-far:-near]  (historical segment)

    slope is normalized by the first price in the slice
    (= approximate daily return rate per bar).

    Returns (normalized_slope, r_squared) or (None, None).
    """
    if near == 0:
        if len(df) < far:
            return None, None
        y = df["close"].iloc[-far:].values.astype(float)
    else:
        if len(df) < far:
            return None, None
        y = df["close"].iloc[-far:-near].values.astype(float)

    if len(y) < 2:
        return None, None

    x = np.arange(len(y), dtype=float)
    slope, _, r_value, _, _ = stats.linregress(x, y)

    first_price = y[0]
    if first_price <= 0:
        return None, None

    return round(slope / first_price, 6), round(r_value ** 2, 4)


def compute_sr_levels(df: pd.DataFrame, params: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Compute SR level list from OHLCV (used by pipeline and dashboard).

    Returns list[dict] sorted by (is_extreme DESC, touches DESC, span_days DESC).
    Each dict contains:
      price, zone_low, zone_high, touches, span_days,
      level_type (support|resistance|both), last_touch, is_extreme,
      swing_dates, swing_prices
    """
    swing_order: int = params.get("swing_order", 3)
    cluster_pct: float = params.get("cluster_pct", 1.5)
    min_touches: int = params.get("min_touches", 2)
    min_span_days: int = params.get("min_span_days", 10)
    max_levels: int = params.get("max_levels", 5)
    max_dist_pct: float = params.get("max_dist_pct", 15.0)
    extreme_order: int = params.get("extreme_order", 60)
    extreme_pct: float = params.get("extreme_pct", 5.0)
    lookback: int = params.get("lookback_window", 500)

    df = df.iloc[-lookback:] if len(df) > lookback else df

    high_idx = argrelextrema(df["high"].values, np.greater_equal, order=swing_order)[0]
    low_idx = argrelextrema(df["low"].values, np.less_equal, order=swing_order)[0]

    if len(high_idx) == 0 and len(low_idx) == 0:
        return []

    swing_prices = np.concatenate([
        df["high"].values[high_idx],
        df["low"].values[low_idx],
    ])
    swing_dates = np.concatenate([
        df.index[high_idx].to_numpy(),
        df.index[low_idx].to_numpy(),
    ])

    # Sort by price for single-pass clustering
    order = np.argsort(swing_prices)
    swing_prices = swing_prices[order]
    swing_dates = swing_dates[order]

    clusters: list[tuple[list[float], list[Any]]] = []
    i = 0
    while i < len(swing_prices):
        cp: list[float] = [float(swing_prices[i])]
        cd: list[Any] = [swing_dates[i]]
        j = i + 1
        while j < len(swing_prices):
            cluster_mean = float(np.mean(cp))
            if abs(float(swing_prices[j]) - cluster_mean) / cluster_mean * 100 <= cluster_pct:
                cp.append(float(swing_prices[j]))
                cd.append(swing_dates[j])
                j += 1
            else:
                break
        clusters.append((cp, cd))
        i = j

    current_close = float(df["close"].iloc[-1])
    levels: list[dict[str, Any]] = []

    for cp, cd in clusters:
        touches = len(cp)
        if touches < min_touches:
            continue

        dates_pd = pd.DatetimeIndex(cd)
        span_days = int((dates_pd.max() - dates_pd.min()).days)
        if span_days < min_span_days:
            continue

        price = float(np.mean(cp))
        zone_low = float(np.min(cp))
        zone_high = float(np.max(cp))

        if current_close > zone_high * 1.005:
            level_type = "support"
        elif current_close < zone_low * 0.995:
            level_type = "resistance"
        else:
            level_type = "both"

        levels.append({
            "price": round(price, 2),
            "zone_low": round(zone_low, 2),
            "zone_high": round(zone_high, 2),
            "touches": touches,
            "span_days": span_days,
            "level_type": level_type,
            "last_touch": dates_pd.max(),
            "is_extreme": False,
            "swing_dates": list(pd.DatetimeIndex(cd)),
            "swing_prices": [round(p, 2) for p in cp],
        })

    # --- Extreme single-point pivots ---
    if extreme_order >= 2 and len(df) >= extreme_order * 2 + 1:
        n = len(df)
        ex_high_idx = argrelextrema(df["high"].values, np.greater_equal, order=extreme_order)[0]
        ex_low_idx = argrelextrema(df["low"].values, np.less_equal, order=extreme_order)[0]

        # Exclude the last extreme_order bars: they lack right-side context and
        # would be falsely identified as extrema due to scipy's clip edge mode.
        ex_high_idx = ex_high_idx[ex_high_idx < n - extreme_order]
        ex_low_idx = ex_low_idx[ex_low_idx < n - extreme_order]

        for idx, is_high in [(i, True) for i in ex_high_idx] + [(i, False) for i in ex_low_idx]:
            price = float(df["high"].values[idx] if is_high else df["low"].values[idx])
            lo = max(0, idx - extreme_order)
            hi = min(n, idx + extreme_order + 1)
            surrounding = np.concatenate([
                df["close"].values[lo:idx],
                df["close"].values[idx + 1:hi],
            ])
            if len(surrounding) == 0:
                continue
            surrounding_mean = float(surrounding.mean())
            deviation = (price - surrounding_mean) / surrounding_mean * 100
            if is_high and deviation < extreme_pct:
                continue
            if not is_high and deviation > -extreme_pct:
                continue
            if any(abs(lv["price"] - price) / price * 100 <= cluster_pct for lv in levels):
                continue
            d = df.index[idx]
            if current_close > price * 1.005:
                ltype = "support"
            elif current_close < price * 0.995:
                ltype = "resistance"
            else:
                ltype = "both"
            levels.append({
                "price": round(price, 2),
                "zone_low": round(price, 2),
                "zone_high": round(price, 2),
                "touches": 1,
                "span_days": 0,
                "level_type": ltype,
                "last_touch": d,
                "is_extreme": True,
                "swing_dates": [d],
                "swing_prices": [round(price, 2)],
            })

    # Filter: remove levels too far from current price
    levels = [
        lv for lv in levels
        if abs(lv["price"] - current_close) / current_close * 100 <= max_dist_pct
    ]

    # Sort: extreme first, then by touches and span
    levels.sort(key=lambda x: (x["is_extreme"], x["touches"], x["span_days"]), reverse=True)
    return levels[:max_levels]


def compute_raw_swings(
    df: pd.DataFrame,
    swing_order: int = 3,
    lookback_window: int = 500,
) -> tuple[list, list]:
    """
    Return all raw swing points within lookback_window bars (unfiltered).

    Returns (dates, prices) as two equal-length lists.
    """
    df = df.iloc[-lookback_window:] if len(df) > lookback_window else df

    high_idx = argrelextrema(df["high"].values, np.greater_equal, order=swing_order)[0]
    low_idx = argrelextrema(df["low"].values, np.less_equal, order=swing_order)[0]

    dates = list(df.index[high_idx]) + list(df.index[low_idx])
    prices = list(df["high"].values[high_idx]) + list(df["low"].values[low_idx])
    return dates, prices
