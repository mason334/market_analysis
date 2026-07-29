from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class EffectiveLeg:
    """One merged directional leg derived from persisted adaptive segments."""

    start_price_index: int
    end_price_index: int
    fitted_log_return: float
    source_segment_indices: tuple[int, ...]

    @property
    def direction(self) -> str:
        return "up" if self.fitted_log_return > 0.0 else "down"


def _finite_or_zero(value: Any) -> float:
    number = 0.0 if value is None else float(value)
    if not isfinite(number):
        raise ValueError("Effective-leg segment metrics must be finite when present.")
    return number


def _validate_thresholds(
    min_abs_fitted_log_return: float,
    min_linearity_r2: float,
    min_abs_vol_adjusted_trend: float,
) -> tuple[float, float, float]:
    min_return = float(min_abs_fitted_log_return)
    min_r2 = float(min_linearity_r2)
    min_vol_adjusted = float(min_abs_vol_adjusted_trend)
    if (
        not isfinite(min_return)
        or min_return <= 0.0
        or not isfinite(min_r2)
        or not 0.0 <= min_r2 <= 1.0
        or not isfinite(min_vol_adjusted)
        or min_vol_adjusted <= 0.0
    ):
        raise ValueError("Effective-leg thresholds are outside their valid ranges.")
    return min_return, min_r2, min_vol_adjusted


def extract_effective_legs(
    segments: Iterable[dict[str, Any]],
    *,
    min_abs_fitted_log_return: float = 0.02,
    min_linearity_r2: float = 0.35,
    min_abs_vol_adjusted_trend: float = 0.75,
) -> tuple[EffectiveLeg, ...]:
    """Filter flat segments and merge consecutive effective directions."""
    min_return, min_r2, min_vol_adjusted = _validate_thresholds(
        min_abs_fitted_log_return,
        min_linearity_r2,
        min_abs_vol_adjusted_trend,
    )
    ordered = sorted(segments, key=lambda row: int(row["segment_index"]))
    if [int(row["segment_index"]) for row in ordered] != list(range(len(ordered))):
        raise ValueError("Adaptive segments must be complete and consecutively indexed.")

    legs: list[EffectiveLeg] = []
    for segment in ordered:
        segment_index = int(segment["segment_index"])
        start_bar_index = int(segment["start_bar_index"])
        end_bar_index = int(segment["end_bar_index"])
        if start_bar_index < 0 or end_bar_index < start_bar_index:
            raise ValueError("Adaptive segment bar indexes are invalid.")

        fitted_return = _finite_or_zero(segment.get("fitted_log_return"))
        linearity_r2 = _finite_or_zero(segment.get("linearity_r2"))
        vol_adjusted = _finite_or_zero(segment.get("vol_adjusted_trend"))
        is_directional = abs(fitted_return) >= min_return and (
            linearity_r2 >= min_r2 or abs(vol_adjusted) >= min_vol_adjusted
        )
        if not is_directional:
            continue

        start_price_index = 0 if start_bar_index == 0 else start_bar_index - 1
        if legs and legs[-1].fitted_log_return * fitted_return > 0.0:
            previous = legs[-1]
            legs[-1] = EffectiveLeg(
                start_price_index=previous.start_price_index,
                end_price_index=end_bar_index,
                fitted_log_return=previous.fitted_log_return + fitted_return,
                source_segment_indices=(*previous.source_segment_indices, segment_index),
            )
        else:
            legs.append(
                EffectiveLeg(
                    start_price_index=start_price_index,
                    end_price_index=end_bar_index,
                    fitted_log_return=fitted_return,
                    source_segment_indices=(segment_index,),
                )
            )
    return tuple(legs)
