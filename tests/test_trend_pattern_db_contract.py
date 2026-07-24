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


def _pattern_row() -> dict[str, Any]:
    return {
        "symbol": "TEST",
        "date": date(2026, 7, 15),
        "lookback_bars": 40,
        "regime": "transitioning",
        "directional_bias": "up",
        "path_structure": "reversal",
        "terminal_state": "up",
        "pattern": "bottom_reversal",
        "pattern_confidence": 0.8,
        "classification_reason": "down_to_up_reversal",
        "direction_sequence": "down>up",
        "segment_count": 2,
        "net_fitted_log_return": 0.03,
        "gross_fitted_log_return": 0.17,
        "net_to_gross_ratio": 0.176,
        "latest_segment_direction": "up",
        "latest_segment_log_slope": 0.01,
        "latest_segment_fitted_log_return": 0.10,
        "method": "rule_based_adaptive_segments",
        "calculation_version": "trend_pattern_v3",
    }


def test_upsert_trend_pattern_writes_contract_fields(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(queries, "get_conn", lambda: connection)

    queries.upsert_trend_pattern_daily([_pattern_row()])

    assert connection.committed
    assert len(connection.executions) == 2
    delete_statement, delete_params = connection.executions[0]
    assert "NOT (lookback_bars = ANY(%s))" in delete_statement
    assert delete_params == ("TEST", date(2026, 7, 15), [40])
    insert_statement, insert_params = connection.executions[1]
    assert "INSERT INTO trend_pattern_daily" in insert_statement
    assert len(insert_params or ()) == 20


def test_schema_creates_trend_pattern_table_and_indexes(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(schema, "get_conn", lambda: connection)

    schema.init_schema()

    statements = "\n".join(statement for statement, _ in connection.executions)
    assert connection.committed
    assert "CREATE TABLE IF NOT EXISTS trend_pattern_daily" in statements
    assert "ADD COLUMN IF NOT EXISTS regime" in statements
    assert "ADD COLUMN IF NOT EXISTS directional_bias" in statements
    assert "ADD COLUMN IF NOT EXISTS path_structure" in statements
    assert "ADD COLUMN IF NOT EXISTS terminal_state" in statements
    assert "trend_pattern_daily_date_lookback_pattern_idx" in statements
    assert "trend_pattern_daily_symbol_date_idx" in statements
