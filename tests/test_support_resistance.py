from __future__ import annotations

import pandas as pd
from conftest import make_ohlcv

from market_analysis.indicators.support_resistance import (
    compute_raw_swings,
    compute_sr_levels,
    support_resistance,
)

PARAMS = {
    "swing_order": 3,
    "cluster_pct": 1.5,
    "min_touches": 2,
    "min_span_days": 10,
    "max_levels": 10,
    "max_dist_pct": 20,
    "extreme_order": 60,
    "extreme_pct": 5.0,
    "lookback_window": 500,
    "atr_period": 14,
    "watch_atr_mult": 1.5,
    "warn_atr_mult": 0.5,
    "breakout_window": 5,
}


def test_support_resistance_returns_snapshot_schema(ohlcv: pd.DataFrame) -> None:
    result = support_resistance("TEST", ohlcv, PARAMS)

    assert result is not None
    assert result["symbol"] == "TEST"
    assert result["date"] == ohlcv.index[-1].date()
    assert {
        "nearest_support",
        "nearest_resistance",
        "dist_support_pct",
        "dist_support_atr",
        "dist_resistance_pct",
        "dist_resistance_atr",
        "atr_14",
        "sr_status",
        "breakout_5d",
        "breakout_level",
    } <= result.keys()


def test_support_resistance_empty_df_returns_none() -> None:
    assert support_resistance("EMPTY", pd.DataFrame(), PARAMS) is None


def test_compute_raw_swings_returns_matching_dates_and_prices() -> None:
    df = make_ohlcv(120)
    dates, prices = compute_raw_swings(df, swing_order=3, lookback_window=80)

    assert len(dates) == len(prices)
    assert all(date in df.index[-80:] for date in dates)


def test_compute_sr_levels_respects_max_levels() -> None:
    df = make_ohlcv(250)
    params = {**PARAMS, "max_levels": 3}

    levels = compute_sr_levels(df, params)

    assert len(levels) <= 3
    for level in levels:
        assert {
            "price",
            "zone_low",
            "zone_high",
            "touches",
            "span_days",
            "level_type",
            "last_touch",
            "is_extreme",
        } <= level.keys()
