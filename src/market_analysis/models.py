from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class IndicatorSnapshot:
    symbol: str
    date: date
    indicator: str
    value: float
