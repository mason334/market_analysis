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


def _trend_row() -> dict[str, Any]:
    return {
        "symbol": "TEST",
        "date": date(2026, 7, 15),
        "window_label": "20d",
        "far_bars": 20,
        "near_bars": 0,
        "slope": 0.001,
        "r2": 0.8,
        "method": "linear_regression",
        "observation_count": 20,
        "log_slope_per_bar": 0.0009,
        "linearity_r2": 0.81,
        "fitted_log_return": 0.0171,
        "actual_log_return": 0.018,
        "realized_volatility_daily": 0.012,
        "vol_adjusted_trend": 0.33,
        "efficiency_ratio": 0.65,
        "jackknife_slope_stability": 0.94,
        "adjacent_slope_stability": 0.72,
        "calculation_version": "fixed_trend_v2",
    }


def test_upsert_trend_daily_writes_v2_fields(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(queries, "get_conn", lambda: connection)

    queries.upsert_trend_daily([_trend_row()])

    assert connection.committed
    assert len(connection.executions) == 2
    delete_statement, delete_params = connection.executions[0]
    assert "NOT (window_label = ANY(%s))" in delete_statement
    assert delete_params == ("TEST", date(2026, 7, 15), ["20d"])

    statement, params = connection.executions[1]
    assert params is not None
    assert statement.count("%s") == len(params) == 19
    assert params[-11:] == (
        20,
        0.0009,
        0.81,
        0.0171,
        0.018,
        0.012,
        0.33,
        0.65,
        0.94,
        0.72,
        "fixed_trend_v2",
    )


def test_init_schema_applies_idempotent_v2_columns(monkeypatch) -> None:
    connection = _FakeConnection()
    monkeypatch.setattr(schema, "get_conn", lambda: connection)

    schema.init_schema()

    statements = "\n".join(statement for statement, _ in connection.executions)
    assert connection.committed
    assert "ADD COLUMN IF NOT EXISTS log_slope_per_bar FLOAT" in statements
    assert "ADD COLUMN IF NOT EXISTS adjacent_slope_stability FLOAT" in statements
    assert "trend_daily_date_window_idx" in statements
