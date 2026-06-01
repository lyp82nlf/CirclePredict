from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Dimension(StrEnum):
    VALUATION = "valuation"
    SENTIMENT = "sentiment"
    MARKET = "market"
    MACRO = "macro"


class Direction(StrEnum):
    HIGH_GOOD = "high_good"
    HIGH_BAD = "high_bad"


@dataclass(frozen=True)
class IndicatorDefinition:
    id: str
    market: str
    name: str
    dimension: Dimension
    direction: Direction
    weight: float
    source: str
    unit: str
    description: str


@dataclass(frozen=True)
class IndicatorObservation:
    date: date
    value: float | None


@dataclass(frozen=True)
class MarketDataset:
    market: str
    label: str
    as_of_date: date
    indicators: dict[str, list[IndicatorObservation]]


@dataclass(frozen=True)
class ScoreWindow:
    name: str
    days: int

