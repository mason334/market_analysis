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


@app.command("run")
def run(
    universe: Path = typer.Option(
        Path("config/universe.yaml"),
        "--universe",
        "-u",
        help="股票池 YAML 文件路径",
        exists=True,
        readable=True,
    ),
) -> None:
    """对 universe 中所有股票跑今日信号分析。"""
    from market_analysis.pipeline.run_analysis import run_pipeline

    total = run_pipeline(universe_path=universe)
    typer.echo(f"Done. Total signals written: {total}")


@app.command("show")
def show(
    target_date: str = typer.Option(
        str(date.today()),
        "--date",
        "-d",
        help="查看信号的日期（YYYY-MM-DD）",
    ),
    strategy: str | None = typer.Option(None, "--strategy", "-s", help="过滤策略名称"),
) -> None:
    """查看指定日期的信号。"""
    import pandas as pd

    from market_analysis.db.queries import fetch_signals_by_date

    d = date.fromisoformat(target_date)
    df: pd.DataFrame = fetch_signals_by_date(d)

    if df.empty:
        typer.echo(f"No signals found for {d}.")
        return

    if strategy:
        df = df[df["strategy"] == strategy]

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 120)
    typer.echo(df.to_string(index=False))


if __name__ == "__main__":
    app()
