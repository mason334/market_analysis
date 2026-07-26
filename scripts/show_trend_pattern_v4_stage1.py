from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from market_analysis.config import settings
from market_analysis.indicators.trend_pattern_v4_structure import (
    DIRECTION_NEUTRAL_STRUCTURES,
    classify_effective_leg_structure,
)
from market_analysis.pipeline.render_trend_pattern_v4_stage1 import (
    render_trend_pattern_v4_stage1,
)


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


_configure_utf8_console()
app = typer.Typer(add_completion=False)
console = Console()


@app.command()
def show(
    legs: str = typer.Option(
        "0.08,-0.10,0.12",
        help="Comma-separated signed effective-leg fitted log returns.",
    ),
    output: Path = typer.Option(
        Path("artifacts/trend_pattern_v4_stage1.html"),
        help="Standalone HTML output path.",
    ),
) -> None:
    """Classify one example and regenerate the verified 40-cell reference page."""
    values = tuple(float(value.strip()) for value in legs.split(",") if value.strip())
    params = dict(settings.indicators.get("trend_pattern_v4", {}))
    tolerance = float(params.get("pivot_retest_tolerance", 0.25))
    result = classify_effective_leg_structure(values, tolerance)

    detail = Table(title="trend_pattern_v4 · stage 1")
    detail.add_column("Field")
    detail.add_column("Value")
    detail.add_row("Input effective legs", " → ".join(f"{value:+.4f}" for value in values))
    detail.add_row("Start direction", result.start_direction)
    detail.add_row("Direction sequence", result.direction_sequence)
    detail.add_row(
        "Normalized legs",
        " → ".join(f"{value:+.4f}" for value in result.normalized_leg_returns),
    )
    detail.add_row(
        "Normalized pivots",
        " → ".join(
            f"P{index}={value:+.4f}"
            for index, value in enumerate(result.normalized_pivots)
        ),
    )
    detail.add_row(
        "Pivot relations",
        " → ".join(value.value for value in result.pivot_relation_sequence) or "none",
    )
    detail.add_row("Structure cell", f"{result.structure_index:02d} · {result.structure_code}")
    console.print(detail)

    if result.relation_measurements:
        evidence = Table(title="逐腿同类 pivot 比较")
        evidence.add_column("Leg")
        evidence.add_column("Pivot comparison")
        evidence.add_column("Amplitude ratio", justify="right")
        evidence.add_column("Relative difference", justify="right")
        evidence.add_column("Result")
        for item in result.relation_measurements:
            evidence.add_row(
                str(item.current_leg_number),
                f"P{item.current_pivot_index} vs P{item.reference_pivot_index}",
                f"{item.amplitude_ratio:.4f}",
                f"{item.relative_difference:.4f}",
                item.relation.value,
            )
        console.print(evidence)

    lower_ratio = 1.0 - tolerance
    upper_ratio = 1.0 / lower_ratio
    console.print(
        "Decision rule:",
        f"ratio < {lower_ratio:.4f} → short_of;",
        f"{lower_ratio:.4f} ≤ ratio ≤ {upper_ratio:.4f} → retest;",
        f"ratio > {upper_ratio:.4f} → break.",
    )

    counts = Counter(item.effective_leg_count for item in DIRECTION_NEUTRAL_STRUCTURES)
    console.print(
        "Complete table:",
        " + ".join(str(counts[count]) for count in range(1, 5)),
        f"= {len(DIRECTION_NEUTRAL_STRUCTURES)} cells",
    )
    page = render_trend_pattern_v4_stage1(output)
    console.print(f"Reference page: {page.resolve()}")


if __name__ == "__main__":
    app()
