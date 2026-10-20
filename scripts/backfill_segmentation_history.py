#!/usr/bin/env python3
"""补齐指定日期范围内缺失的 125/250 bars 自适应与 Pivot 分段快照。"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import BinaryIO

import typer

from market_analysis.db import close_pools
from market_analysis.db.schema import init_schema
from market_analysis.pipeline.backfill_segmentation import run_segmentation_backfill

_PROJECT_DIR = Path(__file__).resolve().parent.parent
_LOCK_FILE = _PROJECT_DIR / ".market_analysis_segmentation.lock"


def _acquire_lock() -> BinaryIO:
    lock_handle = _LOCK_FILE.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            lock_handle.seek(0, os.SEEK_END)
            if lock_handle.tell() == 0:
                lock_handle.write(b"\0")
                lock_handle.flush()
            lock_handle.seek(0)
            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        lock_handle.close()
        raise RuntimeError(f"Another segmentation job holds {_LOCK_FILE}.") from None
    return lock_handle


def main(
    start_date: str = typer.Option("2026-09-25", "--start-date"),
    end_date: str | None = typer.Option(
        None,
        "--end-date",
        help="Inclusive end date; defaults to today and is limited by available SPY data.",
    ),
    calendar_symbol: str = typer.Option("SPY", "--calendar-symbol"),
) -> None:
    """Run an idempotent historical backfill for configured 125/250-bar windows."""
    lock_handle: BinaryIO | None = None
    try:
        parsed_start = date.fromisoformat(start_date)
        parsed_end = date.today() if end_date is None else date.fromisoformat(end_date)
        lock_handle = _acquire_lock()
        init_schema()
        result = run_segmentation_backfill(
            parsed_start,
            parsed_end,
            calendar_symbol=calendar_symbol.upper(),
            show_progress=True,
        )
        typer.echo(
            "Backfill complete: "
            f"trading_dates={result.trading_dates}, "
            f"adaptive_dates_run={result.adaptive_dates_run}, "
            f"pivot_windows_run={result.pivot_windows_run}, "
            f"pivot_windows_skipped={result.pivot_windows_skipped}."
        )
    finally:
        close_pools()
        if lock_handle is not None:
            lock_handle.close()


if __name__ == "__main__":
    typer.run(main)
