from __future__ import annotations

import pandas as pd
from conftest import make_ohlcv

from market_analysis.strategies.ma_support import ma_support
from market_analysis.strategies.sudden_surge import sudden_surge


def _set(df: pd.DataFrame, col: str, iloc: int, value: float) -> pd.DataFrame:
    """Return a copy of df with df[col].iloc[iloc] set to value (CoW-safe)."""
    df = df.copy()
    df.loc[df.index[iloc], col] = value
    return df


class TestSuddenSurge:
    PARAMS = {"lookback_days": 5, "min_return": 0.08, "volume_ratio_min": 1.5}

    def test_no_signal_on_flat_market(self, ohlcv: pd.DataFrame) -> None:
        signals = sudden_surge("TEST", ohlcv, self.PARAMS)
        assert isinstance(signals, list)

    def test_signal_on_surge(self) -> None:
        df = make_ohlcv(250)
        surge_close = float(df["close"].iloc[-6]) * 1.15
        avg_vol = float(df["volume"].iloc[-31:-1].mean()) * 3.0
        df = _set(df, "close", -1, surge_close)
        df = _set(df, "volume", -1, avg_vol)
        signals = sudden_surge("SURGE", df, self.PARAMS)
        assert len(signals) == 1
        s = signals[0]
        assert s["symbol"] == "SURGE"
        assert s["strategy"] == "sudden_surge"
        assert s["signal_type"] == "bullish"
        assert s["detail_json"]["volume_ratio"] >= 1.5

    def test_insufficient_data_returns_empty(self) -> None:
        df = make_ohlcv(10)
        signals = sudden_surge("TINY", df, self.PARAMS)
        assert signals == []

    def test_output_schema(self) -> None:
        df = make_ohlcv(250)
        surge_close = float(df["close"].iloc[-6]) * 1.15
        avg_vol = float(df["volume"].iloc[-31:-1].mean()) * 3.0
        df = _set(df, "close", -1, surge_close)
        df = _set(df, "volume", -1, avg_vol)
        signals = sudden_surge("SCHEMA", df, self.PARAMS)
        for s in signals:
            expected = {"signal_id", "symbol", "date", "strategy", "signal_type", "detail_json"}
            assert expected <= s.keys()


class TestMaSupport:
    PARAMS = {
        "periods": [20, 50, 200],
        "proximity_pct": 0.02,
        "directions": ["support", "resistance"],
    }

    def test_returns_list(self, ohlcv: pd.DataFrame) -> None:
        signals = ma_support("TEST", ohlcv, self.PARAMS)
        assert isinstance(signals, list)

    def test_signal_near_ma(self) -> None:
        df = make_ohlcv(250)
        # Compute MA50 excluding the last bar, then set last bar just above it
        ma50_excl = float(df["close"].iloc[-50:-1].mean())
        # New MA50 with last bar = ma50_excl * 1.001:
        # new_ma50 = (49 * ma50_excl + ma50_excl * 1.001) / 50 ≈ ma50_excl * 1.00002
        # close = ma50_excl * 1.001 > new_ma50 → direction = support
        df = _set(df, "close", -1, ma50_excl * 1.001)
        signals = ma_support("MTEST", df, self.PARAMS)
        assert len(signals) >= 1
        # The signal triggered for the smallest proximity should be support
        support_signals = [s for s in signals if s["detail_json"]["direction"] == "support"]
        assert len(support_signals) >= 1

    def test_only_closest_ma_returned(self) -> None:
        df = make_ohlcv(250)
        ma20 = float(df["close"].iloc[-20:].mean())
        df = _set(df, "close", -1, ma20 * 1.005)
        signals = ma_support("MULTI", df, self.PARAMS)
        assert len(signals) <= 1

    def test_insufficient_rows_skips_period(self) -> None:
        df = make_ohlcv(30)
        params = {"periods": [200], "proximity_pct": 0.02, "directions": ["support"]}
        signals = ma_support("SHORT", df, params)
        assert signals == []
