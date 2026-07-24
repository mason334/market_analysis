from __future__ import annotations

from market_analysis.config import settings
from market_analysis.pipeline.render_trend_pattern_reference import (
    PATTERN_EXAMPLES,
    classify_example,
    render_trend_pattern_reference,
)

EXPECTED_PATTERNS = {
    "range_bound",
    "contracting_range",
    "expanding_range",
    "uptrend",
    "downtrend",
    "uptrend_then_sideways",
    "downtrend_then_sideways",
    "uptrend_resumption",
    "uptrend_with_pullback",
    "downtrend_resumption",
    "downtrend_with_rebound",
    "uptrend_with_multiple_pullbacks",
    "downtrend_with_multiple_rebounds",
    "bottom_reversal",
    "top_reversal",
    "bottom_reversal_then_sideways",
    "top_reversal_then_sideways",
    "double_bottom_like",
    "double_top_like",
    "complex_bottom_reversal",
    "complex_top_reversal",
    "irregular_path",
}


def test_reference_catalog_covers_every_pattern_name_once() -> None:
    names = [item.pattern for item in PATTERN_EXAMPLES]

    assert len(names) == len(set(names))
    assert set(names) == EXPECTED_PATTERNS


def test_every_reachable_synthetic_example_uses_real_classifier() -> None:
    for item in PATTERN_EXAMPLES:
        for returns in item.examples:
            assert classify_example(returns)["pattern"] == item.pattern


def test_irregular_path_is_documented_as_unreachable_fallback() -> None:
    irregular = next(item for item in PATTERN_EXAMPLES if item.pattern == "irregular_path")

    assert irregular.examples == ()
    assert "兜底" in irregular.chinese_name


def test_render_reference_writes_self_contained_html(tmp_path) -> None:
    output = render_trend_pattern_reference(tmp_path / "trend-pattern-reference.html")
    html = output.read_text(encoding="utf-8")
    params = settings.indicators["trend_pattern"]
    min_return = float(params["min_abs_fitted_log_return"])
    min_r2 = float(params["min_linearity_r2"])
    min_vol = float(params["min_abs_vol_adjusted_trend"])
    range_ratio = float(params["range_net_to_gross_max"])
    similarity_tolerance = float(params["swing_similarity_tolerance"])
    amplitude_change = float(params["swing_amplitude_change_min"])
    confirmation_ratio = float(params["double_test_confirmation_ratio"])

    assert "<!doctype html>" in html
    assert "<svg" in html
    assert "http://" not in html
    assert "https://" not in html
    assert f"abs(fitted_log_return) &gt;= {min_return:g}" in html
    assert f"linearity_r2 &gt;= {min_r2:g}" in html
    assert f"abs(vol_adjusted_trend) &gt;= {min_vol:g}" in html
    assert f"Q &lt;= {range_ratio:g}" in html
    assert f"relative_difference &lt;= {similarity_tolerance:g}" in html
    assert f"previous × {1.0 - amplitude_change:g}" in html
    assert f"previous × {1.0 + amplitude_change:g}" in html
    assert (
        f"max(reference amplitudes) × <strong>{confirmation_ratio:g}</strong>"
        in html
    )
    assert "<code>range_net_to_gross_max</code>" in html
    assert f'<span class="parameter-value">= {range_ratio:g}</span>' in html
    for pattern in EXPECTED_PATTERNS:
        assert pattern in html
