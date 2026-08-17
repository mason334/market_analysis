from __future__ import annotations

import atexit
import json
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


@app.command("run-adaptive-segmentation")
def run_adaptive_segmentation(
    target_date: str | None = typer.Option(
        None,
        "--date",
        "-d",
        help="Optional snapshot cutoff date (YYYY-MM-DD); otherwise use all market data.",
    ),
    lookback: int | None = typer.Option(
        None,
        "--lookback",
        min=40,
        max=500,
        help="Requested lookback bars; defaults to the configured lookbacks.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Recompute snapshots even when persisted parameters and segments match.",
    ),
) -> None:
    """Persist the canonical adaptive segmentation snapshot."""
    from market_analysis.db.schema import init_schema
    from market_analysis.pipeline.run_adaptive_segmentation import (
        run_adaptive_segmentation_pipeline,
    )

    init_schema()
    parsed_date = None if target_date is None else date.fromisoformat(target_date)
    total = run_adaptive_segmentation_pipeline(
        parsed_date,
        lookback_bars=lookback,
        force_recompute=force,
        show_progress=True,
    )
    typer.echo(f"Done. {total} symbols written to adaptive segmentation tables.")


@app.command("run-pivot-segmentation")
def run_pivot_segmentation_cmd(
    target_date: str | None = typer.Option(
        None,
        "--date",
        "-d",
        help="Adaptive segmentation snapshot date; defaults to the latest snapshot.",
    ),
) -> None:
    """Refine persisted adaptive segments with close-price pivots."""
    from market_analysis.db.schema import init_schema
    from market_analysis.pipeline.run_pivot_segmentation import (
        run_pivot_segmentation_pipeline,
    )

    init_schema()
    parsed_date = None if target_date is None else date.fromisoformat(target_date)
    total = run_pivot_segmentation_pipeline(parsed_date, show_progress=True)
    typer.echo(f"Done. {total} symbol/lookback snapshots written to pivot segmentation tables.")


@app.command("compute-adaptive-segmentation")
def compute_adaptive_segmentation_cmd(
    symbol: str = typer.Option(..., "--symbol", help="Ticker symbol."),
    lookback: int = typer.Option(250, "--lookback", min=40, max=500),
    min_segment_bars: int = typer.Option(5, "--min-segment-bars", min=2, max=30),
    max_segments: int = typer.Option(10, "--max-segments", min=1, max=20),
    bic_penalty_multiplier: float = typer.Option(
        3.0,
        "--bic-penalty-multiplier",
        min=0.1,
        max=10.0,
    ),
    target_date: str = typer.Option("", "--date", help="Optional end date YYYY-MM-DD."),
) -> None:
    """Compute one read-only adaptive segmentation result and emit JSON."""
    from market_analysis.pipeline.compute_adaptive_segmentation import (
        compute_adaptive_segmentation_for_symbol,
    )

    result = compute_adaptive_segmentation_for_symbol(
        symbol,
        lookback_bars=lookback,
        min_segment_bars=min_segment_bars,
        max_segments=max_segments,
        bic_penalty_multiplier=bic_penalty_multiplier,
        target_date=date.fromisoformat(target_date) if target_date else None,
    )
    typer.echo(json.dumps(result, default=str, allow_nan=False, separators=(",", ":")))


@app.command("validate-adaptive-segmentation")
def validate_adaptive_segmentation(
    symbols: str = typer.Option(
        "",
        "--symbols",
        help="Comma-separated symbols; defaults to validation.adaptive_segmentation.symbols.",
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
            "artifacts/adaptive_segmentation_validation/<date>/<run_timestamp>."
        ),
    ),
    full_grid: bool = typer.Option(
        False,
        "--full-grid",
        help="Run the complete Cartesian parameter grid instead of staged screening.",
    ),
) -> None:
    """Generate a read-only validation report with parameter-level progress."""
    from market_analysis.pipeline.validate_adaptive_segmentation import (
        run_adaptive_segmentation_validation,
    )

    selected_symbols = [value.strip() for value in symbols.split(",") if value.strip()]
    report = run_adaptive_segmentation_validation(
        symbols=selected_symbols or None,
        target_date=date.fromisoformat(target_date),
        output_dir=output_dir,
        full_grid=full_grid,
        show_progress=True,
    )
    typer.echo(f"Validation report written to {report}")


@app.command("run-trend-segmentation-experiment", hidden=True)
def run_trend_segmentation_experiment_legacy() -> None:
    """Compatibility alias for run-adaptive-segmentation."""
    run_adaptive_segmentation(target_date=None, lookback=None, force=False)


@app.command("compute-adaptive-trend", hidden=True)
def compute_adaptive_trend_legacy(
    symbol: str = typer.Option(..., "--symbol"),
    lookback: int = typer.Option(250, "--lookback", min=40, max=500),
    min_segment_bars: int = typer.Option(5, "--min-segment-bars", min=2, max=30),
    max_segments: int = typer.Option(10, "--max-segments", min=1, max=20),
    bic_penalty_multiplier: float = typer.Option(
        3.0,
        "--bic-penalty-multiplier",
        min=0.1,
        max=10.0,
    ),
    target_date: str = typer.Option("", "--date"),
) -> None:
    """Compatibility alias for compute-adaptive-segmentation."""
    compute_adaptive_segmentation_cmd(
        symbol=symbol,
        lookback=lookback,
        min_segment_bars=min_segment_bars,
        max_segments=max_segments,
        bic_penalty_multiplier=bic_penalty_multiplier,
        target_date=target_date,
    )


@app.command("validate-trend-segmentation", hidden=True)
def validate_trend_segmentation_legacy(
    symbols: str = typer.Option("", "--symbols"),
    target_date: str = typer.Option(str(date.today()), "--date", "-d"),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
    full_grid: bool = typer.Option(False, "--full-grid"),
) -> None:
    """Compatibility alias for validate-adaptive-segmentation."""
    validate_adaptive_segmentation(
        symbols=symbols,
        target_date=target_date,
        output_dir=output_dir,
        full_grid=full_grid,
    )


@app.command("run-trend-pattern-analysis")
def run_trend_pattern_analysis() -> None:
    """Classify the latest adaptive trend segmentation snapshot."""
    from market_analysis.db.schema import init_schema
    from market_analysis.pipeline.run_trend_patterns import run_trend_pattern_pipeline

    init_schema()
    total = run_trend_pattern_pipeline()
    typer.echo(f"Done. {total} long-window trend patterns written.")


@app.command("run-trend-pattern-v4-analysis")
def run_trend_pattern_v4_analysis(
    target_date: str | None = typer.Option(
        None,
        "--date",
        "-d",
        help="Segmentation snapshot date (YYYY-MM-DD); defaults to the latest snapshot.",
    ),
) -> None:
    """Build v4 structure and close-path metrics from persisted segmentation."""
    from market_analysis.db.schema import init_schema
    from market_analysis.pipeline.run_trend_pattern_v4 import (
        run_trend_pattern_v4_pipeline,
    )

    init_schema()
    parsed_date = None if target_date is None else date.fromisoformat(target_date)
    total = run_trend_pattern_v4_pipeline(parsed_date)
    typer.echo(f"Done. {total} trend_pattern_v4 rows written.")


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
