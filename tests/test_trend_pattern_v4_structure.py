from __future__ import annotations

from collections import Counter

import pytest

from market_analysis.indicators.trend_pattern_v4_structure import (
    DIRECTION_NEUTRAL_STRUCTURES,
    PivotRelation,
    classify_effective_leg_structure,
    example_returns_for_template,
)


def test_direction_neutral_table_contains_exactly_40_unique_cells() -> None:
    counts = Counter(
        template.effective_leg_count for template in DIRECTION_NEUTRAL_STRUCTURES
    )

    assert counts == {1: 1, 2: 3, 3: 9, 4: 27}
    assert len(DIRECTION_NEUTRAL_STRUCTURES) == 40
    assert [item.structure_index for item in DIRECTION_NEUTRAL_STRUCTURES] == list(
        range(1, 41)
    )
    assert len({item.structure_code for item in DIRECTION_NEUTRAL_STRUCTURES}) == 40


def test_every_template_example_round_trips_through_the_real_classifier() -> None:
    for template in DIRECTION_NEUTRAL_STRUCTURES:
        example = example_returns_for_template(template)
        result = classify_effective_leg_structure(example)

        assert result.structure_index == template.structure_index
        assert result.structure_code == template.structure_code
        assert result.pivot_relation_sequence == template.pivot_relations


def test_direction_mirror_maps_to_the_same_structure_cell() -> None:
    upward = classify_effective_leg_structure((0.08, -0.048, 0.0768, -0.0768))
    downward = classify_effective_leg_structure((-0.08, 0.048, -0.0768, 0.0768))

    assert upward.structure_code == "L4-SBR"
    assert downward.structure_code == upward.structure_code
    assert upward.normalized_leg_returns == pytest.approx(
        downward.normalized_leg_returns
    )
    assert upward.start_direction == "up"
    assert downward.start_direction == "down"
    assert upward.direction_sequence == "up>down>up>down"
    assert downward.direction_sequence == "down>up>down>up"


def test_relative_difference_boundaries_are_mutually_exclusive() -> None:
    short_of = classify_effective_leg_structure((1.0, -0.74), 0.25)
    lower_boundary = classify_effective_leg_structure((1.0, -0.75), 0.25)
    equal = classify_effective_leg_structure((1.0, -1.0), 0.25)
    upper_boundary = classify_effective_leg_structure((0.75, -1.0), 0.25)
    break_result = classify_effective_leg_structure((0.74, -1.0), 0.25)

    assert short_of.pivot_relation_sequence == (PivotRelation.SHORT_OF,)
    assert lower_boundary.pivot_relation_sequence == (PivotRelation.RETEST,)
    assert equal.pivot_relation_sequence == (PivotRelation.RETEST,)
    assert upper_boundary.pivot_relation_sequence == (PivotRelation.RETEST,)
    assert break_result.pivot_relation_sequence == (PivotRelation.BREAK,)


def test_three_leg_example_is_classified_from_both_pivot_comparisons() -> None:
    result = classify_effective_leg_structure((0.08, -0.10, 0.12), 0.25)

    assert result.structure_code == "L3-RR"
    assert result.pivot_relation_sequence == (
        PivotRelation.RETEST,
        PivotRelation.RETEST,
    )
    assert result.normalized_pivots == pytest.approx((0.0, 0.08, -0.02, 0.10))
    assert [
        item.relative_difference for item in result.relation_measurements
    ] == pytest.approx([0.20, 1 / 6])


@pytest.mark.parametrize(
    "returns",
    [
        (),
        (0.0,),
        (0.1, 0.2),
        (0.1, -0.2, 0.3, -0.4, 0.5),
        (float("nan"),),
    ],
)
def test_stage_one_rejects_inputs_outside_its_defined_domain(
    returns: tuple[float, ...],
) -> None:
    with pytest.raises(ValueError):
        classify_effective_leg_structure(returns)


@pytest.mark.parametrize("tolerance", [-0.01, 1.0, float("inf")])
def test_stage_one_rejects_invalid_retest_tolerance(tolerance: float) -> None:
    with pytest.raises(ValueError, match="pivot_retest_tolerance"):
        classify_effective_leg_structure((0.1,), tolerance)
