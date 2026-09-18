from __future__ import annotations

from datetime import date

from typer.testing import CliRunner

from market_analysis import cli
from market_analysis.db import schema
from market_analysis.pipeline import run_adaptive_segmentation as adaptive_pipeline


def test_cli_exposes_only_current_segmentation_workflows() -> None:
    result = CliRunner().invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "init-db",
        "run-adaptive-segmentation",
        "run-pivot-segmentation",
        "compute-adaptive-segmentation",
        "validate-adaptive-segmentation",
    ):
        assert command in result.stdout
    for retired_command in (
        "run-indicators",
        "run-strategies",
        "run-sector-heat",
        "run-trend-pattern-analysis",
        "run-trend-pattern-v4-analysis",
    ):
        assert retired_command not in result.stdout


def test_run_adaptive_segmentation_passes_date_and_lookback(monkeypatch) -> None:
    calls: list[tuple[date | None, int | None, bool, bool]] = []
    monkeypatch.setattr(schema, "init_schema", lambda: None)

    def run_pipeline(
        target_date: date | None = None,
        *,
        lookback_bars: int | None = None,
        force_recompute: bool = False,
        show_progress: bool = False,
    ) -> int:
        calls.append((target_date, lookback_bars, force_recompute, show_progress))
        return 7

    monkeypatch.setattr(
        adaptive_pipeline,
        "run_adaptive_segmentation_pipeline",
        run_pipeline,
    )

    result = CliRunner().invoke(
        cli.app,
        [
            "run-adaptive-segmentation",
            "--date",
            "2026-08-14",
            "--lookback",
            "120",
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert calls == [(date(2026, 8, 14), 120, True, True)]
    assert "Done. 7 symbols written" in result.stdout


def test_run_adaptive_segmentation_without_options_keeps_config_defaults(
    monkeypatch,
) -> None:
    calls: list[tuple[date | None, int | None, bool, bool]] = []
    monkeypatch.setattr(schema, "init_schema", lambda: None)

    def run_pipeline(
        target_date: date | None = None,
        *,
        lookback_bars: int | None = None,
        force_recompute: bool = False,
        show_progress: bool = False,
    ) -> int:
        calls.append((target_date, lookback_bars, force_recompute, show_progress))
        return 0

    monkeypatch.setattr(
        adaptive_pipeline,
        "run_adaptive_segmentation_pipeline",
        run_pipeline,
    )

    result = CliRunner().invoke(cli.app, ["run-adaptive-segmentation"])

    assert result.exit_code == 0
    assert calls == [(None, None, False, True)]
