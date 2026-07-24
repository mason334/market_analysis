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


def _run_indicators() -> int:
    from market_analysis.db.schema import init_schema
    from market_analysis.pipeline.run_indicators import run_pipeline

    init_schema()
    return run_pipeline()


@app.command("init-db")
def init_db() -> None:
    """初始化数据库表（幂等）。"""
    from market_analysis.db.schema import init_schema

    init_schema()
    typer.echo("Database schema initialized.")


@app.command("run-indicators")
def run_indicators_cmd() -> None:
    """从 OPTIONS_ACTIVE 成分股和 universe ETF 计算每日指标快照。"""
    total = _run_indicators()
    typer.echo(f"Done. {total} symbols written to indicator tables.")


@app.command("run-strategies")
def run_strategies_cmd() -> None:
    """兼容旧命令；请优先使用 run-indicators。"""
    total = _run_indicators()
    typer.echo(f"Done. {total} symbols written to indicator tables.")


@app.command("run-sector-heat")
def run_sector_heat() -> None:
    """计算所有板块的今日资金热度快照，结果写入 sector_heat_daily。"""
    from market_analysis.db.schema import init_schema
    from market_analysis.pipeline.run_sector_heat import run_sector_heat_pipeline

    init_schema()
    total = run_sector_heat_pipeline()
    typer.echo(f"Done. {total} sectors written to sector_heat_daily.")


@app.command("run-trend-segmentation-experiment")
def run_trend_segmentation_experiment() -> None:
    """Run the adaptive piecewise-linear trend segmentation experiment."""
    from market_analysis.db.schema import init_schema
    from market_analysis.pipeline.run_adaptive_trend_experiment import (
        run_adaptive_trend_experiment_pipeline,
    )

    init_schema()
    total = run_adaptive_trend_experiment_pipeline()
    typer.echo(f"Done. {total} symbols written to adaptive trend experiment tables.")


@app.command("validate-trend-segmentation")
def validate_trend_segmentation(
    symbols: str = typer.Option(
        "",
        "--symbols",
        help="Comma-separated symbols; defaults to validation.adaptive_trend.symbols.",
    ),
    target_date: str = typer.Option(
        str(date.today()),
        "--date",
        "-d",
        help="Latest validation anchor date (YYYY-MM-DD).",
    ),
    output_dir: Path | None = typer.Option(
        None,
        "--output-dir",
        help=(
            "Report directory; defaults to "
            "artifacts/adaptive_trend_validation/<date>/<run_timestamp>."
        ),
    ),
    full_grid: bool = typer.Option(
        False,
        "--full-grid",
        help="Run the complete Cartesian parameter grid instead of staged screening.",
    ),
) -> None:
    """Generate a read-only validation report with parameter-level progress."""
    from market_analysis.pipeline.validate_adaptive_trend import (
        run_adaptive_trend_validation,
    )

    selected_symbols = [value.strip() for value in symbols.split(",") if value.strip()]
    report = run_adaptive_trend_validation(
        symbols=selected_symbols or None,
        target_date=date.fromisoformat(target_date),
        output_dir=output_dir,
        full_grid=full_grid,
        show_progress=True,
    )
    typer.echo(f"Validation report written to {report}")


@app.command("run-trend-pattern-analysis")
def run_trend_pattern_analysis() -> None:
    """Classify the latest adaptive trend segmentation snapshot."""
    from market_analysis.db.schema import init_schema
    from market_analysis.pipeline.run_trend_patterns import run_trend_pattern_pipeline

    init_schema()
    total = run_trend_pattern_pipeline()
    typer.echo(f"Done. {total} long-window trend patterns written.")


@app.command("show")
def show(
    target_date: str = typer.Option(
        str(date.today()),
        "--date",
        "-d",
        help="查看快照的日期（YYYY-MM-DD）",
    ),
) -> None:
    """查看指定日期的指标快照。"""
    import pandas as pd

    from market_analysis.db.queries import fetch_indicator_snapshot_by_date

    d = date.fromisoformat(target_date)
    df: pd.DataFrame = fetch_indicator_snapshot_by_date(d)

    if df.empty:
        typer.echo(f"No snapshot data found for {d}.")
        return

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 160)
    typer.echo(df.to_string(index=False))


if __name__ == "__main__":
    app()
