# ruff: noqa: E501
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
from math import exp, log, sin
from pathlib import Path
from typing import Any

from market_analysis.config import settings
from market_analysis.indicators.trend_pattern import classify_trend_pattern

_PROJECT_ROOT = Path(__file__).parents[3]
_DEFAULT_OUTPUT = _PROJECT_ROOT / "artifacts" / "trend_pattern_reference.html"


@dataclass(frozen=True)
class PatternExample:
    pattern: str
    chinese_name: str
    logic: str
    parameters: tuple[str, ...]
    examples: tuple[tuple[float, ...], ...] = ()
    note: str = ""


PATTERN_EXAMPLES: tuple[PatternExample, ...] = (
    PatternExample(
        "range_bound",
        "区间震荡",
        "两条路径：所有分段均为 flat；或至少 3 个有效 leg、Q 不高于区间阈值，"
        "且振幅既不连续收敛也不连续扩张。",
        (
            "min_abs_fitted_log_return",
            "min_linearity_r2",
            "min_abs_vol_adjusted_trend",
            "range_net_to_gross_max",
            "swing_amplitude_change_min",
        ),
        ((0.005,), (0.08, -0.08, 0.02)),
        "第一幅为全 flat，第二幅为多段上下抵消。",
    ),
    PatternExample(
        "contracting_range",
        "收敛区间",
        "至少 3 个有效 leg，Q 不高于区间阈值；每个后续振幅均不超过前一振幅乘以 "
        "(1 - swing_amplitude_change_min)。",
        ("range_net_to_gross_max", "swing_amplitude_change_min"),
        ((0.10, -0.07, 0.04),),
    ),
    PatternExample(
        "expanding_range",
        "扩张区间",
        "至少 3 个有效 leg，Q 不高于区间阈值；每个后续振幅均不低于前一振幅乘以 "
        "(1 + swing_amplitude_change_min)。",
        ("range_net_to_gross_max", "swing_amplitude_change_min"),
        ((0.04, -0.08, 0.12),),
    ),
    PatternExample(
        "uptrend",
        "单段上涨趋势",
        "去除 flat 并合并同方向分段后只有一个 up leg，且原始末段不是 flat。",
        (
            "min_abs_fitted_log_return",
            "min_linearity_r2",
            "min_abs_vol_adjusted_trend",
        ),
        ((0.10,),),
    ),
    PatternExample(
        "downtrend",
        "单段下跌趋势",
        "去除 flat 并合并同方向分段后只有一个 down leg，且原始末段不是 flat。",
        (
            "min_abs_fitted_log_return",
            "min_linearity_r2",
            "min_abs_vol_adjusted_trend",
        ),
        ((-0.10,),),
    ),
    PatternExample(
        "uptrend_then_sideways",
        "上涨后横盘",
        "只有一个有效 up leg，但原始方向序列的最后一段为 flat。",
        (
            "min_abs_fitted_log_return",
            "min_linearity_r2",
            "min_abs_vol_adjusted_trend",
        ),
        ((0.10, 0.005),),
    ),
    PatternExample(
        "downtrend_then_sideways",
        "下跌后横盘",
        "只有一个有效 down leg，但原始方向序列的最后一段为 flat。",
        (
            "min_abs_fitted_log_return",
            "min_linearity_r2",
            "min_abs_vol_adjusted_trend",
        ),
        ((-0.10, -0.005),),
    ),
    PatternExample(
        "uptrend_resumption",
        "上涨趋势恢复",
        "有效 legs 为 up > down > up，净 fitted return 为正，且最新 up 振幅大于中间 down 振幅。",
        ("range_net_to_gross_max", "swing_similarity_tolerance"),
        ((0.10, -0.03, 0.08),),
        "double test 与 range 规则优先；示例刻意避开这两类条件。",
    ),
    PatternExample(
        "uptrend_with_pullback",
        "上涨趋势伴随回撤",
        "有效 legs 为 up > down > up，净 fitted return 为正，且最新 up 振幅不大于中间 down 振幅。",
        ("range_net_to_gross_max", "swing_similarity_tolerance"),
        ((0.10, -0.05, 0.03),),
        "double test 与 range 规则优先；示例刻意避开这两类条件。",
    ),
    PatternExample(
        "downtrend_resumption",
        "下跌趋势恢复",
        "有效 legs 为 down > up > down，净 fitted return 为负，且最新 down 振幅大于中间 up 振幅。",
        ("range_net_to_gross_max", "swing_similarity_tolerance"),
        ((-0.10, 0.03, -0.08),),
        "double test 与 range 规则优先；示例刻意避开这两类条件。",
    ),
    PatternExample(
        "downtrend_with_rebound",
        "下跌趋势伴随反弹",
        "有效 legs 为 down > up > down，净 fitted return 为负，且最新 down 振幅不大于中间 up 振幅。",
        ("range_net_to_gross_max", "swing_similarity_tolerance"),
        ((-0.10, 0.05, -0.03),),
        "double test 与 range 规则优先；示例刻意避开这两类条件。",
    ),
    PatternExample(
        "uptrend_with_multiple_pullbacks",
        "上涨趋势伴随多次回撤",
        "至少 4 个有效 legs，首个方向为 up，净 fitted return 为正；且未先命中 double test 或 range。",
        (
            "range_net_to_gross_max",
            "swing_similarity_tolerance",
            "double_test_confirmation_ratio",
        ),
        ((0.12, -0.03, 0.08, -0.02),),
    ),
    PatternExample(
        "downtrend_with_multiple_rebounds",
        "下跌趋势伴随多次反弹",
        "至少 4 个有效 legs，首个方向为 down，净 fitted return 为负；且未先命中 double test 或 range。",
        (
            "range_net_to_gross_max",
            "swing_similarity_tolerance",
            "double_test_confirmation_ratio",
        ),
        ((-0.12, 0.03, -0.08, 0.02),),
    ),
    PatternExample(
        "bottom_reversal",
        "底部反转",
        "恰好两个有效 legs，方向为 down > up，且原始末段不是 flat。当前规则不额外要求反转比例。",
        (
            "min_abs_fitted_log_return",
            "min_linearity_r2",
            "min_abs_vol_adjusted_trend",
        ),
        ((-0.10, 0.08),),
    ),
    PatternExample(
        "top_reversal",
        "顶部反转",
        "恰好两个有效 legs，方向为 up > down，且原始末段不是 flat。当前规则不额外要求反转比例。",
        (
            "min_abs_fitted_log_return",
            "min_linearity_r2",
            "min_abs_vol_adjusted_trend",
        ),
        ((0.10, -0.08),),
    ),
    PatternExample(
        "bottom_reversal_then_sideways",
        "底部反转后横盘",
        "两个有效 legs 为 down > up，并且原始方向序列最后一段为 flat。",
        (
            "min_abs_fitted_log_return",
            "min_linearity_r2",
            "min_abs_vol_adjusted_trend",
        ),
        ((-0.10, 0.08, 0.005),),
    ),
    PatternExample(
        "top_reversal_then_sideways",
        "顶部反转后横盘",
        "两个有效 legs 为 up > down，并且原始方向序列最后一段为 flat。",
        (
            "min_abs_fitted_log_return",
            "min_linearity_r2",
            "min_abs_vol_adjusted_trend",
        ),
        ((0.10, -0.08, -0.005),),
    ),
    PatternExample(
        "double_bottom_like",
        "近似双底",
        "3-leg 路径 up > down > up 比较 A1/A2、以 A3 确认；4-leg 路径 down > up > down > up "
        "比较 A2/A3、以 A4 确认。参考振幅相似且确认振幅达标。",
        ("swing_similarity_tolerance", "double_test_confirmation_ratio"),
        ((0.08, -0.075, 0.06),),
        "double test 在 3 个及以上有效 legs 的规则中优先于 range。",
    ),
    PatternExample(
        "double_top_like",
        "近似双顶",
        "3-leg 路径 down > up > down 比较 A1/A2、以 A3 确认；4-leg 路径 up > down > up > down "
        "比较 A2/A3、以 A4 确认。参考振幅相似且确认振幅达标。",
        ("swing_similarity_tolerance", "double_test_confirmation_ratio"),
        ((-0.08, 0.075, -0.06),),
        "double test 在 3 个及以上有效 legs 的规则中优先于 range。",
    ),
    PatternExample(
        "complex_bottom_reversal",
        "复杂底部反转",
        "3 legs 为 down > up > down 但净 fitted return 非负；或至少 4 legs、首个方向 down、"
        "最终净 fitted return 为正。",
        (
            "range_net_to_gross_max",
            "swing_similarity_tolerance",
            "double_test_confirmation_ratio",
        ),
        ((-0.03, 0.15, -0.04),),
        "必须先避开 double test 和 range。",
    ),
    PatternExample(
        "complex_top_reversal",
        "复杂顶部反转",
        "3 legs 为 up > down > up 但净 fitted return 非正；或至少 4 legs、首个方向 up、"
        "最终净 fitted return 为负。",
        (
            "range_net_to_gross_max",
            "swing_similarity_tolerance",
            "double_test_confirmation_ratio",
        ),
        ((0.03, -0.15, 0.04),),
        "必须先避开 double test 和 range。",
    ),
    PatternExample(
        "irregular_path",
        "不规则路径（兜底）",
        "初始化兜底值：当路径未命中任何受支持的几何规则时使用。当前有效 legs 会交替，且后续分支"
        "基本穷尽合法路径，因此正常、完整输入下没有可构造的可达示例。",
        (),
        (),
        "保留该行是为了完整说明输出契约；不伪造一个无法通过当前分类函数验证的 K 线案例。",
    ),
)


def _summary(segment_count: int) -> dict[str, Any]:
    return {
        "symbol": "SYNTH",
        "date": date(2026, 7, 20),
        "lookback_bars": 40,
        "segment_count": segment_count,
    }


def _segments(returns: tuple[float, ...]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": "SYNTH",
            "date": date(2026, 7, 20),
            "lookback_bars": 40,
            "segment_index": index,
            "fitted_log_return": value,
            "log_slope_per_bar": value / 6,
            "linearity_r2": 0.80,
            "vol_adjusted_trend": 1.50 if value >= 0 else -1.50,
        }
        for index, value in enumerate(returns)
    ]


def _trend_pattern_params() -> dict[str, Any]:
    return dict(settings.indicators.get("trend_pattern", {}))


def classify_example(
    returns: tuple[float, ...],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_params = params if params is not None else _trend_pattern_params()
    return classify_trend_pattern(
        _summary(len(returns)),
        _segments(returns),
        active_params,
    )


def _direction(value: float, params: dict[str, Any]) -> str:
    min_return = float(params.get("min_abs_fitted_log_return", 0.02))
    if abs(value) < min_return:
        return "flat"
    return "up" if value > 0 else "down"


def _synthetic_prices(returns: tuple[float, ...]) -> tuple[list[float], list[int]]:
    points = [log(100.0)]
    boundaries = [0]
    bars_per_segment = 6
    for segment_index, fitted_return in enumerate(returns):
        start = points[-1]
        for step in range(1, bars_per_segment + 1):
            fraction = step / bars_per_segment
            curve = 0.0025 * sin(fraction * 3.141592653589793)
            curve *= 1 if segment_index % 2 == 0 else -1
            points.append(start + fitted_return * fraction + curve)
        boundaries.append(len(points) - 1)
    return [exp(value) for value in points], boundaries


def _chart_svg(
    pattern: str,
    returns: tuple[float, ...],
    chart_index: int,
    params: dict[str, Any],
) -> str:
    closes, boundaries = _synthetic_prices(returns)
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else close * 0.998
        offset = (0.0008 if index % 3 == 0 else -0.0005) * previous
        open_price = previous + offset
        opens.append(open_price)
        highs.append(max(open_price, close) * 1.0035)
        lows.append(min(open_price, close) * 0.9965)

    width, height = 280.0, 126.0
    left, right, top, bottom = 9.0, 9.0, 16.0, 19.0
    plot_width = width - left - right
    plot_height = height - top - bottom
    low_value, high_value = min(lows), max(highs)
    span = max(high_value - low_value, 1e-6)

    def x(index: int) -> float:
        return left + plot_width * index / max(len(closes) - 1, 1)

    def y(value: float) -> float:
        return top + plot_height * (high_value - value) / span

    candle_width = max(2.4, min(6.0, plot_width / max(len(closes), 1) * 0.55))
    marks: list[str] = []
    for index, (open_price, high, low, close) in enumerate(
        zip(opens, highs, lows, closes, strict=True)
    ):
        state = "up" if close >= open_price else "down"
        center = x(index)
        body_top = min(y(open_price), y(close))
        body_height = max(abs(y(open_price) - y(close)), 1.2)
        marks.append(
            f'<line class="wick {state}" x1="{center:.2f}" y1="{y(high):.2f}" '
            f'x2="{center:.2f}" y2="{y(low):.2f}"/>'
        )
        marks.append(
            f'<rect class="candle {state}" x="{center - candle_width / 2:.2f}" '
            f'y="{body_top:.2f}" width="{candle_width:.2f}" height="{body_height:.2f}"/>'
        )

    overlays: list[str] = []
    for segment_index, fitted_return in enumerate(returns):
        start_index = boundaries[segment_index]
        end_index = boundaries[segment_index + 1]
        state = _direction(fitted_return, params)
        overlays.append(
            f'<line class="fit {state}" x1="{x(start_index):.2f}" '
            f'y1="{y(closes[start_index]):.2f}" x2="{x(end_index):.2f}" '
            f'y2="{y(closes[end_index]):.2f}"/>'
        )
        label_x = (x(start_index) + x(end_index)) / 2
        overlays.append(
            f'<text class="leg-label" x="{label_x:.2f}" y="11">{state}</text>'
        )
        if segment_index:
            overlays.append(
                f'<line class="breakpoint" x1="{x(start_index):.2f}" y1="{top:.2f}" '
                f'x2="{x(start_index):.2f}" y2="{height - bottom:.2f}"/>'
            )

    formatted_returns = " > ".join(f"{value:+.3f}" for value in returns)
    return (
        f'<svg class="mini-chart" viewBox="0 0 {width:.0f} {height:.0f}" role="img" '
        f'aria-labelledby="chart-{escape(pattern)}-{chart_index}-title '
        f'chart-{escape(pattern)}-{chart_index}-desc">'
        f'<title id="chart-{escape(pattern)}-{chart_index}-title">{escape(pattern)} 模拟 K 线</title>'
        f'<desc id="chart-{escape(pattern)}-{chart_index}-desc">拟合 log return 序列：'
        f'{escape(formatted_returns)}</desc>'
        '<line class="gridline" x1="9" y1="61" x2="271" y2="61"/>'
        + "".join(marks)
        + "".join(overlays)
        + f'<text class="return-label" x="140" y="123">{escape(formatted_returns)}</text>'
        + "</svg>"
    )


def _unreachable_chart() -> str:
    return (
        '<svg class="mini-chart" viewBox="0 0 280 126" role="img" '
        'aria-label="irregular_path 当前没有合法可达的模拟案例">'
        '<line class="gridline" x1="9" y1="63" x2="271" y2="63"/>'
        '<path class="unreachable" d="M18 77 L62 42 L101 78 L143 39 L184 83 L226 46 L263 69"/>'
        '<text class="unreachable-label" x="140" y="103">当前合法输入下无可达案例</text>'
        '</svg>'
    )


def _example_results(
    item: PatternExample,
    params: dict[str, Any],
) -> list[dict[str, Any]]:
    results = [classify_example(returns, params) for returns in item.examples]
    mismatches = [result["pattern"] for result in results if result["pattern"] != item.pattern]
    if mismatches:
        raise ValueError(
            f"Synthetic example for {item.pattern} classified as {', '.join(mismatches)}."
        )
    return results


_DIRECTION_PARAMETERS = (
    "min_abs_fitted_log_return",
    "min_linearity_r2",
    "min_abs_vol_adjusted_trend",
)


def _format_threshold(value: Any) -> str:
    return f"{float(value):g}"


def _effective_parameters(item: PatternExample) -> tuple[str, ...]:
    if not item.examples:
        return item.parameters
    return tuple(dict.fromkeys((*_DIRECTION_PARAMETERS, *item.parameters)))


def _threshold_lines(
    parameter_names: tuple[str, ...],
    params: dict[str, Any],
) -> list[str]:
    names = set(parameter_names)
    lines: list[str] = []
    if names.intersection(_DIRECTION_PARAMETERS):
        min_return = _format_threshold(params["min_abs_fitted_log_return"])
        min_r2 = _format_threshold(params["min_linearity_r2"])
        min_vol = _format_threshold(params["min_abs_vol_adjusted_trend"])
        lines.append(
            "方向段：abs(fitted_log_return) &gt;= "
            f"{min_return}，并且 linearity_r2 &gt;= {min_r2} "
            f"或 abs(vol_adjusted_trend) &gt;= {min_vol}"
        )
    if "range_net_to_gross_max" in names:
        value = _format_threshold(params["range_net_to_gross_max"])
        lines.append(f"区间判定：Q &lt;= {value}")
    if "swing_similarity_tolerance" in names:
        value = float(params["swing_similarity_tolerance"])
        lines.append(
            "摆动相似：relative_difference &lt;= "
            f"{_format_threshold(value)}，等价于 similarity &gt;= "
            f"{_format_threshold(1.0 - value)}"
        )
    if "swing_amplitude_change_min" in names:
        value = float(params["swing_amplitude_change_min"])
        lines.append(
            "振幅变化：收敛 current &lt;= previous × "
            f"{_format_threshold(1.0 - value)}；扩张 current &gt;= previous × "
            f"{_format_threshold(1.0 + value)}"
        )
    if "double_test_confirmation_ratio" in names:
        value = _format_threshold(params["double_test_confirmation_ratio"])
        lines.append(
            "double test 确认：confirmation &gt;= "
            f"max(reference_left, reference_right) × {value}"
        )
    return lines


def _parameter_html(
    parameter_names: tuple[str, ...],
    params: dict[str, Any],
) -> str:
    if not parameter_names:
        return '<span class="muted">无直接参数；代码兜底</span>'
    return "".join(
        '<div class="parameter-item">'
        f"<code>{escape(parameter)}</code>"
        f'<span class="parameter-value">= {_format_threshold(params[parameter])}</span>'
        "</div>"
        for parameter in parameter_names
    )


def _render_row(
    item: PatternExample,
    params: dict[str, Any],
) -> tuple[str, str]:
    results = _example_results(item, params)
    if results:
        regime = str(results[0]["regime"])
        attributes = sorted(
            {
                (
                    str(result["directional_bias"]),
                    str(result["path_structure"]),
                    str(result["terminal_state"]),
                )
                for result in results
            }
        )
        attribute_text = " / ".join(
            f"bias={bias}, structure={structure}, terminal={terminal}"
            for bias, structure, terminal in attributes
        )
        charts = "".join(
            _chart_svg(item.pattern, returns, index, params)
            for index, returns in enumerate(item.examples)
        )
        sequences = " / ".join(str(result["direction_sequence"]) for result in results)
    else:
        regime = "irregular"
        attribute_text = "bias=neutral, structure=mixed, terminal=—"
        charts = _unreachable_chart()
        sequences = "无可达合法示例"

    parameter_names = _effective_parameters(item)
    parameter_html = _parameter_html(parameter_names, params)
    threshold_lines = _threshold_lines(parameter_names, params)
    threshold_html = (
        '<div class="thresholds"><div class="threshold-title">当前阈值</div>'
        + "".join(f"<div>{line}</div>" for line in threshold_lines)
        + "</div>"
        if threshold_lines
        else ""
    )
    note_html = f'<p class="note">{escape(item.note)}</p>' if item.note else ""
    row = f"""
<tr class="pattern-row" data-regime="{escape(regime)}">
  <td class="chart-cell">{charts}</td>
  <td class="identity-cell">
    <code class="pattern-name">{escape(item.pattern)}</code>
    <div>{escape(item.chinese_name)}</div>
    <div class="attributes">{escape(regime)} · {escape(attribute_text)}</div>
  </td>
  <td>
    <div class="sequence">{escape(sequences)}</div>
    <p>{escape(item.logic)}</p>
    {threshold_html}
    {note_html}
  </td>
  <td class="parameter-cell">{parameter_html}</td>
</tr>"""
    return regime, row


def render_trend_pattern_reference(output_path: Path | None = None) -> Path:
    target = output_path or _DEFAULT_OUTPUT
    target.parent.mkdir(parents=True, exist_ok=True)
    params = _trend_pattern_params()

    grouped: dict[str, list[str]] = {
        "ranging": [],
        "trending": [],
        "transitioning": [],
        "irregular": [],
    }
    for item in PATTERN_EXAMPLES:
        regime, row = _render_row(item, params)
        grouped[regime].append(row)

    group_labels = {
        "ranging": "Ranging｜区间",
        "trending": "Trending｜趋势",
        "transitioning": "Transitioning｜转换",
        "irregular": "Irregular｜兜底",
    }
    body_rows = "".join(
        f'<tr class="group-row"><th colspan="4">{group_labels[regime]}</th></tr>'
        + "".join(grouped[regime])
        for regime in ("ranging", "trending", "transitioning", "irregular")
    )
    min_return = _format_threshold(params["min_abs_fitted_log_return"])
    min_r2 = _format_threshold(params["min_linearity_r2"])
    min_vol = _format_threshold(params["min_abs_vol_adjusted_trend"])
    range_ratio = _format_threshold(params["range_net_to_gross_max"])
    similarity_tolerance = float(params["swing_similarity_tolerance"])
    similarity_min = _format_threshold(1.0 - similarity_tolerance)
    similarity_tolerance_text = _format_threshold(similarity_tolerance)
    amplitude_change = float(params["swing_amplitude_change_min"])
    contracting_multiplier = _format_threshold(1.0 - amplitude_change)
    expanding_multiplier = _format_threshold(1.0 + amplitude_change)
    amplitude_change_text = _format_threshold(amplitude_change)
    confirmation_ratio = _format_threshold(params["double_test_confirmation_ratio"])

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trend pattern 识别规则参考</title>
<style>
:root {{
  color-scheme: light dark;
  --background: #f8fafc;
  --foreground: #172033;
  --muted: #e8edf4;
  --muted-foreground: #5d687a;
  --border: #cbd4e1;
  --surface: #ffffff;
  --up: #16835b;
  --down: #cf4b49;
  --flat: #7a8494;
  --fit: #375dfb;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --background: #111620;
    --foreground: #e9eef7;
    --muted: #252d3a;
    --muted-foreground: #aab4c5;
    --border: #394456;
    --surface: #171d28;
    --up: #42c892;
    --down: #f07875;
    --flat: #a4adbc;
    --fit: #8aa2ff;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background: var(--background);
  color: var(--foreground);
  font-family: Inter, "Microsoft YaHei", "PingFang SC", system-ui, sans-serif;
  line-height: 1.5;
}}
main {{ max-width: 1500px; margin: 0 auto; padding: 28px 22px 48px; }}
h1 {{ margin: 0 0 8px; font-size: clamp(24px, 3vw, 36px); font-weight: 500; }}
h2 {{ margin: 28px 0 10px; font-size: 18px; font-weight: 500; }}
p {{ margin: 5px 0; }}
.lede {{ color: var(--muted-foreground); max-width: 1000px; }}
.formula-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(270px, 1fr));
  gap: 10px 20px;
  margin: 14px 0 22px;
}}
.formula-grid div {{ border-top: 1px solid var(--border); padding-top: 8px; }}
code {{
  background: var(--muted);
  color: var(--foreground);
  border-radius: 4px;
  padding: 1px 5px;
  overflow-wrap: anywhere;
}}
.table-wrap {{ overflow-x: auto; border-top: 1px solid var(--border); }}
table {{ width: 100%; min-width: 1040px; border-collapse: collapse; background: var(--surface); }}
th, td {{ border-bottom: 1px solid var(--border); padding: 12px; text-align: left; vertical-align: top; }}
thead th {{ background: var(--muted); font-weight: 500; }}
.group-row th {{ background: var(--muted); font-weight: 500; letter-spacing: .02em; }}
.chart-cell {{ width: 302px; }}
.identity-cell {{ width: 250px; }}
.parameter-cell {{ width: 230px; }}
.parameter-item {{ margin-bottom: 7px; }}
.parameter-value {{ display: block; margin-top: 2px; font-variant-numeric: tabular-nums; }}
.pattern-name {{ display: inline-block; margin-bottom: 5px; font-weight: 500; }}
.attributes, .note, .muted {{ color: var(--muted-foreground); font-size: 13px; }}
.sequence {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-weight: 500; }}
.thresholds {{ margin-top: 9px; padding-top: 7px; border-top: 1px dashed var(--border); }}
.thresholds div {{ margin-top: 3px; }}
.threshold-title {{ color: var(--muted-foreground); font-size: 13px; font-weight: 500; }}
.chart-cell .mini-chart + .mini-chart {{ margin-top: 7px; }}
.mini-chart {{ display: block; width: 280px; height: 126px; background: var(--surface); }}
.gridline {{ stroke: var(--border); stroke-width: 1; }}
.wick {{ stroke-width: 1; }}
.wick.up {{ stroke: var(--up); }}
.wick.down {{ stroke: var(--down); }}
.candle.up {{ fill: var(--up); }}
.candle.down {{ fill: var(--down); }}
.fit {{ stroke-width: 2.2; fill: none; }}
.fit.up {{ stroke: var(--up); }}
.fit.down {{ stroke: var(--down); }}
.fit.flat {{ stroke: var(--flat); }}
.breakpoint {{ stroke: var(--border); stroke-width: 1; stroke-dasharray: 3 3; }}
.leg-label, .return-label, .unreachable-label {{
  fill: var(--muted-foreground);
  font-size: 9px;
  text-anchor: middle;
}}
.leg-label {{ font-weight: 500; }}
.unreachable {{ stroke: var(--flat); fill: none; stroke-width: 2; stroke-dasharray: 5 4; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 14px; margin: 10px 0 18px; color: var(--muted-foreground); }}
.legend span::before {{ content: ""; display: inline-block; width: 18px; height: 3px; margin-right: 6px; vertical-align: middle; }}
.legend .up::before {{ background: var(--up); }}
.legend .down::before {{ background: var(--down); }}
.legend .flat::before {{ background: var(--flat); }}
.warning {{ border-left: 3px solid var(--flat); padding-left: 10px; color: var(--muted-foreground); }}
@media (max-width: 700px) {{
  main {{ padding: 18px 12px 36px; }}
  .formula-grid {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<main>
  <h1>Trend pattern 识别规则参考</h1>
  <p class="lede">基于 <code>trend_pattern_v3</code> 当前规则，阈值直接读取 <code>config/settings.yaml</code>。每幅模拟图由固定的分段 fitted log return 生成，并已通过使用同一套生产阈值的真实分类函数验证；它只展示一种典型触发路径，不代表该 pattern 的全部真实市场形态。</p>

  <h2>共同计算口径</h2>
  <div class="formula-grid">
    <div><code>方向段</code><br><strong>abs(fitted_log_return) &gt;= {min_return}</strong>，并且 <strong>linearity_r2 &gt;= {min_r2}</strong> 或 <strong>abs(vol_adjusted_trend) &gt;= {min_vol}</strong>；否则为 flat。</div>
    <div><code>有效 legs</code><br>从原始方向序列中去除 flat，并合并相邻同方向分段。</div>
    <div><code>Q = net / gross</code><br>abs(sum(fitted returns)) / sum(abs(fitted returns))；至少 3 legs 且 <strong>Q &lt;= {range_ratio}</strong> 时进入 range 分支。</div>
    <div><code>摆动相似度</code><br>relative_difference &lt;= <strong>{similarity_tolerance_text}</strong>，等价于 similarity &gt;= <strong>{similarity_min}</strong>。</div>
    <div><code>振幅变化</code><br>最小变化比例 <strong>{amplitude_change_text}</strong>：收敛要求 current &lt;= previous × <strong>{contracting_multiplier}</strong>；扩张要求 current &gt;= previous × <strong>{expanding_multiplier}</strong>。</div>
    <div><code>Double test 确认</code><br>confirmation &gt;= max(reference amplitudes) × <strong>{confirmation_ratio}</strong>，并且摆动相似度同时达标。</div>
    <div><code>规则优先级</code><br>无 leg → 单 leg → 双 leg → double test → range → 三 leg 趋势/复杂反转 → 多 leg。</div>
  </div>
  <div class="legend" aria-label="图例">
    <span class="up">up / 上涨</span><span class="down">down / 下跌</span><span class="flat">flat / 横向</span>
  </div>
  <p class="warning">蜡烛是帮助理解拟合路径的确定性模拟数据。分类器实际使用分段指标，而不是直接根据蜡烛外观命名。</p>

  <h2>Pattern 总表</h2>
  <div class="table-wrap">
    <table>
      <thead><tr><th>模拟 K 线与拟合 leg</th><th>Pattern 与分层属性</th><th>有效序列与识别条件</th><th>相关参数</th></tr></thead>
      <tbody>{body_rows}</tbody>
    </table>
  </div>
</main>
</body>
</html>
"""
    target.write_text(html, encoding="utf-8")
    return target


if __name__ == "__main__":
    render_trend_pattern_reference()
