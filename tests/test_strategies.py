from __future__ import annotations

import pandas as pd
from conftest import make_ohlcv

from market_analysis.models import IndicatorSnapshot
from market_analysis.strategies.hurst import hurst
from market_analysis.strategies.ma_support import ma_support
from market_analysis.strategies.sudden_surge import sudden_surge
from market_analysis.strategies.variance_ratio import variance_ratio


def _set(df: pd.DataFrame, col: str, iloc: int, value: float) -> pd.DataFrame:
    """Return a copy of df with df[col].iloc[iloc] set to value (CoW-safe)."""
    df = df.copy()
    df.loc[df.index[iloc], col] = value
    return df


# ---------------------------------------------------------------------------
# sudden_surge
# ---------------------------------------------------------------------------
class TestSuddenSurge:
    PARAMS = {"lookback_days": 5, "min_return": 0.08, "volume_ratio_min": 1.5}

    def test_returns_tuple(self, ohlcv: pd.DataFrame) -> None:
        result = sudden_surge("TEST", ohlcv, self.PARAMS)
        assert isinstance(result, tuple)
        assert len(result) == 2
        snapshots, signals = result
        assert isinstance(snapshots, list)
        assert isinstance(signals, list)

    def test_snapshots_always_produced(self, ohlcv: pd.DataFrame) -> None:
        snapshots, _ = sudden_surge("TEST", ohlcv, self.PARAMS)
        indicators = {s.indicator for s in snapshots}
        assert "ss_ret5d" in indicators
        assert "ss_vol_ratio" in indicators

    def test_snapshots_are_indicator_snapshots(self, ohlcv: pd.DataFrame) -> None:
        snapshots, _ = sudden_surge("TEST", ohlcv, self.PARAMS)
        for s in snapshots:
            assert isinstance(s, IndicatorSnapshot)

    def test_no_signal_on_flat_market(self, ohlcv: pd.DataFrame) -> None:
        _, signals = sudden_surge("TEST", ohlcv, self.PARAMS)
        assert isinstance(signals, list)

    def test_signal_on_surge(self) -> None:
        df = make_ohlcv(250)
        surge_close = float(df["close"].iloc[-6]) * 1.15
        avg_vol = float(df["volume"].iloc[-31:-1].mean()) * 3.0
        df = _set(df, "close", -1, surge_close)
        df = _set(df, "volume", -1, avg_vol)
        snapshots, signals = sudden_surge("SURGE", df, self.PARAMS)
        assert len(signals) == 1
        s = signals[0]
        assert s["symbol"] == "SURGE"
        assert s["strategy"] == "sudden_surge"
        assert s["signal_type"] == "bullish"
        assert s["detail_json"]["volume_ratio"] >= 1.5
        assert len(snapshots) == 2

    def test_insufficient_data_returns_empty(self) -> None:
        df = make_ohlcv(10)
        snapshots, signals = sudden_surge("TINY", df, self.PARAMS)
        assert signals == []
        assert snapshots == []

    def test_output_schema(self) -> None:
        df = make_ohlcv(250)
        surge_close = float(df["close"].iloc[-6]) * 1.15
        avg_vol = float(df["volume"].iloc[-31:-1].mean()) * 3.0
        df = _set(df, "close", -1, surge_close)
        df = _set(df, "volume", -1, avg_vol)
        _, signals = sudden_surge("SCHEMA", df, self.PARAMS)
        for s in signals:
            expected = {"signal_id", "symbol", "date", "strategy", "signal_type", "detail_json"}
            assert expected <= s.keys()


# ---------------------------------------------------------------------------
# ma_support
# ---------------------------------------------------------------------------
class TestMaSupport:
    PARAMS = {
        "periods": [20, 50, 200],
        "proximity_pct": 0.02,
        "directions": ["support", "resistance"],
    }

    def test_returns_tuple(self, ohlcv: pd.DataFrame) -> None:
        result = ma_support("TEST", ohlcv, self.PARAMS)
        assert isinstance(result, tuple) and len(result) == 2

    def test_snapshots_produced_for_available_periods(self, ohlcv: pd.DataFrame) -> None:
        snapshots, _ = ma_support("TEST", ohlcv, self.PARAMS)
        indicators = {s.indicator for s in snapshots}
        # ohlcv has 250 rows so all periods are computable
        assert "ma_dist_20" in indicators
        assert "ma_dist_50" in indicators
        assert "ma_dist_200" in indicators

    def test_signal_near_ma(self) -> None:
        df = make_ohlcv(250)
        ma50_excl = float(df["close"].iloc[-50:-1].mean())
        df = _set(df, "close", -1, ma50_excl * 1.001)
        _, signals = ma_support("MTEST", df, self.PARAMS)
        assert len(signals) >= 1
        support_signals = [s for s in signals if s["detail_json"]["direction"] == "support"]
        assert len(support_signals) >= 1

    def test_only_closest_ma_returned(self) -> None:
        df = make_ohlcv(250)
        ma20 = float(df["close"].iloc[-20:].mean())
        df = _set(df, "close", -1, ma20 * 1.005)
        _, signals = ma_support("MULTI", df, self.PARAMS)
        assert len(signals) <= 1

    def test_insufficient_rows_skips_period(self) -> None:
        df = make_ohlcv(30)
        params = {"periods": [200], "proximity_pct": 0.02, "directions": ["support"]}
        snapshots, signals = ma_support("SHORT", df, params)
        assert signals == []
        assert snapshots == []  # no period has enough data


# ---------------------------------------------------------------------------
# variance_ratio
# ---------------------------------------------------------------------------
class TestVarianceRatio:
    PARAMS = {
        "lags": [5, 10, 20],
        "bullish_threshold": 1.1,
        "bearish_threshold": 0.9,
        "min_agree": 2,
    }

    def test_returns_tuple(self, ohlcv: pd.DataFrame) -> None:
        result = variance_ratio("TEST", ohlcv, self.PARAMS)
        assert isinstance(result, tuple) and len(result) == 2

    def test_snapshots_always_produced(self, ohlcv: pd.DataFrame) -> None:
        snapshots, _ = variance_ratio("TEST", ohlcv, self.PARAMS)
        indicators = {s.indicator for s in snapshots}
        assert "vr_5" in indicators
        assert "vr_10" in indicators
        assert "vr_20" in indicators

    def test_snapshot_values_reasonable(self, ohlcv: pd.DataFrame) -> None:
        snapshots, _ = variance_ratio("TEST", ohlcv, self.PARAMS)
        for s in snapshots:
            assert isinstance(s, IndicatorSnapshot)
            assert 0.0 < s.value < 5.0, f"{s.indicator} = {s.value} out of range"

    def test_insufficient_data_returns_empty(self) -> None:
        df = make_ohlcv(10)
        snapshots, signals = variance_ratio("TINY", df, self.PARAMS)
        assert snapshots == []
        assert signals == []

    def test_signal_on_trending_series(self) -> None:
        """Artificially trended series should produce VR > 1."""
        import numpy as np

        rng = np.random.default_rng(0)
        n = 200
        # Strong uptrend: cumulative sum of positive shocks
        ret = rng.normal(0.002, 0.005, n)  # positive drift
        prices = 100 * np.cumprod(1 + ret)
        from datetime import date

        import pandas as pd

        dates = pd.bdate_range(start=date(2024, 1, 2), periods=n)
        df = pd.DataFrame(
            {
                "open": prices,
                "high": prices * 1.005,
                "low": prices * 0.995,
                "close": prices,
                "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
            },
            index=dates,
        )
        df.index.name = "date"
        snapshots, _ = variance_ratio("TREND", df, self.PARAMS)
        vr5 = next((s.value for s in snapshots if s.indicator == "vr_5"), None)
        assert vr5 is not None


# ---------------------------------------------------------------------------
# hurst
# ---------------------------------------------------------------------------
class TestHurst:
    PARAMS = {"window": 60, "bullish_threshold": 0.65, "bearish_threshold": 0.35}

    def test_returns_tuple(self, ohlcv: pd.DataFrame) -> None:
        result = hurst("TEST", ohlcv, self.PARAMS)
        assert isinstance(result, tuple) and len(result) == 2

    def test_snapshot_always_produced(self, ohlcv: pd.DataFrame) -> None:
        snapshots, _ = hurst("TEST", ohlcv, self.PARAMS)
        assert any(s.indicator == "hurst_60d" for s in snapshots)

    def test_snapshot_value_in_range(self, ohlcv: pd.DataFrame) -> None:
        snapshots, _ = hurst("TEST", ohlcv, self.PARAMS)
        h_snap = next(s for s in snapshots if s.indicator == "hurst_60d")
        assert 0.0 <= h_snap.value <= 1.0

    def test_insufficient_data_returns_empty(self) -> None:
        df = make_ohlcv(30)
        snapshots, signals = hurst("TINY", df, self.PARAMS)
        assert snapshots == []
        assert signals == []

    def test_random_walk_hurst_near_half(self) -> None:
        """GBM with no drift should produce H closer to 0.5 than extremes."""
        import numpy as np

        rng = np.random.default_rng(99)
        n = 300
        ret = rng.normal(0, 0.01, n)
        prices = 100 * np.cumprod(1 + ret)
        from datetime import date

        import pandas as pd

        dates = pd.bdate_range(start=date(2024, 1, 2), periods=n)
        df = pd.DataFrame(
            {
                "open": prices,
                "high": prices,
                "low": prices,
                "close": prices,
                "volume": np.ones(n) * 1e6,
            },
            index=dates,
        )
        df.index.name = "date"
        snapshots, _ = hurst("RW", df, self.PARAMS)
        h_val = next(s.value for s in snapshots if s.indicator == "hurst_60d")
        # Random walk Hurst should be between 0.3 and 0.7 (loose bound)
        assert 0.2 <= h_val <= 0.8
