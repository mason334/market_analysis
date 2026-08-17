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


class _FetchConnection(_FakeConnection):
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        super().__init__()
        self.rows = rows

    def execute(
        self, statement: str, params: tuple[Any, ...] | None = None
    ) -> _FetchConnection:
        self.executions.append((statement, params))
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


def _summary(lookback: int = 40) -> dict[str, Any]:
    return {
        "symbol": "TEST",
        "date": date(2026, 7, 15),
        "requested_lookback_bars": 250,
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
        "calculation_version": "adaptive_segmentation_v3",
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
        "calculation_version": "adaptive_segmentation_v3",
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
    assert len(summary_execution[1] or ()) == 23
    assert (summary_execution[1] or ())[2:5] == (250, 40, 40)
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
    assert "ADD COLUMN IF NOT EXISTS requested_lookback_bars" in statements
    assert "trend_segmentation_requested_lookback_check" in statements
    assert "ADD COLUMN IF NOT EXISTS fitted_anchor_log_price" in statements
    assert "trend_segmentation_daily_date_lookback_idx" in statements
    assert "trend_segment_daily_symbol_date_idx" in statements


def test_adaptive_summary_snapshot_returns_requested_and_actual_lookbacks(
    monkeypatch,
) -> None:
    expected = _summary()
    row = tuple(expected[column] for column in queries._TREND_SEGMENTATION_COLS)
    connection = _FetchConnection([row])
    monkeypatch.setattr(queries, "get_conn", lambda: connection)

    result = queries.fetch_trend_segmentation_snapshot(date(2026, 7, 15))

    assert result == [expected]
    assert result[0]["requested_lookback_bars"] == 250
    assert result[0]["lookback_bars"] == 40


def test_adaptive_input_metadata_reads_capped_windows_in_one_query(
    monkeypatch,
) -> None:
    target_date = date(2026, 8, 14)
    connection = _FetchConnection(
        [
            ("AAPL", target_date, 250),
            ("NEW", target_date, 65),
        ]
    )
    monkeypatch.setattr(queries, "get_source_conn", lambda: connection)

    result = queries.fetch_adaptive_input_metadata(
        ["AAPL", "NEW"],
        source="tiingo",
        target_date=target_date,
        max_lookback_bars=250,
    )

    assert result == {
        "AAPL": (target_date, 250),
        "NEW": (target_date, 65),
    }
    statement, params = connection.executions[0]
    assert "SELECT UNNEST(%s::text[])" in statement
    assert "source = %s" in statement
    assert "LIMIT %s" in statement
    assert params == (
        ["AAPL", "NEW"],
        "tiingo",
        target_date,
        target_date,
        250,
    )


def test_adaptive_close_window_filters_source_date_and_limit(monkeypatch) -> None:
    target_date = date(2026, 8, 14)
    connection = _FetchConnection(
        [
            (date(2026, 8, 13), 100.0),
            (target_date, 101.0),
        ]
    )
    monkeypatch.setattr(queries, "get_source_conn", lambda: connection)

    result = queries.fetch_adaptive_close_window(
        "AAPL",
        source="tiingo",
        target_date=target_date,
        observation_count=250,
    )

    assert result["close"].tolist() == [100.0, 101.0]
    statement, params = connection.executions[0]
    assert "source = %s" in statement
    assert "date <= %s" in statement
    assert "LIMIT %s" in statement
    assert params == ("AAPL", "tiingo", target_date, target_date, 250)


def test_adaptive_resume_candidates_include_segment_integrity(monkeypatch) -> None:
    target_date = date(2026, 8, 14)
    expected = {
        column: None for column in queries._ADAPTIVE_RESUME_CANDIDATE_COLS
    }
    expected.update(
        {
            "symbol": "AAPL",
            "date": target_date,
            "requested_lookback_bars": 250,
            "lookback_bars": 250,
            "segment_count": 10,
            "persisted_segment_count": 10,
            "min_segment_index": 0,
            "max_segment_index": 9,
            "segment_identity_matches": True,
        }
    )
    row = tuple(
        expected[column] for column in queries._ADAPTIVE_RESUME_CANDIDATE_COLS
    )
    connection = _FetchConnection([row])
    monkeypatch.setattr(queries, "get_conn", lambda: connection)

    result = queries.fetch_adaptive_resume_candidates(
        ["AAPL"],
        [250],
        target_date=target_date,
        calculation_version="adaptive_segmentation_v3",
    )

    assert result == [expected]
    statement, params = connection.executions[0]
    assert "DISTINCT ON (summary.symbol, summary.requested_lookback_bars)" in statement
    assert "COUNT(segment.segment_index) AS persisted_segment_count" in statement
    assert "BOOL_AND" in statement
    assert params == (
        ["AAPL"],
        [250],
        "adaptive_segmentation_v3",
        target_date,
        target_date,
    )
