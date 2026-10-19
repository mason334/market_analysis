from __future__ import annotations

from datetime import date

from typer.testing import CliRunner

from market_analysis import cli
from market_analysis.db import schema
from market_analysis.pipeline import run_adaptive_segmentation as adaptive_pipeline
from market_analysis.pipeline import run_pivot_segmentation as pivot_pipeline


def test_cli_exposes_only_current_segmentation_workflows() -> None:
    result = CliRunner().invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    for command in (
        "init-db",
        "run-adaptive-segmentation",
        "run-pivot-segmentation",
        "compute-adaptive-segmentation",
        "compute-manual-pivot-analysis",
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


def test_run_pivot_segmentation_passes_date_and_requested_lookback(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(schema, "init_schema", lambda: None)

    def run_pipeline(target_date=None, *, requested_lookback_bars=None, show_progress=False):
        calls.append((target_date, requested_lookback_bars, show_progress))
        return 3

    monkeypatch.setattr(pivot_pipeline, "run_pivot_segmentation_pipeline", run_pipeline)
    result = CliRunner().invoke(
        cli.app,
        ["run-pivot-segmentation", "--date", "2026-08-14", "--lookback", "125"],
    )

    assert result.exit_code == 0
    assert calls == [(date(2026, 8, 14), 125, True)]
    assert "Done. 3 symbol/lookback snapshots written" in result.stdout


def test_run_pivot_segmentation_without_lookback_keeps_all_windows(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(schema, "init_schema", lambda: None)

    def run_pipeline(target_date=None, *, requested_lookback_bars=None, show_progress=False):
        calls.append((target_date, requested_lookback_bars, show_progress))
        return 0

    monkeypatch.setattr(pivot_pipeline, "run_pivot_segmentation_pipeline", run_pipeline)
    result = CliRunner().invoke(cli.app, ["run-pivot-segmentation"])

    assert result.exit_code == 0
    assert calls == [(None, None, True)]


def test_compute_manual_pivot_analysis_passes_all_parameters(monkeypatch) -> None:
    from market_analysis.pipeline import preview_manual_pivot_analysis

    calls: list[dict[str, object]] = []

    def compute(symbol: str, **kwargs: object) -> dict[str, object]:
        calls.append({"symbol": symbol, **kwargs})
        return {"ok": True}

    monkeypatch.setattr(
        preview_manual_pivot_analysis,
        "compute_manual_pivot_analysis_for_symbol",
        compute,
    )
    result = CliRunner().invoke(
        cli.app,
        [
            "compute-manual-pivot-analysis",
            "--symbol",
            "AAPL",
            "--start-date",
            "2026-01-02",
            "--end-date",
            "2026-08-14",
            "--adaptive-min-segment-bars",
            "6",
            "--adaptive-max-segments",
            "8",
            "--adaptive-bic-penalty-multiplier",
            "3.0",
            "--pivot-search-radius-bars",
            "7",
            "--pivot-min-segment-bars",
            "6",
            "--pivot-min-abs-fitted-log-return",
            "0.03",
            "--pivot-min-linearity-r2",
            "0.45",
            "--pivot-min-abs-vol-adjusted-trend",
            "1.0",
        ],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "symbol": "AAPL",
            "start_date": date(2026, 1, 2),
            "end_date": date(2026, 8, 14),
            "adaptive_min_segment_bars": 6,
            "adaptive_max_segments": 8,
            "adaptive_bic_penalty_multiplier": 3.0,
            "pivot_search_radius_bars": 7,
            "pivot_min_segment_bars": 6,
            "pivot_min_abs_fitted_log_return": 0.03,
            "pivot_min_linearity_r2": 0.45,
            "pivot_min_abs_vol_adjusted_trend": 1.0,
        }
    ]
    assert '"ok":true' in result.stdout
