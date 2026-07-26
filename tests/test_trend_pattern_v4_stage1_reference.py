from __future__ import annotations

from pathlib import Path

from market_analysis.pipeline.render_trend_pattern_v4_stage1 import (
    render_trend_pattern_v4_stage1,
)


def test_reference_page_contains_all_40_verified_structure_cells(
    tmp_path: Path,
) -> None:
    output = render_trend_pattern_v4_stage1(tmp_path / "v4-stage1.html")
    html = output.read_text(encoding="utf-8")

    assert html.count('class="structure-row"') == 40
    assert html.count('class="structure-chart"') == 40
    assert html.count('class="group-row"') == 4
    assert 'data-structure-code="L1"' in html
    assert 'data-structure-code="L2-S"' in html
    assert 'data-structure-code="L3-RB"' in html
    assert 'data-structure-code="L4-BBB"' in html
    assert "1 + 3 + 9 + 27" in html
    assert "0.750 ≤ r ≤ 1.333" in html
    assert "double bottom" in html
    assert "https://" not in html


def test_reference_page_filter_targets_every_structure_row(tmp_path: Path) -> None:
    output = render_trend_pattern_v4_stage1(tmp_path / "v4-stage1.html")
    html = output.read_text(encoding="utf-8")

    for leg_count, expected in ((1, 1), (2, 3), (3, 9), (4, 27)):
        assert html.count(f'data-leg-count="{leg_count}"') == expected
        assert html.count(f'data-leg-group="{leg_count}"') == 1
    assert 'filter.addEventListener("change", applyFilter)' in html
