from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest


def make_ohlcv(n: int = 250, seed: int = 42) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame with DatetimeIndex."""
    rng = np.random.default_rng(seed)
    start = date(2025, 1, 2)
    dates = pd.bdate_range(start=start, periods=n)
    close = 100.0 * np.cumprod(1 + rng.normal(0, 0.01, n))
    df = pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.003, n)),
            "high": close * (1 + rng.uniform(0, 0.01, n)),
            "low": close * (1 - rng.uniform(0, 0.01, n)),
            "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
        },
        index=dates,
    )
    df.index.name = "date"
    return df


@pytest.fixture
def ohlcv() -> pd.DataFrame:
    return make_ohlcv()
