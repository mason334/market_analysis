from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from market_analysis.pipeline import preview_manual_pivot_analysis


def _piecewise_frame(periods: int = 80) -> pd.DataFrame:
    index = pd.bdate_range("2026-01-02", periods=periods)
    first = np.linspace(4.0, 4.25, periods // 2)
    second = np.linspace(4.25, 4.05, periods - periods // 2)
    close = np.exp(np.concatenate([first, second]))
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": np.full(periods, 1_000.0),
        },
        index=index,
    )


def test_manual_pivot_analysis_returns_both_stages_without_writes(monkeypatch) -> None:
    frame = _piecewise_frame()
    monkeypatch.setattr(
        preview_manual_pivot_analysis,
        "fetch_ohlcv",
        lambda *_args, **_kwargs: frame,
    )

    result = preview_manual_pivot_analysis.compute_manual_pivot_analysis_for_symbol(
        "test",
        start_date=frame.index[5].date(),
        end_date=frame.index[-6].date(),
        adaptive_min_segment_bars=5,
        adaptive_max_segments=4,
        adaptive_bic_penalty_multiplier=2.0,
        pivot_search_radius_bars=3,
        pivot_min_segment_bars=5,
        pivot_min_abs_fitted_log_return=0.02,
        pivot_min_linearity_r2=0.35,
        pivot_min_abs_vol_adjusted_trend=0.75,
    )

    assert result["resolved_window"]["symbol"] == "TEST"
    assert result["resolved_window"]["observation_count"] == 70
    assert result["adaptive"]["summary"]["calculation_version"] == (
        "adaptive_segmentation_v4"
    )
    assert result["pivot"]["summary"]["calculation_version"] == (
        "pivot_refined_segmentation_v4"
    )
    assert len(result["ohlcv_points"]) == 70
    assert len(result["adaptive"]["fitted_points"]) == 70
    assert result["pivot"]["fitted_points"]
    assert result["resolved_parameters"]["pivot"]["search_radius_bars"] == 3


@pytest.mark.parametrize("periods", [39, 501])
def test_manual_pivot_analysis_enforces_observation_bounds(monkeypatch, periods) -> None:
    frame = _piecewise_frame(periods)
    monkeypatch.setattr(
        preview_manual_pivot_analysis,
        "fetch_ohlcv",
        lambda *_args, **_kwargs: frame,
    )

    with pytest.raises(ValueError, match="observations"):
        preview_manual_pivot_analysis.compute_manual_pivot_analysis_for_symbol(
            "AAPL",
            start_date=frame.index[0].date(),
            end_date=frame.index[-1].date(),
        )


def test_manual_pivot_analysis_rejects_invalid_symbol() -> None:
    with pytest.raises(ValueError, match="symbol"):
        preview_manual_pivot_analysis.compute_manual_pivot_analysis_for_symbol(
            "BRK.B",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )


def test_yaml_default_max_segments_uses_production_window_recommendation(
    monkeypatch,
) -> None:
    frame = _piecewise_frame(60)
    monkeypatch.setattr(
        preview_manual_pivot_analysis,
        "fetch_ohlcv",
        lambda *_args, **_kwargs: frame,
    )

    result = preview_manual_pivot_analysis.compute_manual_pivot_analysis_for_symbol(
        "AAPL",
        start_date=frame.index[0].date(),
        end_date=frame.index[-1].date(),
    )

    assert result["request"]["parameters"]["adaptive_max_segments"] is None
    assert result["resolved_parameters"]["adaptive"]["max_segments"] == 5
