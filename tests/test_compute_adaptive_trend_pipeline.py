from __future__ import annotations

import numpy as np
import pandas as pd

from market_analysis.pipeline import compute_adaptive_trend


def test_single_symbol_pipeline_returns_dashboard_json_payload(monkeypatch) -> None:
    index = pd.bdate_range("2026-01-02", periods=40)
    frame = pd.DataFrame(
        {
            "open": np.exp(4.0 + 0.01 * np.arange(40)),
            "high": np.exp(4.01 + 0.01 * np.arange(40)),
            "low": np.exp(3.99 + 0.01 * np.arange(40)),
            "close": np.exp(4.0 + 0.01 * np.arange(40)),
            "volume": np.full(40, 1_000),
        },
        index=index,
    )
    monkeypatch.setattr(compute_adaptive_trend, "fetch_ohlcv", lambda *_args, **_kwargs: frame)

    result = compute_adaptive_trend.compute_adaptive_trend_for_symbol(
        "test",
        lookback_bars=40,
        min_segment_bars=5,
        max_segments=4,
        bic_penalty_multiplier=3.0,
    )

    assert result["summary"]["symbol"] == "TEST"
    assert result["summary"]["calculation_version"] == "adaptive_trend_v3"
    assert len(result["segments"]) == 1
    assert len(result["fitted_points"]) == 40
    assert result["fitted_points"][-1]["date"] == index[-1].date()
    assert result["elapsed_seconds"] >= 0.0
