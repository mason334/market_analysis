from __future__ import annotations

import structlog

from market_analysis.db import get_conn

log = structlog.get_logger(__name__)

_CREATE_SIGNALS = """
CREATE TABLE IF NOT EXISTS signals (
    signal_id    TEXT        PRIMARY KEY,
    symbol       TEXT        NOT NULL,
    date         DATE        NOT NULL,
    strategy     TEXT        NOT NULL,
    signal_type  TEXT        NOT NULL,
    detail_json  JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (symbol, date, strategy)
);
"""

_CREATE_SIGNALS_IDX = """
CREATE INDEX IF NOT EXISTS signals_date_idx ON signals (date DESC);
CREATE INDEX IF NOT EXISTS signals_symbol_idx ON signals (symbol);
CREATE INDEX IF NOT EXISTS signals_strategy_idx ON signals (strategy);
"""

_CREATE_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS indicator_snapshots (
    symbol    TEXT  NOT NULL,
    date      DATE  NOT NULL,
    indicator TEXT  NOT NULL,
    value     FLOAT NOT NULL,
    PRIMARY KEY (symbol, date, indicator)
);
"""

_CREATE_SNAPSHOTS_IDX = """
CREATE INDEX IF NOT EXISTS snapshots_date_idx   ON indicator_snapshots (date DESC);
CREATE INDEX IF NOT EXISTS snapshots_symbol_idx ON indicator_snapshots (symbol);
"""


def init_schema() -> None:
    with get_conn() as conn:
        conn.execute(_CREATE_SIGNALS)
        for stmt in _CREATE_SIGNALS_IDX.strip().splitlines():
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.execute(_CREATE_SNAPSHOTS)
        for stmt in _CREATE_SNAPSHOTS_IDX.strip().splitlines():
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
    log.info("db.schema.initialized")
