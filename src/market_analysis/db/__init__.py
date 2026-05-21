from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

import psycopg
import structlog
from psycopg_pool import ConnectionPool

from market_analysis.config import settings

log = structlog.get_logger(__name__)

# Pool for market_analysis DB (signals read/write)
_pool: ConnectionPool | None = None
# Pool for market_data DB (OHLCV read-only)
_source_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.dsn,
            min_size=1,
            max_size=5,
            open=True,
        )
        log.info("db.pool.created", dbname=settings.db_name)
    return _pool


def get_source_pool() -> ConnectionPool:
    global _source_pool
    if _source_pool is None:
        _source_pool = ConnectionPool(
            conninfo=settings.source_dsn,
            min_size=1,
            max_size=5,
            open=True,
        )
        log.info("db.source_pool.created", dbname=settings.source_db_name)
    return _source_pool


@contextmanager
def get_conn() -> Generator[psycopg.Connection, None, None]:
    with get_pool().connection() as conn:
        yield conn


@contextmanager
def get_source_conn() -> Generator[psycopg.Connection, None, None]:
    with get_source_pool().connection() as conn:
        yield conn


def close_pools() -> None:
    global _pool, _source_pool
    for pool, name in [(_pool, "pool"), (_source_pool, "source_pool")]:
        if pool is not None:
            pool.close()
            log.info(f"db.{name}.closed")
    _pool = None
    _source_pool = None
