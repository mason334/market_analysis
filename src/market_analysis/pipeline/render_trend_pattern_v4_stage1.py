# ruff: noqa: E501

from __future__ import annotations

from collections import Counter
from html import escape
from pathlib import Path
from typing import Any

from market_analysis.config import settings
from market_analysis.indicators.trend_pattern_v4_structure import (
    DIRECTION_NEUTRAL_STRUCTURES,
    PivotRelation,
    Stage1Structure,
    StructureTemplate,
    classify_effective_leg_structure,
    example_returns_for_template,
)

_PROJECT_ROOT = Path(__file__).parents[3]
_DEFAULT_OUTPUT = _PROJECT_ROOT / "artifacts" / "trend_pattern_v4_stage1.html"

_RELATION_CN = {
    PivotRelation.SHORT_OF: "未到前一同类 pivot",
    PivotRelation.RETEST: "回到前一同类 pivot 容差带",
    PivotRelation.BREAK: "越过前一同类 pivot 容差带",
}


def _params() -> dict[str, Any]:
    return dict(settings.indicators.get("trend_pattern_v4", {}))


def _format_values(values: tuple[float, ...]) -> str:
    return " → ".join(f"{value:+.4f}" for value in values)


def _format_pivots(values: tuple[float, ...]) -> str:
    return " → ".join(f"P{index}={value:+.4f}" for index, value in enumerate(values))


def _relation_sequence(values: tuple[PivotRelation, ...]) -> str:
    return " → ".join(value.value for value in values) if values else "none"


def _chart_svg(result: Stage1Structure) -> str:
    pivots = result.normalized_pivots
    width, height = 330.0, 164.0
    left, right, top, bottom = 31.0, 18.0, 24.0, 34.0
    plot_width = width - left - right
    plot_height = height - top - bottom
    low, high = min(pivots), max(pivots)
    padding = max((high - low) * 0.18, 0.015)
    low -= padding
    high += padding
    span = max(high - low, 1e-9)

    def x(index: int) -> float:
        return left + plot_width * index / max(len(pivots) - 1, 1)

    def y(value: float) -> float:
        return top + plot_height * (high - value) / span

    zero_y = y(0.0)
    marks = [
        f'<line class="zero-line" x1="{left:.1f}" y1="{zero_y:.1f}" '
        f'x2="{width - right:.1f}" y2="{zero_y:.1f}"/>'
    ]
    for index, value in enumerate(result.normalized_leg_returns):
        state = "up" if value > 0.0 else "down"
        marks.append(
            f'<line class="path {state}" x1="{x(index):.1f}" y1="{y(pivots[index]):.1f}" '
            f'x2="{x(index + 1):.1f}" y2="{y(pivots[index + 1]):.1f}"/>'
        )

    for measurement in result.relation_measurements:
        relation = measurement.relation
        reference = measurement.reference_pivot_index
        current = measurement.current_pivot_index
        middle_x = (x(reference) + x(current)) / 2
        middle_y = (y(pivots[reference]) + y(pivots[current])) / 2 - 6
        marks.append(
            f'<line class="comparison {relation.value}" '
            f'x1="{x(reference):.1f}" y1="{y(pivots[reference]):.1f}" '
            f'x2="{x(current):.1f}" y2="{y(pivots[current]):.1f}"/>'
        )
        marks.append(
            f'<text class="relation-label" x="{middle_x:.1f}" y="{middle_y:.1f}">'
            f"{relation.code}</text>"
        )

    for index, pivot in enumerate(pivots):
        marks.append(
            f'<circle class="pivot" cx="{x(index):.1f}" cy="{y(pivot):.1f}" r="3.4"/>'
        )
        label_y = y(pivot) - 8 if index % 2 else y(pivot) + 16
        marks.append(
            f'<text class="pivot-label" x="{x(index):.1f}" y="{label_y:.1f}">'
            f"P{index} {pivot:+.3f}</text>"
        )

    description = (
        f"{result.structure_code}，标准化腿序列 {_format_values(result.normalized_leg_returns)}，"
        f"pivot 关系 {_relation_sequence(result.pivot_relation_sequence)}"
    )
    return (
        f'<svg class="structure-chart" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'role="img" aria-label="{escape(description)}">'
        f"<title>{escape(description)}</title>"
        + "".join(marks)
        + "</svg>"
    )


def _relation_evidence(result: Stage1Structure) -> str:
    if not result.relation_measurements:
        return '<span class="muted">首腿没有可比较的同类 pivot。</span>'
    rows: list[str] = []
    for item in result.relation_measurements:
        comparator = "≤" if item.relation == PivotRelation.RETEST else ">"
        relation_text = _RELATION_CN[item.relation]
        rows.append(
            '<div class="evidence-line">'
            f"<code>P{item.current_pivot_index} vs P{item.reference_pivot_index}</code>"
            f"<span>A{item.current_leg_number}/A{item.current_leg_number - 1}"
            f" = {item.amplitude_ratio:.3f}</span>"
            f"<span>relative difference = {item.relative_difference:.3f} "
            f"{comparator} {result.pivot_retest_tolerance:.3f}</span>"
            f'<strong class="relation-{item.relation.value}">{item.relation.value}</strong>'
            f'<span class="muted">{relation_text}</span>'
            "</div>"
        )
    return "".join(rows)


def _row(template: StructureTemplate, tolerance: float) -> str:
    example = example_returns_for_template(template, tolerance)
    result = classify_effective_leg_structure(example, tolerance)
    if result.structure_code != template.structure_code:
        raise ValueError(
            f"Example for {template.structure_code} classified as {result.structure_code}."
        )
    relations = _relation_sequence(result.pivot_relation_sequence)
    return f"""
<tr class="structure-row" data-leg-count="{template.effective_leg_count}"
    data-structure-code="{escape(template.structure_code)}">
  <td class="identity">
    <span class="structure-index">{template.structure_index:02d}</span>
    <code class="structure-code">{escape(template.structure_code)}</code>
  </td>
  <td>
    <div class="sequence">{escape(template.normalized_direction_sequence)}</div>
    <div class="muted">首腿统一镜像为 up</div>
  </td>
  <td>
    <div class="sequence">{escape(relations)}</div>
    {_relation_evidence(result)}
  </td>
  <td class="numeric">
    <div><strong>legs</strong> {_format_values(result.normalized_leg_returns)}</div>
    <div class="pivots">{_format_pivots(result.normalized_pivots)}</div>
  </td>
  <td class="chart-cell">{_chart_svg(result)}</td>
</tr>"""


def render_trend_pattern_v4_stage1(output_path: Path | None = None) -> Path:
    """Render the verified 40-cell stage-one table as a self-contained HTML page."""
    target = output_path or _DEFAULT_OUTPUT
    target.parent.mkdir(parents=True, exist_ok=True)
    params = _params()
    tolerance = float(params.get("pivot_retest_tolerance", 0.25))
    lower_ratio = 1.0 - tolerance
    upper_ratio = 1.0 / lower_ratio

    counts = Counter(
        template.effective_leg_count for template in DIRECTION_NEUTRAL_STRUCTURES
    )
    expected_counts = {1: 1, 2: 3, 3: 9, 4: 27}
    if counts != expected_counts or len(DIRECTION_NEUTRAL_STRUCTURES) != 40:
        raise ValueError("The v4 stage-one structure table is not complete.")

    grouped_rows: list[str] = []
    for leg_count in range(1, 5):
        grouped_rows.append(
            f'<tr class="group-row" data-leg-group="{leg_count}">'
            f'<th colspan="5">{leg_count} effective leg'
            f'{"s" if leg_count > 1 else ""} · {counts[leg_count]} 个方向无关组合</th></tr>'
        )
        grouped_rows.extend(
            _row(template, tolerance)
            for template in DIRECTION_NEUTRAL_STRUCTURES
            if template.effective_leg_count == leg_count
        )

    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trend pattern v4 · 第一阶段结构组合表</title>
<style>
:root {{
  color-scheme: light dark;
  --background: #f5f7fb;
  --foreground: #172033;
  --surface: #ffffff;
  --surface-alt: #edf1f7;
  --muted: #647084;
  --border: #cbd4e1;
  --up: #167c59;
  --down: #c44f4d;
  --short: #b16d14;
  --retest: #3669c9;
  --break: #8b4dbc;
  --ring: #3158d3;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --background: #10151e;
    --foreground: #e8edf6;
    --surface: #171e29;
    --surface-alt: #222b39;
    --muted: #a6b0c0;
    --border: #3a4658;
    --up: #45c595;
    --down: #ef7976;
    --short: #e5a44d;
    --retest: #7ea5f5;
    --break: #c795eb;
    --ring: #8aa6ff;
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
main {{ max-width: 1680px; margin: 0 auto; padding: 30px 22px 54px; }}
h1 {{ margin: 0 0 8px; font-size: clamp(25px, 3vw, 38px); font-weight: 500; }}
h2 {{ margin: 30px 0 10px; font-size: 19px; font-weight: 500; }}
p {{ margin: 6px 0; }}
.lede {{ max-width: 1060px; color: var(--muted); }}
.summary {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 1px;
  margin: 20px 0;
  background: var(--border);
  border: 1px solid var(--border);
}}
.summary > div {{ background: var(--surface); padding: 14px 16px; }}
.summary strong {{ display: block; font-size: 24px; font-weight: 500; }}
.summary span {{ color: var(--muted); }}
.logic {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px 24px;
  margin: 16px 0;
}}
.logic > div {{ border-top: 1px solid var(--border); padding-top: 10px; }}
code {{
  padding: 2px 5px;
  border-radius: 4px;
  background: var(--surface-alt);
  color: var(--foreground);
  overflow-wrap: anywhere;
}}
.controls {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 14px 0;
}}
label {{ color: var(--muted); }}
select {{
  min-height: 38px;
  padding: 6px 34px 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--foreground);
}}
select:focus {{ outline: 2px solid var(--ring); outline-offset: 2px; }}
.legend {{ display: flex; flex-wrap: wrap; gap: 15px; color: var(--muted); }}
.legend span::before {{
  content: "";
  display: inline-block;
  width: 18px;
  height: 3px;
  margin-right: 6px;
  vertical-align: middle;
  background: currentColor;
}}
.legend .short {{ color: var(--short); }}
.legend .retest {{ color: var(--retest); }}
.legend .break {{ color: var(--break); }}
.table-wrap {{ overflow-x: auto; border-top: 1px solid var(--border); }}
table {{ width: 100%; min-width: 1310px; border-collapse: collapse; background: var(--surface); }}
th, td {{
  padding: 12px;
  border-bottom: 1px solid var(--border);
  text-align: left;
  vertical-align: top;
}}
thead th {{ background: var(--surface-alt); font-weight: 500; }}
.group-row th {{ background: var(--surface-alt); font-weight: 500; }}
.identity {{ width: 130px; white-space: nowrap; }}
.structure-index {{ display: inline-block; width: 28px; color: var(--muted); }}
.structure-code {{ font-weight: 500; }}
.sequence {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-weight: 500; }}
.muted, .pivots {{ color: var(--muted); font-size: 13px; }}
.numeric {{ min-width: 300px; font-variant-numeric: tabular-nums; }}
.pivots {{ margin-top: 7px; }}
.evidence-line {{
  display: grid;
  grid-template-columns: minmax(92px, auto) minmax(118px, auto) minmax(210px, 1fr);
  gap: 3px 10px;
  align-items: baseline;
  margin-top: 7px;
}}
.evidence-line strong {{ grid-column: 1; font-weight: 500; }}
.evidence-line .muted {{ grid-column: 2 / -1; }}
.relation-short_of {{ color: var(--short); }}
.relation-retest {{ color: var(--retest); }}
.relation-break {{ color: var(--break); }}
.chart-cell {{ width: 354px; }}
.structure-chart {{ display: block; width: 330px; height: 164px; }}
.zero-line {{ stroke: var(--border); stroke-width: 1; }}
.path {{ stroke-width: 3; stroke-linecap: round; }}
.path.up {{ stroke: var(--up); }}
.path.down {{ stroke: var(--down); }}
.comparison {{ fill: none; stroke-width: 1.3; stroke-dasharray: 4 4; }}
.comparison.short_of {{ stroke: var(--short); }}
.comparison.retest {{ stroke: var(--retest); }}
.comparison.break {{ stroke: var(--break); }}
.pivot {{ fill: var(--surface); stroke: var(--foreground); stroke-width: 1.5; }}
.pivot-label, .relation-label {{
  fill: var(--muted);
  font-size: 9px;
  text-anchor: middle;
}}
.relation-label {{ fill: var(--foreground); font-weight: 500; }}
.hidden {{ display: none; }}
@media (max-width: 720px) {{
  main {{ padding: 20px 12px 40px; }}
  .summary {{ grid-template-columns: 1fr 1fr; }}
  .logic {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>
<main>
  <h1>Trend pattern v4 · 第一阶段结构组合表</h1>
  <p class="lede">本页只展示方向无关的结构分类，不使用 double bottom、reversal、range
  等人类 pattern 名称。每个合法的 1～4 effective-leg 路径恰好落入一个结构单元；向下起始路径
  先整体镜像，使用同一张表。</p>

  <div class="summary" aria-label="组合数量">
    <div><strong>40</strong><span>方向无关结构总数</span></div>
    <div><strong>1 + 3 + 9 + 27</strong><span>1～4 effective legs</span></div>
    <div><strong>3</strong><span>short_of / retest / break</span></div>
    <div><strong>{tolerance:.2f}</strong><span>pivot retest tolerance</span></div>
  </div>

  <h2>分类依据</h2>
  <div class="logic">
    <div><code>方向归一化</code><br>如果首腿向下，将全部 signed leg return 乘以 -1；
    所有模板因此统一从 up 开始。</div>
    <div><code>同类 pivot 比较</code><br>第 i 条腿完成后比较 Pᵢ 与 Pᵢ₋₂；
    等价于比较当前腿与前一腿的绝对振幅。</div>
    <div><code>relative difference</code><br>|Aᵢ − Aᵢ₋₁| / max(Aᵢ, Aᵢ₋₁)。
    不超过 {tolerance:.2f} 为 retest。</div>
    <div><code>互斥区间</code><br>令 r = Aᵢ/Aᵢ₋₁：
    r &lt; {lower_ratio:.3f} 为 short_of；{lower_ratio:.3f} ≤ r ≤ {upper_ratio:.3f}
    为 retest；r &gt; {upper_ratio:.3f} 为 break。</div>
  </div>

  <div class="controls">
    <label for="leg-filter">显示 effective-leg 数量</label>
    <select id="leg-filter">
      <option value="all">全部 40 个</option>
      <option value="1">1 leg · 1 个</option>
      <option value="2">2 legs · 3 个</option>
      <option value="3">3 legs · 9 个</option>
      <option value="4">4 legs · 27 个</option>
    </select>
    <div class="legend" aria-label="pivot 关系图例">
      <span class="short">S · short_of</span>
      <span class="retest">R · retest</span>
      <span class="break">B · break</span>
    </div>
  </div>

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>编号</th>
          <th>标准方向骨架</th>
          <th>Pivot 关系与计算证据</th>
          <th>示例数值</th>
          <th>示例路径图</th>
        </tr>
      </thead>
      <tbody>
        {"".join(grouped_rows)}
      </tbody>
    </table>
  </div>
</main>
<script>
const filter = document.getElementById("leg-filter");
const applyFilter = () => {{
  const selected = filter.value;
  document.querySelectorAll(".structure-row").forEach((row) => {{
    row.classList.toggle("hidden", selected !== "all" && row.dataset.legCount !== selected);
  }});
  document.querySelectorAll(".group-row").forEach((row) => {{
    row.classList.toggle("hidden", selected !== "all" && row.dataset.legGroup !== selected);
  }});
}};
filter.addEventListener("change", applyFilter);
</script>
</body>
</html>
"""
    target.write_text(html, encoding="utf-8")
    return target


if __name__ == "__main__":
    render_trend_pattern_v4_stage1()
