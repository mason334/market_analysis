from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from html import escape
from itertools import product
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import structlog
from plotly.subplots import make_subplots
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn

from market_analysis.config import settings
from market_analysis.db.queries import fetch_ohlcv
from market_analysis.indicators.adaptive_trend import (
    compute_adaptive_segmentation,
    reconstruct_adaptive_fit,
)

log = structlog.get_logger(__name__)
_PROJECT_ROOT = Path(__file__).parents[3]


@dataclass(frozen=True, order=True)
class ParameterSet:
    bic_penalty_multiplier: float
    min_segment_bars: int
    max_segments: int

    @property
    def key(self) -> str:
        return (
            f"bic={self.bic_penalty_multiplier:g}|"
            f"min={self.min_segment_bars}|max={self.max_segments}"
        )


def _validation_config() -> dict[str, Any]:
    return dict(settings.validation.get("adaptive_trend", {}))


def _default_output_dir(target_date: date, run_started_at: datetime | None = None) -> Path:
    started_at = run_started_at or datetime.now().astimezone()
    run_timestamp = started_at.strftime("run_%Y%m%d_%H%M%S_%f")
    return (
        _PROJECT_ROOT
        / "artifacts"
        / "adaptive_trend_validation"
        / target_date.isoformat()
        / run_timestamp
    )


def _parameter_grid(config: dict[str, Any]) -> list[ParameterSet]:
    return [
        ParameterSet(float(penalty), int(minimum), int(maximum))
        for penalty, minimum, maximum in product(
            config.get("bic_penalty_grid", [1.5, 2.0, 3.0, 4.0, 5.0]),
            config.get("min_segment_bars_grid", [5, 7, 10]),
            config.get("max_segments_grid", [3, 4]),
        )
    ]


def _production_parameters() -> ParameterSet:
    params = settings.indicators.get("adaptive_trend", {})
    return ParameterSet(
        float(params.get("bic_penalty_multiplier", 3.0)),
        int(params.get("min_segment_bars", 5)),
        int(params.get("max_segments", 5)),
    )


def _anchor_windows(
    frame: pd.DataFrame,
    target_date: date,
    offsets: list[int],
    required_bars: int,
) -> list[tuple[int, pd.DataFrame]]:
    eligible = frame.loc[frame.index.date <= target_date]
    windows: list[tuple[int, pd.DataFrame]] = []
    for offset in sorted(set(offsets)):
        end = len(eligible) - offset
        if end >= required_bars:
            windows.append((offset, eligible.iloc[:end]))
    return windows


def _configured_anchor_pairs(
    config: dict[str, Any],
) -> tuple[list[int], list[tuple[int, int]], list[int]]:
    base_offsets = sorted(
        {int(value) for value in config.get("anchor_offsets", [0, 10, 20, 30, 40])}
    )
    if not base_offsets or any(value < 0 for value in base_offsets):
        raise ValueError("anchor_offsets must contain non-negative integers.")
    comparison_step = int(config.get("anchor_comparison_step_bars", 2))
    if comparison_step <= 0:
        raise ValueError("anchor_comparison_step_bars must be positive.")
    anchor_pairs = [
        (base_offset, base_offset + comparison_step)
        for base_offset in base_offsets
    ]
    calculation_offsets = sorted(
        {offset for pair in anchor_pairs for offset in pair}
    )
    return base_offsets, anchor_pairs, calculation_offsets


def _decorate_rows(
    parameter_set: ParameterSet,
    stage: str,
    anchor_offset: int,
    summary: dict[str, Any],
    segments: list[dict[str, Any]],
    full_frame: pd.DataFrame,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    decorated_summary = {
        **summary,
        "parameter_key": parameter_set.key,
        "stage": stage,
        "anchor_offset": anchor_offset,
        "is_production": False,
        "window_start_date": segments[0]["start_date"],
        "window_end_date": segments[-1]["end_date"],
    }
    date_positions = {timestamp.date(): index for index, timestamp in enumerate(full_frame.index)}
    decorated_summary["global_window_start_index"] = date_positions.get(
        decorated_summary["window_start_date"]
    )
    decorated_summary["global_window_end_index"] = date_positions.get(
        decorated_summary["window_end_date"]
    )
    decorated_segments = [
        {
            **row,
            "parameter_key": parameter_set.key,
            "stage": stage,
            "anchor_offset": anchor_offset,
            "global_start_index": date_positions.get(row["start_date"]),
        }
        for row in segments
    ]
    return decorated_summary, decorated_segments


def _run_parameter_sets(
    frames: dict[str, pd.DataFrame],
    parameter_sets: list[ParameterSet],
    lookbacks: list[int],
    target_date: date,
    offsets: list[int],
    stage: str,
    cache: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    refresh: bool = False,
    progress: Progress | None = None,
) -> None:
    required_bars = max(lookbacks)
    pending = [
        parameter_set
        for parameter_set in parameter_sets
        if refresh or parameter_set.key not in cache
    ]
    task_id = (
        progress.add_task(f"{stage}: preparing", total=len(pending))
        if progress is not None and pending
        else None
    )
    for parameter_set in pending:
        if progress is not None and task_id is not None:
            progress.update(task_id, description=f"{stage}: {parameter_set.key}")
        summaries: list[dict[str, Any]] = []
        segments: list[dict[str, Any]] = []
        for symbol, full_frame in frames.items():
            for anchor_offset, anchor_frame in _anchor_windows(
                full_frame, target_date, offsets, required_bars
            ):
                for lookback in lookbacks:
                    summary, rows = compute_adaptive_segmentation(
                        symbol,
                        anchor_frame,
                        lookback,
                        min_segment_bars=parameter_set.min_segment_bars,
                        max_segments=parameter_set.max_segments,
                        bic_penalty_multiplier=parameter_set.bic_penalty_multiplier,
                    )
                    if summary is None:
                        continue
                    decorated_summary, decorated_segments = _decorate_rows(
                        parameter_set,
                        stage,
                        anchor_offset,
                        summary,
                        rows,
                        full_frame,
                    )
                    summaries.append(decorated_summary)
                    segments.extend(decorated_segments)
        cache[parameter_set.key] = summaries, segments
        log.info(
            "adaptive_trend_validation.parameter_set.done",
            parameter_key=parameter_set.key,
            observations=len(summaries),
        )
        if progress is not None and task_id is not None:
            progress.advance(task_id)


def _configured_tolerances(config: dict[str, Any]) -> tuple[int, list[int]]:
    raw_primary = config.get("breakpoint_tolerance_bars", 2)
    if isinstance(raw_primary, list):
        if not raw_primary:
            raise ValueError("breakpoint_tolerance_bars cannot be an empty list.")
        primary = int(raw_primary[0])
        sensitivity = [int(value) for value in raw_primary]
    else:
        primary = int(raw_primary)
        sensitivity = [
            int(value)
            for value in config.get(
                "breakpoint_tolerance_sensitivity_bars", [primary]
            )
        ]
    if primary < 0 or any(value < 0 for value in sensitivity):
        raise ValueError("Breakpoint tolerances must be non-negative.")
    return primary, sorted(set([primary, *sensitivity]))


def _breakpoint_match_count(
    first: list[int], second: list[int], tolerance: int
) -> int:
    ordered_first = sorted(first)
    ordered_second = sorted(second)
    first_index = 0
    second_index = 0
    matches = 0
    while first_index < len(ordered_first) and second_index < len(ordered_second):
        first_value = ordered_first[first_index]
        second_value = ordered_second[second_index]
        if abs(first_value - second_value) <= tolerance:
            matches += 1
            first_index += 1
            second_index += 1
        elif first_value < second_value:
            first_index += 1
        else:
            second_index += 1
    return matches


def _local_breakpoint_stability_metrics(
    parameter_set: ParameterSet,
    summary_frame: pd.DataFrame,
    segment_frame: pd.DataFrame,
    tolerances: list[int],
    anchor_pairs: list[tuple[int, int]],
) -> dict[str, Any]:
    matched_by_tolerance = {tolerance: 0 for tolerance in tolerances}
    breakpoint_denominator = 0
    comparable_anchor_pairs = 0
    breakpoint_evidence_pairs = 0
    both_single_pairs = 0
    both_no_comparable_pairs = 0

    grouped = summary_frame.groupby(["symbol", "lookback_bars"], sort=False)
    for _, group in grouped:
        snapshots: dict[int, tuple[Any, list[int]]] = {}
        for row in group.itertuples():
            matching = segment_frame.loc[
                (segment_frame["symbol"] == row.symbol)
                & (segment_frame["lookback_bars"] == row.lookback_bars)
                & (segment_frame["date"] == row.date)
                & (segment_frame["anchor_offset"] == row.anchor_offset)
            ].sort_values("segment_index")
            breakpoints = [
                int(value)
                for value in matching.loc[
                    matching["segment_index"] > 0, "global_start_index"
                ].dropna()
            ]
            snapshots[int(row.anchor_offset)] = (row, breakpoints)

        for first_offset, second_offset in anchor_pairs:
            if first_offset not in snapshots or second_offset not in snapshots:
                continue
            first_row, first_breakpoints = snapshots[first_offset]
            second_row, second_breakpoints = snapshots[second_offset]
            overlap_start = max(
                int(first_row.global_window_start_index),
                int(second_row.global_window_start_index),
            )
            overlap_end = min(
                int(first_row.global_window_end_index),
                int(second_row.global_window_end_index),
            )
            core_start = overlap_start + parameter_set.min_segment_bars
            core_end = overlap_end - parameter_set.min_segment_bars + 1
            if core_start > core_end:
                continue

            comparable_anchor_pairs += 1
            if first_row.segment_count == 1 and second_row.segment_count == 1:
                both_single_pairs += 1
            first_core = [
                value for value in first_breakpoints if core_start <= value <= core_end
            ]
            second_core = [
                value for value in second_breakpoints if core_start <= value <= core_end
            ]
            pair_denominator = len(first_core) + len(second_core)
            if pair_denominator == 0:
                both_no_comparable_pairs += 1
                continue

            breakpoint_evidence_pairs += 1
            breakpoint_denominator += pair_denominator
            for tolerance in tolerances:
                matched_by_tolerance[tolerance] += _breakpoint_match_count(
                    first_core, second_core, tolerance
                )

    stability_by_tolerance = {
        tolerance: (
            2.0 * matched_by_tolerance[tolerance] / breakpoint_denominator
            if breakpoint_denominator
            else None
        )
        for tolerance in tolerances
    }
    pair_denominator = max(comparable_anchor_pairs, 1)
    result: dict[str, Any] = {
        "comparable_anchor_pair_count": comparable_anchor_pairs,
        "breakpoint_evidence_pair_count": breakpoint_evidence_pairs,
        "comparable_breakpoint_count": breakpoint_denominator,
        "both_single_segment_pair_rate": both_single_pairs / pair_denominator,
        "both_no_comparable_breakpoints_rate": (
            both_no_comparable_pairs / pair_denominator
        ),
    }
    result.update(
        {
            f"local_breakpoint_set_stability_tol_{tolerance}": value
            for tolerance, value in stability_by_tolerance.items()
        }
    )
    return result


def _score_parameter_set(
    parameter_set: ParameterSet,
    summaries: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    primary_tolerance: int,
    tolerances: list[int],
    anchor_pairs: list[tuple[int, int]],
) -> dict[str, Any]:
    if not summaries:
        return {
            "parameter_key": parameter_set.key,
            "ranking_status": "no_observations",
            "fit_complexity_score": None,
            "observation_count": 0,
        }
    summary_frame = pd.DataFrame(summaries)
    segment_frame = pd.DataFrame(segments)
    max_segment_count_hit_rate = float(
        (summary_frame["segment_count"] >= parameter_set.max_segments).mean()
    )
    multi_segment_model_rate = float((summary_frame["segment_count"] > 1).mean())
    positive_bic_rate = float((summary_frame["bic_improvement"] > 0).mean())
    near_min_segment_length_rate = float(
        (
            segment_frame["observation_count"]
            <= parameter_set.min_segment_bars + 1
        ).mean()
    )
    jump_share = pd.to_numeric(segment_frame["largest_move_path_share"], errors="coerce")
    jump_dominated_rate = float((jump_share >= 0.50).fillna(False).mean())

    single_rss = pd.to_numeric(summary_frame["single_segment_rss"], errors="coerce")
    selected_rss = pd.to_numeric(summary_frame["selected_rss"], errors="coerce")
    valid_rss = single_rss > 1e-12
    rss_reduction = (1.0 - selected_rss[valid_rss] / single_rss[valid_rss]).clip(0.0, 1.0)
    median_rss_reduction_ratio = (
        float(rss_reduction.median()) if not rss_reduction.empty else 0.0
    )
    linearity = pd.to_numeric(segment_frame["linearity_r2"], errors="coerce")
    weights = pd.to_numeric(segment_frame["observation_count"], errors="coerce")
    valid_linearity = linearity.notna() & weights.notna() & (weights > 0)
    weighted_segment_linearity_r2 = (
        float(
            (linearity[valid_linearity] * weights[valid_linearity]).sum()
            / weights[valid_linearity].sum()
        )
        if valid_linearity.any()
        else 0.0
    )
    stability_metrics = _local_breakpoint_stability_metrics(
        parameter_set, summary_frame, segment_frame, tolerances, anchor_pairs
    )
    local_breakpoint_set_stability = stability_metrics[
        f"local_breakpoint_set_stability_tol_{primary_tolerance}"
    ]
    fit_complexity_score = (
        0.35 * median_rss_reduction_ratio
        + 0.25 * weighted_segment_linearity_r2
        + 0.20 * (1.0 - max_segment_count_hit_rate)
        + 0.15 * (1.0 - near_min_segment_length_rate)
        + 0.05 * (1.0 - jump_dominated_rate)
    )
    return {
        "parameter_key": parameter_set.key,
        "bic_penalty_multiplier": parameter_set.bic_penalty_multiplier,
        "min_segment_bars": parameter_set.min_segment_bars,
        "max_segments": parameter_set.max_segments,
        "ranking_status": "ranked",
        "fit_complexity_score": fit_complexity_score,
        "breakpoint_tolerance_bars": primary_tolerance,
        "anchor_comparison_step_bars": anchor_pairs[0][1] - anchor_pairs[0][0],
        "local_breakpoint_set_stability": local_breakpoint_set_stability,
        **stability_metrics,
        "median_rss_reduction_ratio": median_rss_reduction_ratio,
        "weighted_segment_linearity_r2": weighted_segment_linearity_r2,
        "mean_segment_count": float(summary_frame["segment_count"].mean()),
        "multi_segment_model_rate": multi_segment_model_rate,
        "max_segment_count_hit_rate": max_segment_count_hit_rate,
        "near_min_segment_length_rate": near_min_segment_length_rate,
        "jump_dominated_rate": jump_dominated_rate,
        "positive_bic_improvement_rate": positive_bic_rate,
        "mean_bic_improvement": float(summary_frame["bic_improvement"].mean()),
        "observation_count": len(summary_frame),
    }


def _rank_cache(
    parameter_sets: list[ParameterSet],
    cache: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    primary_tolerance: int,
    tolerances: list[int],
    anchor_pairs: list[tuple[int, int]],
) -> pd.DataFrame:
    rows = [
        _score_parameter_set(
            parameter_set,
            *cache[parameter_set.key],
            primary_tolerance,
            tolerances,
            anchor_pairs,
        )
        for parameter_set in parameter_sets
        if parameter_set.key in cache
    ]
    ranking = pd.DataFrame(rows)
    if ranking.empty:
        return ranking
    return ranking.sort_values(
        ["fit_complexity_score", "parameter_key"],
        ascending=[False, True],
        na_position="last",
    )


def _staged_parameter_sets(
    frames: dict[str, pd.DataFrame],
    config: dict[str, Any],
    lookbacks: list[int],
    target_date: date,
    base_offsets: list[int],
    calculation_offsets: list[int],
    anchor_pairs: list[tuple[int, int]],
    cache: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    progress: Progress | None = None,
) -> list[ParameterSet]:
    production = _production_parameters()
    primary_tolerance, tolerances = _configured_tolerances(config)
    latest_only = [min(base_offsets)]
    stage_one = [
        ParameterSet(float(penalty), production.min_segment_bars, production.max_segments)
        for penalty in config.get("bic_penalty_grid", [1.5, 2.0, 3.0, 4.0, 5.0])
    ]
    _run_parameter_sets(
        frames,
        stage_one,
        lookbacks,
        target_date,
        latest_only,
        "bic_scan",
        cache,
        progress=progress,
    )
    stage_one_rank = _rank_cache(
        stage_one, cache, primary_tolerance, tolerances, anchor_pairs
    )
    penalty_count = int(config.get("top_penalties", 2))
    penalties = list(stage_one_rank.head(penalty_count)["bic_penalty_multiplier"])
    if production.bic_penalty_multiplier not in penalties:
        penalties.append(production.bic_penalty_multiplier)

    stage_two = [
        ParameterSet(float(penalty), int(minimum), production.max_segments)
        for penalty, minimum in product(
            penalties, config.get("min_segment_bars_grid", [5, 7, 10])
        )
    ]
    _run_parameter_sets(
        frames,
        stage_two,
        lookbacks,
        target_date,
        latest_only,
        "minimum_scan",
        cache,
        progress=progress,
    )
    stage_two_rank = _rank_cache(
        stage_two, cache, primary_tolerance, tolerances, anchor_pairs
    )
    top_count = int(config.get("top_parameter_sets", 4))
    finalists = [
        ParameterSet(
            float(row.bic_penalty_multiplier),
            int(row.min_segment_bars),
            int(maximum),
        )
        for row in stage_two_rank.head(top_count).itertuples()
        for maximum in config.get("max_segments_grid", [3, 4])
    ]
    if production not in finalists:
        finalists.append(production)
    _run_parameter_sets(
        frames,
        finalists,
        lookbacks,
        target_date,
        calculation_offsets,
        "stability_review",
        cache,
        refresh=True,
        progress=progress,
    )
    return sorted(set(finalists))


def _make_chart(
    frame: pd.DataFrame,
    summary: dict[str, Any],
    segments: list[dict[str, Any]],
) -> go.Figure:
    fit = reconstruct_adaptive_fit(
        frame.loc[frame.index.date <= summary["date"]],
        int(summary["lookback_bars"]),
        segments,
    )
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.72, 0.28],
    )
    figure.add_trace(
        go.Scatter(
            x=fit.index,
            y=fit["close"],
            name="Actual close",
            mode="lines",
            line={"color": "#30343b", "width": 2},
        ),
        row=1,
        col=1,
    )
    colors = ["#2563eb", "#d97706", "#059669", "#9333ea"]
    for index, segment in enumerate(sorted(segments, key=lambda row: row["segment_index"])):
        start = int(segment["start_bar_index"])
        end = int(segment["end_bar_index"]) + 1
        anchor = start if start == 0 else start - 1
        label = (
            f"S{index + 1}: {segment['start_date']} to {segment['end_date']}"
            f"<br>slope={float(segment['log_slope_per_bar']):.5f}, "
            f"R²={float(segment['linearity_r2']):.2f}"
        )
        figure.add_trace(
            go.Scatter(
                x=fit.index[anchor:end],
                y=fit["fitted_close"].iloc[anchor:end],
                name=label,
                mode="lines",
                line={"color": colors[index % len(colors)], "width": 3},
                hovertemplate="%{x|%Y-%m-%d}<br>fitted=%{y:.2f}<extra>" + label + "</extra>",
            ),
            row=1,
            col=1,
        )
        if index > 0:
            figure.add_vline(
                x=fit.index[start].timestamp() * 1000,
                line_width=1,
                line_dash="dash",
                line_color=colors[index % len(colors)],
                row="all",
            )
        move_date = segment.get("largest_move_date")
        if move_date is not None:
            matching = fit.loc[fit.index.date == move_date]
            if not matching.empty:
                figure.add_trace(
                    go.Scatter(
                        x=matching.index,
                        y=matching["close"],
                        name=f"S{index + 1} largest move",
                        mode="markers",
                        marker={
                            "color": colors[index % len(colors)],
                            "size": 8,
                            "symbol": "diamond",
                        },
                        showlegend=False,
                        hovertemplate=(
                            "%{x|%Y-%m-%d}<br>close=%{y:.2f}<br>largest daily move"
                            "<extra></extra>"
                        ),
                    ),
                    row=1,
                    col=1,
                )
    figure.add_trace(
        go.Bar(
            x=fit.index,
            y=fit["residual_log"],
            name="Log residual",
            marker_color="#64748b",
            hovertemplate="%{x|%Y-%m-%d}<br>log residual=%{y:.5f}<extra></extra>",
        ),
        row=2,
        col=1,
    )
    figure.add_hline(y=0, line_width=1, line_color="#94a3b8", row=2, col=1)
    figure.update_layout(
        title=(
            f"{summary['symbol']} · {summary['date']} · {summary['lookback_bars']} bars · "
            f"{summary['parameter_key']} · segments={summary['segment_count']} · "
            f"BIC improvement={float(summary['bic_improvement']):.2f}"
        ),
        height=570,
        margin={"l": 55, "r": 25, "t": 70, "b": 45},
        legend={"orientation": "h", "y": 1.02, "x": 0},
        template="plotly_white",
    )
    figure.update_yaxes(title_text="Close", row=1, col=1)
    figure.update_yaxes(title_text="Log residual", row=2, col=1)
    return figure


def _parameter_field_guide(
    primary_tolerance: int,
    tolerances: list[int],
    anchor_comparison_step_bars: int,
) -> str:
    sensitivity_fields = ", ".join(
        f"local_breakpoint_set_stability_tol_{tolerance}"
        for tolerance in tolerances
    )
    entries = [
        (
            "production",
            "是否为 indicators.adaptive_trend 当前生产参数。报告始终保留生产参数用于"
            "对照；它不影响排名计算。",
        ),
        (
            "parameter_key",
            "分段模型参数组合的稳定标识，格式为 bic=&lt;penalty&gt;|min=&lt;bars&gt;|"
            "max=&lt;segments&gt;。断点匹配容差和 anchor 设置只用于事后评价，因此不进入"
            "这个 key。",
        ),
        (
            "evaluation_scope",
            "评价范围。full_parameter_grid 表示完整网格在全部局部 anchor 对上运行；"
            "historical_stability_review 表示分阶段模式的最终复验；"
            "latest_anchor_screening 只使用最新截面初筛，不能提供局部稳定性证据。",
        ),
        (
            "bic_penalty_multiplier",
            "BIC 复杂度惩罚乘数。BIC = 拟合误差项 + multiplier × 参数数量 × log(n)。"
            "数值越高越不容易选择更多分段；它会改变最终 segment_count，但不会改变"
            "固定分段数下的最小 RSS 断点位置。",
        ),
        (
            "min_segment_bars",
            "每个分段必须拥有的最少 observations/bars。数值越大，合法断点范围越窄，"
            "也越难用短期波动形成小分段。局部比较时，它还用于求两个窗口合法断点位置"
            "集合的交集。",
        ),
        (
            "max_segments",
            "模型允许的最大分段数量。实际 segment_count 由 BIC 在 1 到此上限之间选择；"
            "频繁达到上限可能表示参数空间不足或存在过度分段风险。",
        ),
        (
            "ranking_status",
            "ranked 表示该参数组合存在有效样本，已计算拟合质量与复杂度分数并参与排名；"
            "no_observations 表示没有有效样本，无法计算分数。断点评价证据是否存在不会改变"
            "ranking_status；相关证据数量和稳定性仅作为独立诊断字段展示。",
        ),
        (
            "fit_complexity_score",
            "0–1 拟合质量与复杂度排序分数：35% × median_rss_reduction_ratio + "
            "25% × weighted_segment_linearity_r2 + 20% × (1 - max_segment_count_hit_rate) + "
            "15% × (1 - near_min_segment_length_rate) + 5% × (1 - jump_dominated_rate)。"
            "分阶段初筛和最终历史复验使用同一公式，但各自基于 evaluation_scope 指定的样本。"
            "local_breakpoint_set_stability 不进入该分数。分数用于比较拟合收益与复杂度风险的"
            "平衡，不是准确率、概率或自动调参结论。",
        ),
        (
            "anchor_comparison_step_bars",
            f"每个基准 anchor 与 base + step 的局部输入差值；本报告为 "
            f"{anchor_comparison_step_bars} bars。程序只比较明确的局部 pair，不会比较"
            "两个相邻基准 anchor。step 小于候选最短分段时，新数据本身不足以单独形成"
            "一个合法分段，更适合检验局部算法稳定性。",
        ),
        (
            "breakpoint_tolerance_bars",
            f"主断点匹配容差，本报告为 ±{primary_tolerance} 个全局交易 bars。两个断点"
            "在容差内通过一对一有序匹配视为同一个断点；该值只影响稳定性诊断，不参与"
            "fit_complexity_score 或参数排名。",
        ),
        (
            "local_breakpoint_set_stability",
            "主容差下的对称断点集合稳定性，计算为 2 × matched_breakpoints ÷ "
            "comparable_breakpoint_count。只比较两个窗口共同合法核心中的断点；双方都"
            "没有断点时不自动记满分。它衡量少量输入变化下输出集合的一致性，不解释"
            "断点出现或消失的经济原因。",
        ),
        (
            sensitivity_fields,
            "不同匹配容差下的稳定性敏感度列。它们复用完全相同的分段结果，只改变"
            "事后断点匹配范围；主容差列同时写入 local_breakpoint_set_stability，其他列用于"
            "观察结论是否对容差敏感。所有容差列都不参与参数排名。",
        ),
        (
            "comparable_anchor_pair_count",
            "成功形成共同合法核心的 symbol × lookback × 局部 anchor pair 数量。每个"
            "配置的 pair 由 (base_offset, base_offset + step) 明确定义。该字段衡量"
            "稳定性证据覆盖了多少标的、窗口和历史位置。",
        ),
        (
            "breakpoint_evidence_pair_count",
            "共同合法核心中至少一边存在断点的 anchor pair 数量。双方核心都没有断点"
            "的 pair 不提供集合匹配证据，因此不会进入稳定性公式分母。",
        ),
        (
            "comparable_breakpoint_count",
            "所有有断点证据的 pair 中，两边核心断点数量之和，即稳定性公式的分母。"
            "它不是唯一断点数；同一历史断点参与不同 pair 时会分别计数。",
        ),
        (
            "both_single_segment_pair_rate",
            "可比较 anchor pair 中，两边模型都选择单段的比例。高值表示模型经常保持"
            "不分段；它单独报告，避免把持续无断点错误解释为断点稳定。",
        ),
        (
            "both_no_comparable_breakpoints_rate",
            "可比较 anchor pair 中，经过共同合法核心过滤后两边都没有断点的比例。"
            "即使窗口整体为多段，断点全部落在核心之外时也会进入该比例。",
        ),
        (
            "median_rss_reduction_ratio",
            "相对单段模型的 RSS 改善比例中位数：median(1 - selected_rss / "
            "single_segment_rss)，限制在 0–1。它衡量多段结构带来的实际拟合改善；"
            "更多分段天然更容易降低 RSS，因此必须与复杂度风险指标一起解释。",
        ),
        (
            "weighted_segment_linearity_r2",
            "所有分段 linearity_r2 按 observation_count 加权的平均值。它衡量选中分段"
            "内部是否接近 log-price 对 bar-index 的直线；较长分段权重更高。",
        ),
        (
            "mean_segment_count",
            "所有有效 symbol × anchor × lookback 样本的平均分段数。接近 1 表示保守；"
            "接近 max_segments 表示模型经常使用复杂结构，本身没有固定的最优目标。",
        ),
        (
            "multi_segment_model_rate",
            "有效样本中 segment_count &gt; 1 的比例。用于观察模型识别结构变化的频率，"
            "不直接进入 fit_complexity_score，因为没有先验依据规定理想多段比例。",
        ),
        (
            "max_segment_count_hit_rate",
            "有效样本中 segment_count 达到 max_segments 的比例。高值表示算法频繁触及"
            "配置上限，可能意味着 penalty 太低、最短段太短或上限不足。",
        ),
        (
            "near_min_segment_length_rate",
            "所有实际分段中 observation_count ≤ min_segment_bars + 1 的比例。高值表示"
            "算法经常使用接近长度约束边界的小分段，可能存在局部过拟合。",
        ),
        (
            "jump_dominated_rate",
            "largest_move_path_share ≥ 0.50 的分段比例，即单个交易日贡献至少一半绝对"
            "路径。用于识别断点和趋势是否主要由跳空或异常单日变化驱动。",
        ),
        (
            "positive_bic_improvement_rate",
            "bic_improvement = single_segment_bic - selected_bic 为正的样本比例。正值表示"
            "选中多段模型在考虑复杂度惩罚后仍优于单段模型；通常与多段模型比例接近。",
        ),
        (
            "mean_bic_improvement",
            "所有有效样本的平均 BIC improvement。不同 bic_penalty_multiplier 会直接改变"
            "该量的尺度，因此不应脱离 penalty 做简单的跨行越大越好比较。",
        ),
        (
            "observation_count",
            "该参数组合成功生成的 symbol × anchor × lookback 摘要行数。局部比较模式会"
            "同时计算每个 base anchor 和 base + step，因此它不是行情 bars、分段数或"
            "断点数。",
        ),
    ]
    definitions = "".join(
        f"<dt><code>{field}</code></dt><dd>{description}</dd>"
        for field, description in entries
    )
    return (
        '<details class="field-guide">'
        '<summary>展开查看 Parameter comparison 字段与算法说明</summary>'
        '<p>比例字段通常位于 0–1。比较参数时应先确认 evaluation_scope 和 '
        'observation_count 一致，再结合图形人工复核。</p>'
        f"<dl>{definitions}</dl>"
        "</details>"
    )


def _render_report(
    output_dir: Path,
    frames: dict[str, pd.DataFrame],
    all_summaries: pd.DataFrame,
    all_segments: pd.DataFrame,
    ranking: pd.DataFrame,
    production: ParameterSet,
    chart_count: int,
    primary_tolerance: int,
    tolerances: list[int],
    anchor_comparison_step_bars: int,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    all_summaries.to_csv(output_dir / "summary.csv", index=False)
    all_segments.to_csv(output_dir / "segments.csv", index=False)
    ranking.to_csv(output_dir / "parameter_comparison.csv", index=False)

    historical = ranking.loc[
        ranking["evaluation_scope"] != "latest_anchor_screening"
    ]
    ranked = historical.loc[historical["ranking_status"] == "ranked"]
    fallback = historical.loc[~historical["parameter_key"].isin(ranked["parameter_key"])]
    chart_candidates = pd.concat([ranked, fallback], ignore_index=True)
    chart_keys = list(
        chart_candidates["parameter_key"].drop_duplicates().head(chart_count)
    )
    if production.key not in chart_keys:
        chart_keys.append(production.key)
    table = ranking.copy()
    table.insert(0, "production", table["parameter_key"].eq(production.key))
    table_html = table.to_html(
        index=False,
        border=0,
        classes="ranking",
        float_format=lambda value: f"{value:.4f}",
    )
    field_guide_html = _parameter_field_guide(
        primary_tolerance, tolerances, anchor_comparison_step_bars
    )
    chart_html: list[str] = []
    plotly_included = False
    filtered = all_summaries.loc[all_summaries["parameter_key"].isin(chart_keys)]
    for summary in filtered.sort_values(
        ["symbol", "date", "lookback_bars", "parameter_key"],
        ascending=[True, False, True, True],
    ).to_dict("records"):
        segment_rows = all_segments.loc[
            (all_segments["parameter_key"] == summary["parameter_key"])
            & (all_segments["symbol"] == summary["symbol"])
            & (all_segments["date"] == summary["date"])
            & (all_segments["lookback_bars"] == summary["lookback_bars"])
        ].to_dict("records")
        figure = _make_chart(frames[str(summary["symbol"])], summary, segment_rows)
        chart_html.append(
            pio.to_html(
                figure,
                full_html=False,
                include_plotlyjs=True if not plotly_included else False,
                config={"responsive": True, "displaylogo": False},
            )
        )
        plotly_included = True

    best_key = (
        escape(str(chart_candidates.iloc[0]["parameter_key"]))
        if not chart_candidates.empty
        else "n/a"
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Adaptive trend validation</title>
<style>
body {{ font-family: system-ui, sans-serif; margin: 24px; color: #1f2937; }}
h1, h2 {{ font-weight: 500; }}
.note {{ max-width: 1000px; line-height: 1.5; }}
.ranking {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
.ranking th, .ranking td {{ border-bottom: 1px solid #d1d5db; padding: 7px; text-align: right; }}
.ranking th:nth-child(-n+2), .ranking td:nth-child(-n+2) {{ text-align: left; }}
.table-wrap {{ overflow-x: auto; }}
.field-guide {{ margin: 12px 0 18px; max-width: 1100px; }}
.field-guide summary {{ cursor: pointer; font-weight: 500; }}
.field-guide p {{ line-height: 1.5; }}
.field-guide dl {{ display: grid; grid-template-columns: minmax(240px, 0.35fr) 1fr; gap: 0; }}
.field-guide dt, .field-guide dd {{
  margin: 0;
  padding: 9px 8px;
  border-bottom: 1px solid #d1d5db;
  line-height: 1.5;
}}
.field-guide dt {{ overflow-wrap: anywhere; }}
@media (max-width: 760px) {{
  .field-guide dl {{ grid-template-columns: 1fr; }}
  .field-guide dt {{ border-bottom: 0; padding-bottom: 2px; }}
  .field-guide dd {{ padding-top: 2px; }}
}}
.chart {{ margin-top: 28px; }}
</style>
</head>
<body>
<h1>Adaptive trend segmentation validation</h1>
<p class="note">Top chart candidate: <strong>{best_key}</strong>.
The fit-complexity score balances fit improvement, segment linearity and
complexity-risk diagnostics. Local breakpoint-set stability is reported
separately and does not affect ranking. The score is not a statistical guarantee.
Inspect the fitted paths and residuals before changing production settings.</p>
<h2>Parameter comparison</h2>
{field_guide_html}
<div class="table-wrap">{table_html}</div>
<h2>Segmentation charts</h2>
{''.join(f'<div class="chart">{chart}</div>' for chart in chart_html)}
</body>
</html>"""
    report_path = output_dir / "index.html"
    report_path.write_text(html, encoding="utf-8")
    return report_path


def run_adaptive_trend_validation(
    symbols: list[str] | None = None,
    target_date: date | None = None,
    output_dir: Path | None = None,
    full_grid: bool = False,
    show_progress: bool = False,
) -> Path:
    """Run a read-only parameter sweep and write a self-contained validation report."""
    config = _validation_config()
    selected_symbols = symbols or [str(value) for value in config.get("symbols", [])]
    if not selected_symbols:
        raise ValueError("At least one validation symbol is required.")
    lookbacks = [
        int(value)
        for value in settings.indicators.get("adaptive_trend", {}).get(
            "lookbacks", [60]
        )
    ]
    base_offsets, anchor_pairs, calculation_offsets = _configured_anchor_pairs(config)
    as_of = target_date or date.today()
    source = str(settings.pipeline.get("source", "tiingo"))
    frames = {
        symbol: frame
        for symbol in selected_symbols
        for frame in [fetch_ohlcv(symbol, source=source)]
        if not frame.empty
    }
    missing = [symbol for symbol in selected_symbols if symbol not in frames]
    if missing:
        log.warning("adaptive_trend_validation.symbols.missing", symbols=missing)
    if not frames:
        raise ValueError("No OHLCV data found for the requested validation symbols.")

    cache: dict[str, tuple[list[dict[str, Any]], list[dict[str, Any]]]] = {}
    progress = (
        Progress(
            TextColumn("{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
        )
        if show_progress
        else None
    )
    if progress is not None:
        progress.start()
    try:
        if full_grid:
            parameter_sets = _parameter_grid(config)
            _run_parameter_sets(
                frames,
                parameter_sets,
                lookbacks,
                as_of,
                calculation_offsets,
                "full_grid",
                cache,
                progress=progress,
            )
        else:
            parameter_sets = _staged_parameter_sets(
                frames,
                config,
                lookbacks,
                as_of,
                base_offsets,
                calculation_offsets,
                anchor_pairs,
                cache,
                progress,
            )
    finally:
        if progress is not None:
            progress.stop()

    primary_tolerance, tolerances = _configured_tolerances(config)
    ranking = _rank_cache(
        parameter_sets, cache, primary_tolerance, tolerances, anchor_pairs
    )
    ranking.insert(
        1,
        "evaluation_scope",
        "full_parameter_grid" if full_grid else "historical_stability_review",
    )
    if not full_grid:
        finalist_keys = {parameter_set.key for parameter_set in parameter_sets}
        screening_sets = [
            ParameterSet(
                float(summaries[0]["bic_penalty_multiplier"]),
                int(summaries[0]["min_segment_bars"]),
                int(summaries[0]["max_segments"]),
            )
            for key, (summaries, _) in cache.items()
            if key not in finalist_keys and summaries
        ]
        if screening_sets:
            screening_ranking = _rank_cache(
                screening_sets,
                cache,
                primary_tolerance,
                tolerances,
                anchor_pairs,
            )
            screening_ranking.insert(1, "evaluation_scope", "latest_anchor_screening")
            ranking = pd.concat([ranking, screening_ranking], ignore_index=True)
    production = _production_parameters()
    all_summaries = pd.DataFrame(
        [row for summaries, _ in cache.values() for row in summaries]
    ).drop_duplicates(["parameter_key", "symbol", "date", "lookback_bars"])
    all_summaries["is_production"] = all_summaries["parameter_key"].eq(production.key)
    all_segments = pd.DataFrame(
        [row for _, segments in cache.values() for row in segments]
    ).drop_duplicates(
        ["parameter_key", "symbol", "date", "lookback_bars", "segment_index"]
    )
    destination = output_dir or _default_output_dir(as_of)
    report_path = _render_report(
        destination,
        frames,
        all_summaries,
        all_segments,
        ranking,
        production,
        int(config.get("chart_parameter_sets", 3)),
        primary_tolerance,
        tolerances,
        anchor_pairs[0][1] - anchor_pairs[0][0],
    )
    log.info(
        "adaptive_trend_validation.done",
        report=str(report_path),
        symbols=len(frames),
        parameter_sets=len(ranking),
    )
    return report_path
