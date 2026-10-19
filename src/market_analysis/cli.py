from __future__ import annotations

import atexit
import json
import sys
from contextlib import redirect_stdout
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
    """Create or update the adaptive and pivot segmentation tables."""
    from market_analysis.db.schema import init_schema

    init_schema()
    typer.echo("Segmentation database schema initialized.")


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
    """Persist adaptive segmentation snapshots."""
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
    lookback: int | None = typer.Option(
        None,
        "--lookback",
        min=40,
        max=500,
        help="Optional requested adaptive lookback; otherwise refine all windows on the date.",
    ),
) -> None:
    """Refine persisted adaptive segments with close-price pivots."""
    from market_analysis.db.schema import init_schema
    from market_analysis.pipeline.run_pivot_segmentation import (
        run_pivot_segmentation_pipeline,
    )

    init_schema()
    parsed_date = None if target_date is None else date.fromisoformat(target_date)
    total = run_pivot_segmentation_pipeline(
        parsed_date,
        requested_lookback_bars=lookback,
        show_progress=True,
    )
    typer.echo(f"Done. {total} symbol/lookback snapshots written to pivot tables.")


@app.command("compute-adaptive-segmentation")
def compute_adaptive_segmentation_cmd(
    symbol: str = typer.Option(..., "--symbol", help="Ticker symbol."),
    lookback: int = typer.Option(250, "--lookback", min=40, max=500),
    min_segment_bars: int = typer.Option(5, "--min-segment-bars", min=2, max=30),
    max_segments: int = typer.Option(10, "--max-segments", min=1, max=20),
    bic_penalty_multiplier: float = typer.Option(
        2.0,
        "--bic-penalty-multiplier",
        min=0.1,
        max=10.0,
    ),
    target_date: str = typer.Option("", "--date", help="Optional end date YYYY-MM-DD."),
) -> None:
    """Compute one read-only adaptive segmentation result and emit JSON."""
    from market_analysis.pipeline.preview_adaptive_segmentation import (
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


@app.command("compute-manual-pivot-analysis")
def compute_manual_pivot_analysis_cmd(
    symbol: str = typer.Option(..., "--symbol", help="Ticker symbol."),
    start_date: str = typer.Option(..., "--start-date", help="Inclusive start date."),
    end_date: str = typer.Option(..., "--end-date", help="Inclusive end date."),
    adaptive_min_segment_bars: int | None = typer.Option(
        None, "--adaptive-min-segment-bars", min=2, max=30
    ),
    adaptive_max_segments: int | None = typer.Option(
        None, "--adaptive-max-segments", min=1, max=20
    ),
    adaptive_bic_penalty_multiplier: float | None = typer.Option(
        None, "--adaptive-bic-penalty-multiplier", min=0.1, max=10.0
    ),
    pivot_search_radius_bars: int | None = typer.Option(
        None, "--pivot-search-radius-bars", min=0, max=30
    ),
    pivot_min_segment_bars: int | None = typer.Option(
        None, "--pivot-min-segment-bars", min=2, max=30
    ),
    pivot_min_abs_fitted_log_return: float | None = typer.Option(
        None, "--pivot-min-abs-fitted-log-return", min=0.0, max=1.0
    ),
    pivot_min_linearity_r2: float | None = typer.Option(
        None, "--pivot-min-linearity-r2", min=0.0, max=1.0
    ),
    pivot_min_abs_vol_adjusted_trend: float | None = typer.Option(
        None, "--pivot-min-abs-vol-adjusted-trend", min=0.0, max=10.0
    ),
) -> None:
    """Compute one read-only adaptive plus Pivot analysis and emit JSON."""
    from market_analysis.pipeline.preview_manual_pivot_analysis import (
        compute_manual_pivot_analysis_for_symbol,
    )

    try:
        with redirect_stdout(sys.stderr):
            result = compute_manual_pivot_analysis_for_symbol(
                symbol,
                start_date=date.fromisoformat(start_date),
                end_date=date.fromisoformat(end_date),
                adaptive_min_segment_bars=adaptive_min_segment_bars,
                adaptive_max_segments=adaptive_max_segments,
                adaptive_bic_penalty_multiplier=adaptive_bic_penalty_multiplier,
                pivot_search_radius_bars=pivot_search_radius_bars,
                pivot_min_segment_bars=pivot_min_segment_bars,
                pivot_min_abs_fitted_log_return=pivot_min_abs_fitted_log_return,
                pivot_min_linearity_r2=pivot_min_linearity_r2,
                pivot_min_abs_vol_adjusted_trend=pivot_min_abs_vol_adjusted_trend,
            )
    finally:
        with redirect_stdout(sys.stderr):
            _shutdown()
    typer.echo(json.dumps(result, default=str, allow_nan=False, separators=(",", ":")))


@app.command("validate-adaptive-segmentation")
def validate_adaptive_segmentation(
    symbols: str = typer.Option(
        "",
        "--symbols",
        help="Comma-separated symbols; defaults to validation configuration.",
    ),
    target_date: str = typer.Option(
        str(date.today()),
        "--date",
        "-d",
        help="Latest validation anchor date (YYYY-MM-DD).",
    ),
    output_dir: Path | None = typer.Option(None, "--output-dir"),
    full_grid: bool = typer.Option(
        False,
        "--full-grid",
        help="Run the complete Cartesian parameter grid instead of staged screening.",
    ),
) -> None:
    """Generate a read-only adaptive-segmentation validation report."""
    from market_analysis.pipeline.evaluate_adaptive_segmentation import (
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


if __name__ == "__main__":
    app()
