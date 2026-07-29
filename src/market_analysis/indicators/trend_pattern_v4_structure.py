from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import product
from math import isfinite
from typing import Any

_EPSILON = 1e-12
_MAX_EFFECTIVE_LEGS = 4


class PivotRelation(StrEnum):
    """Direction-neutral relation between consecutive same-type fitted pivots."""

    SHORT_OF = "short_of"
    RETEST = "retest"
    BREAK = "break"

    @property
    def code(self) -> str:
        return {
            PivotRelation.SHORT_OF: "S",
            PivotRelation.RETEST: "R",
            PivotRelation.BREAK: "B",
        }[self]


_RELATION_ORDER = (
    PivotRelation.SHORT_OF,
    PivotRelation.RETEST,
    PivotRelation.BREAK,
)


@dataclass(frozen=True)
class StructureTemplate:
    """One cell in the 1-4 effective-leg direction-neutral structure table."""

    structure_index: int
    structure_code: str
    effective_leg_count: int
    normalized_direction_sequence: str
    pivot_relations: tuple[PivotRelation, ...]


@dataclass(frozen=True)
class PivotRelationMeasurement:
    """Numeric evidence for one short_of/retest/break decision."""

    current_leg_number: int
    reference_pivot_index: int
    current_pivot_index: int
    previous_amplitude: float
    current_amplitude: float
    amplitude_ratio: float
    relative_difference: float
    relation: PivotRelation


@dataclass(frozen=True)
class Stage1Structure:
    """Stage-one v4 result before any human pattern name is assigned."""

    structure_index: int
    structure_code: str
    effective_leg_count: int
    start_direction: str
    direction_sequence: str
    normalized_direction_sequence: str
    pivot_relation_sequence: tuple[PivotRelation, ...]
    effective_leg_returns: tuple[float, ...]
    normalized_leg_returns: tuple[float, ...]
    normalized_pivots: tuple[float, ...]
    relation_measurements: tuple[PivotRelationMeasurement, ...]
    pivot_retest_tolerance: float

    def to_dict(self) -> dict[str, Any]:
        """Return a serialization-friendly representation for later pipelines."""
        return {
            "structure_index": self.structure_index,
            "structure_code": self.structure_code,
            "effective_leg_count": self.effective_leg_count,
            "start_direction": self.start_direction,
            "direction_sequence": self.direction_sequence,
            "normalized_direction_sequence": self.normalized_direction_sequence,
            "pivot_relation_sequence": [
                relation.value for relation in self.pivot_relation_sequence
            ],
            "effective_leg_returns": list(self.effective_leg_returns),
            "normalized_leg_returns": list(self.normalized_leg_returns),
            "normalized_pivots": list(self.normalized_pivots),
            "relation_measurements": [
                {
                    "current_leg_number": item.current_leg_number,
                    "reference_pivot_index": item.reference_pivot_index,
                    "current_pivot_index": item.current_pivot_index,
                    "previous_amplitude": item.previous_amplitude,
                    "current_amplitude": item.current_amplitude,
                    "amplitude_ratio": item.amplitude_ratio,
                    "relative_difference": item.relative_difference,
                    "relation": item.relation.value,
                }
                for item in self.relation_measurements
            ],
            "pivot_retest_tolerance": self.pivot_retest_tolerance,
        }


def _direction_sequence(leg_count: int, first_direction: str = "up") -> str:
    if first_direction not in {"up", "down"}:
        raise ValueError("first_direction must be 'up' or 'down'.")
    opposite = "down" if first_direction == "up" else "up"
    return ">".join(
        first_direction if index % 2 == 0 else opposite for index in range(leg_count)
    )


def _structure_code(
    leg_count: int,
    relations: Sequence[PivotRelation],
) -> str:
    suffix = "".join(relation.code for relation in relations)
    return f"L{leg_count}" if not suffix else f"L{leg_count}-{suffix}"


def enumerate_direction_neutral_structures() -> tuple[StructureTemplate, ...]:
    """Enumerate the complete 1 + 3 + 9 + 27 direction-neutral table."""
    templates: list[StructureTemplate] = []
    structure_index = 1
    for leg_count in range(1, _MAX_EFFECTIVE_LEGS + 1):
        for relations in product(_RELATION_ORDER, repeat=leg_count - 1):
            templates.append(
                StructureTemplate(
                    structure_index=structure_index,
                    structure_code=_structure_code(leg_count, relations),
                    effective_leg_count=leg_count,
                    normalized_direction_sequence=_direction_sequence(leg_count),
                    pivot_relations=relations,
                )
            )
            structure_index += 1
    return tuple(templates)


DIRECTION_NEUTRAL_STRUCTURES = enumerate_direction_neutral_structures()
_TEMPLATE_BY_KEY = {
    (item.effective_leg_count, item.pivot_relations): item
    for item in DIRECTION_NEUTRAL_STRUCTURES
}


def _validate_tolerance(pivot_retest_tolerance: float) -> float:
    tolerance = float(pivot_retest_tolerance)
    if not isfinite(tolerance) or not 0.0 <= tolerance < 1.0:
        raise ValueError("pivot_retest_tolerance must be finite and in [0, 1).")
    return tolerance


def _validate_effective_leg_returns(
    effective_leg_returns: Sequence[float],
) -> tuple[float, ...]:
    values = tuple(float(value) for value in effective_leg_returns)
    if not 1 <= len(values) <= _MAX_EFFECTIVE_LEGS:
        raise ValueError("Stage one requires between 1 and 4 effective legs.")
    if any(not isfinite(value) or abs(value) <= _EPSILON for value in values):
        raise ValueError("Effective-leg returns must be finite and non-zero.")
    if any(left * right >= 0.0 for left, right in zip(values, values[1:])):
        raise ValueError("Effective-leg directions must alternate after merging.")
    return values


def _pivots(returns: Sequence[float]) -> tuple[float, ...]:
    points = [0.0]
    for value in returns:
        points.append(points[-1] + float(value))
    return tuple(points)


def _relation_measurements(
    normalized_returns: Sequence[float],
    pivot_retest_tolerance: float,
) -> tuple[PivotRelationMeasurement, ...]:
    amplitudes = [abs(value) for value in normalized_returns]
    measurements: list[PivotRelationMeasurement] = []
    for current_index in range(1, len(amplitudes)):
        previous = amplitudes[current_index - 1]
        current = amplitudes[current_index]
        scale = max(previous, current, _EPSILON)
        relative_difference = abs(current - previous) / scale
        if relative_difference <= pivot_retest_tolerance:
            relation = PivotRelation.RETEST
        elif current < previous:
            relation = PivotRelation.SHORT_OF
        else:
            relation = PivotRelation.BREAK
        measurements.append(
            PivotRelationMeasurement(
                current_leg_number=current_index + 1,
                reference_pivot_index=current_index - 1,
                current_pivot_index=current_index + 1,
                previous_amplitude=previous,
                current_amplitude=current,
                amplitude_ratio=current / previous,
                relative_difference=relative_difference,
                relation=relation,
            )
        )
    return tuple(measurements)


def classify_effective_leg_structure(
    effective_leg_returns: Sequence[float],
    pivot_retest_tolerance: float = 0.25,
) -> Stage1Structure:
    """Map 1-4 alternating effective legs to exactly one direction-neutral cell."""
    values = _validate_effective_leg_returns(effective_leg_returns)
    tolerance = _validate_tolerance(pivot_retest_tolerance)
    start_direction = "up" if values[0] > 0.0 else "down"
    orientation = 1.0 if start_direction == "up" else -1.0
    normalized_returns = tuple(value * orientation for value in values)
    measurements = _relation_measurements(normalized_returns, tolerance)
    relations = tuple(item.relation for item in measurements)
    template = _TEMPLATE_BY_KEY[(len(values), relations)]
    return Stage1Structure(
        structure_index=template.structure_index,
        structure_code=template.structure_code,
        effective_leg_count=len(values),
        start_direction=start_direction,
        direction_sequence=_direction_sequence(len(values), start_direction),
        normalized_direction_sequence=template.normalized_direction_sequence,
        pivot_relation_sequence=relations,
        effective_leg_returns=values,
        normalized_leg_returns=normalized_returns,
        normalized_pivots=_pivots(normalized_returns),
        relation_measurements=measurements,
        pivot_retest_tolerance=tolerance,
    )


def example_returns_for_template(
    template: StructureTemplate,
    pivot_retest_tolerance: float = 0.25,
    first_amplitude: float = 0.08,
) -> tuple[float, ...]:
    """Build a deterministic example guaranteed to round-trip to a template."""
    tolerance = _validate_tolerance(pivot_retest_tolerance)
    amplitude = float(first_amplitude)
    if not isfinite(amplitude) or amplitude <= 0.0:
        raise ValueError("first_amplitude must be finite and positive.")

    short_factor = max((1.0 - tolerance) * 0.8, _EPSILON)
    break_factor = 1.2 / max(1.0 - tolerance, _EPSILON)
    factor_by_relation = {
        PivotRelation.SHORT_OF: short_factor,
        PivotRelation.RETEST: 1.0,
        PivotRelation.BREAK: break_factor,
    }
    amplitudes = [amplitude]
    for relation in template.pivot_relations:
        amplitudes.append(amplitudes[-1] * factor_by_relation[relation])
    return tuple(
        value if index % 2 == 0 else -value
        for index, value in enumerate(amplitudes)
    )
