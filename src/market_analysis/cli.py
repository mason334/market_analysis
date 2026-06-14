from __future__ import annotations

import atexit
from datetime import date
from pathlib import Path

import structlog
import typer

app = typer.Typer(name="market-analysis", add_completion=False)
log = structlog.get_logger(__name__)


def _shutdown() -> None:
    from market_analysis.db import close_pools

    close_pools()


atexit.register(_shutdown)


@app.command("init-db")
def init_db() -> None:
    """初始化数据库表（幂等）。"""
    from market_analysis.db.schema import init_schema

    init_schema()
    typer.echo("Database schema initialized.")


@app.command("run-strategies")
def run_strategies_cmd() -> None:
    """从 universe_constituents(OPTIONS_ACTIVE) 和 universe 表取 ticker，跑所有已注册策略，结果写入 indicators_daily。"""
    from market_analysis.pipeline.run_strategies import run_pipeline

    total = run_pipeline()
    typer.echo(f"Done. {total} symbols written to indicators_daily.")


@app.command("run-sector-heat")
def run_sector_heat() -> None:
    """计算所有板块的今日资金热度快照，结果写入 sector_heat_daily。"""
    from market_analysis.db.schema import init_schema
    from market_analysis.pipeline.run_sector_heat import run_sector_heat_pipeline

    init_schema()
    total = run_sector_heat_pipeline()
    typer.echo(f"Done. {total} sectors written to sector_heat_daily.")


@app.command("show")
def show(
    target_date: str = typer.Option(
        str(date.today()),
        "--date",
        "-d",
        help="查看快照的日期（YYYY-MM-DD）",
    ),
) -> None:
    """查看指定日期的 indicators_daily 快照。"""
    import pandas as pd

    from market_analysis.db.queries import fetch_indicators_daily_by_date

    d = date.fromisoformat(target_date)
    df: pd.DataFrame = fetch_indicators_daily_by_date(d)

    if df.empty:
        typer.echo(f"No snapshot data found for {d}.")
        return

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 160)
    typer.echo(df.to_string(index=False))


if __name__ == "__main__":
    app()
