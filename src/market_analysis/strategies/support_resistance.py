from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import structlog
from scipy.signal import argrelextrema

from market_analysis.models import IndicatorSnapshot

log = structlog.get_logger(__name__)

_MIN_ROWS = 60


def support_resistance(
    symbol: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> tuple[list[IndicatorSnapshot], list[dict[str, Any]]]:
    """
    支撑阻力位识别策略（摆动点聚类法）。

    算法：
      1. argrelextrema 找摆动高点（high 局部极大）和低点（low 局部极小）
      2. 对所有摆动价格排序后做单次扫描聚类：相邻价格在 cluster_pct% 以内归为同一水平区
      3. 过滤：触及次数 >= min_touches 且时间跨度 >= min_span_days 天
      4. 突破检测：收盘穿越阻力区上边界 → bullish；跌穿支撑区下边界 → bearish

    Snapshots:
      sr_dist_support    (close - 最近支撑位) / close，值越小越接近支撑
      sr_dist_resistance (最近阻力位 - close) / close，值越小越接近阻力

    Signals:
      bullish: 收盘突破阻力区上边界
      bearish: 收盘跌破支撑区下边界
    """
    if len(df) < _MIN_ROWS:
        log.debug("support_resistance.skip.insufficient_data", symbol=symbol, rows=len(df))
        return [], []

    latest_date: date = df.index[-1].date()
    current_close = float(df["close"].iloc[-1])
    levels = compute_sr_levels(df, params)

    # --- Snapshots: distance to nearest support / resistance ---
    snapshots: list[IndicatorSnapshot] = []
    supports = [lv for lv in levels if lv["price"] < current_close]
    resistances = [lv for lv in levels if lv["price"] > current_close]

    if supports:
        nearest = max(supports, key=lambda x: x["price"])
        dist = round((current_close - nearest["price"]) / current_close, 4)
        snapshots.append(IndicatorSnapshot(symbol, latest_date, "sr_dist_support", dist))
    if resistances:
        nearest = min(resistances, key=lambda x: x["price"])
        dist = round((nearest["price"] - current_close) / current_close, 4)
        snapshots.append(IndicatorSnapshot(symbol, latest_date, "sr_dist_resistance", dist))

    # --- Signals: breakout / breakdown ---
    signals: list[dict[str, Any]] = []
    if len(df) >= 2:
        prev_close = float(df["close"].iloc[-2])
        for lv in levels:
            if prev_close <= lv["zone_high"] and current_close > lv["zone_high"]:
                detail = {
                    "level": lv["price"],
                    "zone": [lv["zone_low"], lv["zone_high"]],
                    "touches": lv["touches"],
                    "breakout": "above_resistance",
                }
                signals.append(_make_signal(symbol, latest_date, "bullish", detail))
                log.info("support_resistance.signal.bullish", symbol=symbol,
                         date=str(latest_date), level=lv["price"])
                break  # at most one signal per day

            if prev_close >= lv["zone_low"] and current_close < lv["zone_low"]:
                detail = {
                    "level": lv["price"],
                    "zone": [lv["zone_low"], lv["zone_high"]],
                    "touches": lv["touches"],
                    "breakout": "below_support",
                }
                signals.append(_make_signal(symbol, latest_date, "bearish", detail))
                log.info("support_resistance.signal.bearish", symbol=symbol,
                         date=str(latest_date), level=lv["price"])
                break

    return snapshots, signals


def compute_sr_levels(df: pd.DataFrame, params: dict[str, Any]) -> list[dict[str, Any]]:
    """
    计算支撑阻力水平位列表，供 dashboard 可视化调用。

    Returns list[dict] sorted by (touches DESC, span_days DESC)，每个 dict 包含：
      price       float  聚类中心价格
      zone_low    float  区间下边界（极值最低价）
      zone_high   float  区间上边界（极值最高价）
      touches     int    触及次数
      span_days   int    首次到末次触及间隔天数
      level_type  str    "support" | "resistance" | "both"
      last_touch  Timestamp
    """
    swing_order: int = params.get("swing_order", 3)
    cluster_pct: float = params.get("cluster_pct", 1.5)
    min_touches: int = params.get("min_touches", 2)
    min_span_days: int = params.get("min_span_days", 10)
    max_levels: int = params.get("max_levels", 5)
    max_dist_pct: float = params.get("max_dist_pct", 15.0)
    extreme_order: int = params.get("extreme_order", 60)
    extreme_pct: float = params.get("extreme_pct", 5.0)
    lookback: int = params.get("lookback_window", 500)  # bars (trading days)

    df = df.iloc[-lookback:] if len(df) > lookback else df
    if len(df) < _MIN_ROWS:
        return []

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

    # Single-pass clustering: add to cluster while within cluster_pct% of running mean
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
            # Swing point dates and their exact prices, for chart annotation
            "swing_dates": list(pd.DatetimeIndex(cd)),
            "swing_prices": [round(p, 2) for p in cp],
        })

    # --- Extreme single-point pivots ---
    # A pivot qualifies as extreme if:
    #   1. It is a local extremum within extreme_order bars on each side
    #   2. Its price deviates from the surrounding close mean by >= extreme_pct%
    #   3. It is not already covered by an existing regular level (within cluster_pct%)
    if extreme_order >= 2 and len(df) >= extreme_order * 2 + 1:
        ex_high_idx = argrelextrema(df["high"].values, np.greater_equal, order=extreme_order)[0]
        ex_low_idx = argrelextrema(df["low"].values, np.less_equal, order=extreme_order)[0]

        for idx, is_high in [(i, True) for i in ex_high_idx] + [(i, False) for i in ex_low_idx]:
            price = float(df["high"].values[idx] if is_high else df["low"].values[idx])
            lo = max(0, idx - extreme_order)
            hi = min(len(df), idx + extreme_order + 1)
            surrounding = np.concatenate([
                df["close"].values[lo:idx],
                df["close"].values[idx + 1:hi],
            ])
            surrounding_mean = float(surrounding.mean())
            deviation = (price - surrounding_mean) / surrounding_mean * 100
            if is_high and deviation < extreme_pct:
                continue
            if not is_high and deviation > -extreme_pct:
                continue
            # Skip if already covered by a regular level
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

    # Filter out levels too far from current price before ranking
    levels = [
        lv for lv in levels
        if abs(lv["price"] - current_close) / current_close * 100 <= max_dist_pct
    ]

    # Extreme levels shown first; among regular levels sort by (touches DESC, span_days DESC)
    levels.sort(key=lambda x: (x["is_extreme"], x["touches"], x["span_days"]), reverse=True)
    return levels[:max_levels]


def compute_raw_swings(
    df: pd.DataFrame,
    swing_order: int = 3,
    lookback_window: int = 500,
) -> tuple[list, list]:
    """
    返回 lookback_window 根K线内所有原始摆动点（未经聚类/过滤）。

    Returns:
        (dates, prices): 两个等长列表
    """
    df = df.iloc[-lookback_window:] if len(df) > lookback_window else df
    if len(df) < _MIN_ROWS:
        return [], []

    high_idx = argrelextrema(df["high"].values, np.greater_equal, order=swing_order)[0]
    low_idx = argrelextrema(df["low"].values, np.less_equal, order=swing_order)[0]

    dates = list(df.index[high_idx]) + list(df.index[low_idx])
    prices = list(df["high"].values[high_idx]) + list(df["low"].values[low_idx])
    return dates, prices


def _make_signal(
    symbol: str, d: date, signal_type: str, detail: dict[str, Any]
) -> dict[str, Any]:
    raw = f"{symbol}|{d}|support_resistance"
    signal_id = hashlib.sha1(raw.encode()).hexdigest()[:16]
    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "date": d,
        "strategy": "support_resistance",
        "signal_type": signal_type,
        "detail_json": detail,
    }
