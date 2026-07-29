from __future__ import annotations

from datetime import date
from typing import Any

from market_analysis.db import queries, schema


class _FakeConnection:
    def __init__(self, rows: list[tuple[Any, ...]] | None = None) -> None:
        self.rows = rows or []
        self.executions: list[tuple[str, tuple[Any, ...] | None]] = []
        self.committed = False

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(
        self,
        statement: str,
        params: tuple[Any, ...] | None = None,
    ) -> _FakeConnection:
        self.executions.append((statement, params))
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def commit(self) -> None:
        self.committed = True


def _v4_row() -> dict[str, Any]:
    return {
        "symbol": "TEST",
        "date": date(2026, 7, 15),
        "lookback_bars": 40,
        "observation_count": 40,
        "source_segment_count": 2,
        "effective_leg_count": 2,
        "start_direction": "up",
        "structure_index": 2,
        "structure_code": "L2-S",
        "net_log_return": 0.08,
        "path_efficiency": 0.6,
        "historical_volatility": 25.0,
        "terminal_price_rank": 0.9,
        "terminal_price_position": 0.95,
        "terminal_leg_start_position": 0.4,
        "terminal_breakout_distance_vol": 0.5,
        "squared_movement_time_position": 0.7,
        "squared_movement_concentration": 0.3,
        "min_abs_fitted_log_return": 0.02,
        "min_linearity_r2": 0.35,
        "min_abs_vol_adjusted_trend": 0.75,
        "pivot_retest_tolerance": 0.25,
        "source_segmentation_method": "continuous_piecewise_log_linear_exhaustive_bic",
        "source_segmentation_calculation_version": "adaptive_trend_v2",
        "method": "effective_leg_structure_and_close_path_metrics",
        "calculation_version": "trend_pattern_v4_1",
    }


def test_upsert_trend_pattern_v4_writes_contract_fields(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(queries, "get_conn", lambda: connection)

    queries.upsert_trend_pattern_v4_daily([_v4_row()])

    assert connection.committed
    assert len(connection.executions) == 2
    delete_statement, delete_params = connection.executions[0]
    assert "DELETE FROM trend_pattern_v4_daily" in delete_statement
    assert delete_params == ("TEST", date(2026, 7, 15), [40])
    insert_statement, insert_params = connection.executions[1]
    assert "INSERT INTO trend_pattern_v4_daily" in insert_statement
    assert len(insert_params or ()) == 26


def test_schema_creates_trend_pattern_v4_table_and_indexes(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(schema, "get_conn", lambda: connection)

    schema.init_schema()

    statements = "\n".join(statement for statement, _ in connection.executions)
    assert connection.committed
    assert "CREATE TABLE IF NOT EXISTS trend_pattern_v4_daily" in statements
    assert "trend_pattern_v4_daily_date_structure_idx" in statements
    assert "trend_pattern_v4_daily_symbol_date_idx" in statements


def test_fetch_close_window_uses_historical_cutoff_and_exact_limit(monkeypatch) -> None:
    connection = _FakeConnection(
        rows=[
            (date(2026, 7, 14), 100.0),
            (date(2026, 7, 15), 101.0),
        ]
    )
    monkeypatch.setattr(queries, "get_source_conn", lambda: connection)

    result = queries.fetch_split_adjusted_close_window(
        "TEST",
        date(2026, 7, 15),
        2,
    )

    assert connection.executions[0][1] == ("TEST", date(2026, 7, 15), 2)
    assert result["close"].tolist() == [100.0, 101.0]
    assert result.index[-1].date() == date(2026, 7, 15)
