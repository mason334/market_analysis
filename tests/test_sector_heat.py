from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from market_analysis.analytics.sector_heat import compute_sector_heat, compute_sector_heat_history


def _make_wide_df(n: int = 80, n_stocks: int = 5, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-02", periods=n)
    data = rng.uniform(1e7, 1e9, (n, n_stocks))
    cols = [f"STOCK{i}" for i in range(n_stocks)]
    df = pd.DataFrame(data, index=dates, columns=cols)
    df.index.name = "date"
    return df


def test_basic_result():
    wide = _make_wide_df(80)
    result = compute_sector_heat("XLK", wide)
    assert result is not None
    assert result["universe_ticker"] == "XLK"
    assert result["sector_turnover"] > 0
    assert result["constituent_count"] == 5
    assert result["turnover_ma20"] is not None
    assert result["turnover_ratio"] is not None
    assert result["turnover_zscore"] is not None


def test_result_date_is_latest():
    wide = _make_wide_df(80)
    result = compute_sector_heat("TEST", wide)
    assert result is not None
    expected_date = wide.index[-1].date()
    assert result["date"] == expected_date


def test_insufficient_data_returns_none():
    wide = _make_wide_df(n=10)
    result = compute_sector_heat("TEST", wide)
    assert result is None


def test_zscore_none_when_fewer_than_60_bars():
    # 30 bars: enough for MA20 but not for 60-day zscore
    wide = _make_wide_df(n=30)
    result = compute_sector_heat("TEST", wide)
    assert result is not None
    assert result["turnover_zscore"] is None


def test_zscore_present_with_60_bars():
    wide = _make_wide_df(n=70)
    result = compute_sector_heat("TEST", wide)
    assert result is not None
    assert result["turnover_zscore"] is not None


def test_handles_partial_nans():
    wide = _make_wide_df(80, n_stocks=5)
    # Some stocks missing on a few dates (new listing, etc.)
    wide.iloc[5:15, 2] = np.nan
    wide.iloc[0:10, 4] = np.nan
    result = compute_sector_heat("TEST", wide)
    assert result is not None
    assert result["sector_turnover"] > 0


def test_empty_df_returns_none():
    result = compute_sector_heat("TEST", pd.DataFrame())
    assert result is None


def test_ratio_greater_than_one_on_surge_day():
    """On a day with turnover 3x the 20-day average, ratio should be ~3."""
    wide = _make_wide_df(80, n_stocks=3, seed=7)
    # Multiply last row by 3
    wide.iloc[-1] = wide.iloc[-1] * 3.0
    result = compute_sector_heat("SURGE", wide)
    assert result is not None
    assert result["turnover_ratio"] > 2.0


# ---------------------------------------------------------------------------
# compute_sector_heat_history tests
# ---------------------------------------------------------------------------

def test_history_returns_dataframe_with_all_dates():
    wide = _make_wide_df(80)
    result = compute_sector_heat_history("XLK", wide)
    assert not result.empty
    assert len(result) == 80
    assert list(result.columns) == [
        "universe_ticker", "date", "sector_turnover", "constituent_count",
        "turnover_ma20", "turnover_ratio", "turnover_zscore",
    ]


def test_history_ma20_nan_for_first_19_rows():
    wide = _make_wide_df(80)
    result = compute_sector_heat_history("XLK", wide)
    # First 19 rows should have NaN ma20 (need 20 bars)
    assert result["turnover_ma20"].iloc[:19].isna().all()
    assert result["turnover_ma20"].iloc[19:].notna().all()


def test_history_zscore_nan_before_60_bars():
    wide = _make_wide_df(80)
    result = compute_sector_heat_history("XLK", wide)
    # z-score requires 60 bars, so first 59 should be NaN
    assert result["turnover_zscore"].iloc[:59].isna().all()
    assert result["turnover_zscore"].iloc[59:].notna().all()


def test_history_latest_row_matches_snapshot():
    """Last row of history should match compute_sector_heat output."""
    wide = _make_wide_df(80)
    snap = compute_sector_heat("XLK", wide)
    hist = compute_sector_heat_history("XLK", wide)
    assert snap is not None
    assert not hist.empty
    last = hist.iloc[-1]
    assert abs(last["sector_turnover"] - snap["sector_turnover"]) < 1.0
    assert abs(last["turnover_ma20"] - snap["turnover_ma20"]) < 1.0


def test_history_empty_df_returns_empty():
    result = compute_sector_heat_history("TEST", pd.DataFrame())
    assert result.empty


def test_history_warmup_data_filtered_correctly():
    """Simulate warm-up usage: 90 extra rows before display range."""
    wide_full = _make_wide_df(n=170)  # 90 warmup + 80 display
    hist_full = compute_sector_heat_history("XLK", wide_full)
    # The last 80 rows should have valid zscore (warmup gave enough history)
    assert hist_full["turnover_zscore"].iloc[-80:].notna().all()


def test_constituent_count_matches_non_nan_stocks():
    wide = _make_wide_df(80, n_stocks=4)
    # Set 2 stocks to NaN on the last day
    wide.iloc[-1, 0] = np.nan
    wide.iloc[-1, 2] = np.nan
    result = compute_sector_heat("TEST", wide)
    assert result is not None
    assert result["constituent_count"] == 2
