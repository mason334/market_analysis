from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from market_analysis.db import queries, schema


class _FakeConnection:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple[Any, ...] | None]] = []
        self.committed = False

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, params: tuple[Any, ...] | None = None) -> None:
        self.executions.append((statement, params))

    def commit(self) -> None:
        self.committed = True


def _summary(lookback: int = 40) -> dict[str, Any]:
    return {
        "symbol": "TEST",
        "date": date(2026, 7, 15),
        "lookback_bars": lookback,
        "observation_count": lookback,
        "segment_count": 1,
        "change_point_count": 0,
        "selected_rss": 0.01,
        "single_segment_rss": 0.2,
        "selected_bic": -100.0,
        "single_segment_bic": -50.0,
        "bic_improvement": 50.0,
        "min_segment_bars": 5,
        "max_segments": 4,
        "bic_penalty_multiplier": 3.0,
        "search_mode": "exact",
        "is_global_optimum": True,
        "candidates_evaluated": 100,
        "refinement_converged": True,
        "search_config": {"exhaustive_batch_size": 2048},
        "search_diagnostics": [],
        "method": "continuous_piecewise_log_linear_deterministic_hybrid_bic",
        "calculation_version": "adaptive_trend_v3",
    }


def _segment() -> dict[str, Any]:
    return {
        "symbol": "TEST",
        "date": date(2026, 7, 15),
        "lookback_bars": 40,
        "segment_index": 0,
        "start_date": date(2026, 5, 20),
        "end_date": date(2026, 6, 16),
        "start_bar_index": 0,
        "end_bar_index": 19,
        "observation_count": 20,
        "log_slope_per_bar": -0.01,
        "linearity_r2": 0.9,
        "fitted_log_return": -0.19,
        "fitted_anchor_log_price": 4.5,
        "actual_log_return": -0.18,
        "realized_volatility_daily": 0.02,
        "vol_adjusted_trend": -2.1,
        "efficiency_ratio": 0.8,
        "largest_move_log_return": -0.04,
        "largest_move_date": date(2026, 6, 2),
        "largest_move_bar_index": 9,
        "largest_move_path_share": 0.2,
        "method": "continuous_piecewise_log_linear_deterministic_hybrid_bic",
        "calculation_version": "adaptive_trend_v3",
    }


def test_adaptive_upsert_replaces_segments_and_writes_both_tables(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(queries, "get_conn", lambda: connection)

    queries.upsert_trend_segmentation_daily([_summary()], [_segment()])

    assert connection.committed
    statements = [statement for statement, _ in connection.executions]
    assert any("DELETE FROM trend_segment_daily" in statement for statement in statements)
    summary_execution = next(
        execution
        for execution in connection.executions
        if "INSERT INTO trend_segmentation_daily" in execution[0]
    )
    segment_execution = next(
        execution
        for execution in connection.executions
        if "INSERT INTO trend_segment_daily" in execution[0]
    )
    assert len(summary_execution[1] or ()) == 22
    assert len(segment_execution[1] or ()) == 23


def test_adaptive_upsert_rejects_mixed_snapshots(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(queries, "get_conn", lambda: connection)
    other = _summary(60)
    other["symbol"] = "OTHER"

    with pytest.raises(ValueError, match="one symbol/date"):
        queries.upsert_trend_segmentation_daily([_summary(), other], [])


def test_schema_creates_adaptive_summary_and_detail_tables(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(schema, "get_conn", lambda: connection)

    schema.init_schema()

    statements = "\n".join(statement for statement, _ in connection.executions)
    assert connection.committed
    assert "CREATE TABLE IF NOT EXISTS trend_segmentation_daily" in statements
    assert "CREATE TABLE IF NOT EXISTS trend_segment_daily" in statements
    assert "ADD COLUMN IF NOT EXISTS largest_move_log_return" in statements
    assert "ADD COLUMN IF NOT EXISTS search_mode" in statements
    assert "ADD COLUMN IF NOT EXISTS search_diagnostics" in statements
    assert "ADD COLUMN IF NOT EXISTS fitted_anchor_log_price" in statements
    assert "trend_segmentation_daily_date_lookback_idx" in statements
    assert "trend_segment_daily_symbol_date_idx" in statements
