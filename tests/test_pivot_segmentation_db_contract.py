from __future__ import annotations

from datetime import date
from typing import Any

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


def _summary(lookback_bars: int = 30) -> dict[str, Any]:
    return {
        "symbol": "TEST",
        "date": date(2026, 2, 12),
        "requested_lookback_bars": 250,
        "lookback_bars": lookback_bars,
        "observation_count": lookback_bars,
        "pivot_count": 0,
        "segment_count": 1,
        "fit_rss": 0.01,
        "search_radius_bars": 5,
        "min_segment_bars": 5,
        "classification_config": {"min_abs_fitted_log_return": 0.02},
        "resolution_diagnostics": {"selected_pivot_count": 0},
        "source_segmentation_method": "source_method",
        "source_segmentation_calculation_version": "adaptive_segmentation_v3",
        "method": "pivot_seeded_continuous_piecewise_log_linear",
        "calculation_version": "pivot_refined_segmentation_v2",
    }


def _segment(lookback_bars: int = 30) -> dict[str, Any]:
    return {
        "symbol": "TEST",
        "date": date(2026, 2, 12),
        "lookback_bars": lookback_bars,
        "segment_index": 0,
        "start_boundary_index": 0,
        "end_boundary_index_exclusive": lookback_bars,
        "start_endpoint_bar_index": 0,
        "end_endpoint_bar_index": lookback_bars - 1,
        "start_endpoint_date": date(2026, 1, 2),
        "end_endpoint_date": date(2026, 2, 12),
        "observation_count": lookback_bars,
        "return_interval_count": lookback_bars - 1,
        "segment_type": "up",
        "log_slope_per_bar": 0.01,
        "linearity_r2": 0.9,
        "fitted_start_log_price": 4.0,
        "fitted_end_log_price": 4.29,
        "fitted_log_return": 0.29,
        "actual_start_log_price": 4.0,
        "actual_end_log_price": 4.28,
        "actual_start_close": 54.6,
        "actual_end_close": 72.2,
        "actual_log_return": 0.28,
        "realized_volatility_daily": 0.02,
        "vol_adjusted_trend": 2.5,
        "efficiency_ratio": 0.8,
        "end_point_type": "window_end",
        "pivot_seed_bar_index": None,
        "pivot_search_start_bar_index": None,
        "pivot_search_end_bar_index": None,
        "pivot_displacement_bars": None,
        "pivot_source_left_type": None,
        "pivot_source_right_type": None,
        "pivot_source_segment_indices": [],
        "pivot_resolution_status": None,
        "method": "pivot_seeded_continuous_piecewise_log_linear",
        "calculation_version": "pivot_refined_segmentation_v2",
    }


def test_pivot_upsert_replaces_detail_rows_and_writes_two_tables(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(queries, "get_conn", lambda: connection)

    queries.upsert_pivot_segmentation_daily([_summary()], [_segment()])

    assert connection.committed
    statements = [statement for statement, _ in connection.executions]
    assert any("DELETE FROM pivot_segment_daily" in value for value in statements)
    summary_execution = next(
        item
        for item in connection.executions
        if "INSERT INTO pivot_segmentation_daily" in item[0]
    )
    segment_execution = next(
        item
        for item in connection.executions
        if "INSERT INTO pivot_segment_daily" in item[0]
    )
    assert len(summary_execution[1] or ()) == 16
    assert (summary_execution[1] or ())[2:5] == (250, 30, 30)
    assert len(segment_execution[1] or ()) == 37


def test_pivot_upsert_keeps_complete_multi_lookback_set(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(queries, "get_conn", lambda: connection)

    queries.upsert_pivot_segmentation_daily(
        [_summary(20), _summary(30)],
        [_segment(20), _segment(30)],
    )

    cleanup_params = [
        params
        for statement, params in connection.executions
        if "NOT (lookback_bars = ANY(%s))" in statement
    ]
    assert cleanup_params == [
        ("TEST", date(2026, 2, 12), [20, 30]),
        ("TEST", date(2026, 2, 12), [20, 30]),
    ]
    summary_inserts = [
        params
        for statement, params in connection.executions
        if "INSERT INTO pivot_segmentation_daily" in statement
    ]
    assert [params[3] for params in summary_inserts if params is not None] == [20, 30]


def test_schema_creates_pivot_summary_and_segment_tables(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(schema, "get_conn", lambda: connection)

    schema.init_schema()

    statements = "\n".join(statement for statement, _ in connection.executions)
    assert connection.committed
    assert "CREATE TABLE IF NOT EXISTS pivot_segmentation_daily" in statements
    assert "CREATE TABLE IF NOT EXISTS pivot_segment_daily" in statements
    assert "PRIMARY KEY (symbol, date, lookback_bars)" in statements
    assert "PRIMARY KEY (symbol, date, lookback_bars, segment_index)" in statements
    assert "pivot_segmentation_requested_lookback_check" in statements
    assert "pivot_segment_daily_date_endpoint_type_idx" in statements


def test_pivot_segment_snapshot_returns_named_columns(monkeypatch) -> None:
    expected = _segment()
    row = tuple(expected[column] for column in queries._PIVOT_SEGMENT_COLS)
    connection = _FetchConnection([row])
    monkeypatch.setattr(queries, "get_conn", lambda: connection)

    result = queries.fetch_pivot_segment_snapshot(date(2026, 2, 12))

    assert result == [expected]
    assert connection.executions[0][1] == (date(2026, 2, 12),)
    assert "ORDER BY symbol, lookback_bars, segment_index" in connection.executions[0][0]


def test_pivot_summary_snapshot_returns_requested_and_actual_lookbacks(
    monkeypatch,
) -> None:
    expected = _summary()
    row = tuple(expected[column] for column in queries._PIVOT_SEGMENTATION_COLS)
    connection = _FetchConnection([row])
    monkeypatch.setattr(queries, "get_conn", lambda: connection)

    result = queries.fetch_pivot_segmentation_snapshot(date(2026, 2, 12))

    assert result == [expected]
    assert result[0]["requested_lookback_bars"] == 250
    assert result[0]["lookback_bars"] == 30
