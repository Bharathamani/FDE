"""Data-quality reporting for gaps, duplicates, and out-of-order readings.

These are first-class operational signals, not silent clean-up. The replay and
API expose what the engine had to repair before it could evaluate a truck.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable

from .config import Policy, PolicyBook
from .models import Reading
from .timeutil import format_timestamp, minutes_between

PolicyResolver = Callable[[str], Policy]


@dataclass(frozen=True)
class Gap:
    start: datetime
    end: datetime
    minutes: float
    within_tolerance: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": format_timestamp(self.start),
            "end": format_timestamp(self.end),
            "minutes": round(self.minutes, 2),
            "within_tolerance": self.within_tolerance,
        }


@dataclass
class TruckQuality:
    truck_id: str
    raw_readings: int
    unique_timestamps: int
    duplicate_timestamps: int
    out_of_order_readings: int
    first_seen: datetime | None
    last_seen: datetime | None
    expected_samples: int
    missing_samples: int
    largest_gap_minutes: float
    gaps: list[Gap] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "truck_id": self.truck_id,
            "raw_readings": self.raw_readings,
            "unique_timestamps": self.unique_timestamps,
            "duplicate_timestamps": self.duplicate_timestamps,
            "out_of_order_readings": self.out_of_order_readings,
            "first_seen": format_timestamp(self.first_seen) if self.first_seen else None,
            "last_seen": format_timestamp(self.last_seen) if self.last_seen else None,
            "expected_samples": self.expected_samples,
            "missing_samples": self.missing_samples,
            "largest_gap_minutes": round(self.largest_gap_minutes, 2),
            "gaps": [gap.to_dict() for gap in self.gaps],
        }


@dataclass
class DataQualityReport:
    readings: int
    unique_readings: int
    duplicate_timestamps: int
    out_of_order_readings: int
    gaps_over_tolerance: int
    missing_samples: int
    trucks: list[TruckQuality] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "readings": self.readings,
            "unique_readings": self.unique_readings,
            "duplicate_timestamps": self.duplicate_timestamps,
            "out_of_order_readings": self.out_of_order_readings,
            "gaps_over_tolerance": self.gaps_over_tolerance,
            "missing_samples": self.missing_samples,
            "trucks": [truck.to_dict() for truck in self.trucks],
        }


def _resolve_policy(
    policy_for: Policy | PolicyBook | PolicyResolver,
) -> PolicyResolver:
    if isinstance(policy_for, Policy):
        return lambda _truck_id: policy_for
    if isinstance(policy_for, PolicyBook):
        return policy_for.for_truck
    return policy_for


def _truck_quality(
    truck_id: str,
    readings: list[Reading],
    policy: Policy,
) -> TruckQuality:
    duplicate_timestamps = len(readings) - len({reading.timestamp for reading in readings})

    out_of_order = 0
    previous: datetime | None = None
    for reading in readings:
        if previous is not None and reading.timestamp < previous:
            out_of_order += 1
        previous = reading.timestamp

    by_timestamp = {reading.timestamp: reading for reading in readings}
    ordered = [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]
    first_seen = ordered[0].timestamp if ordered else None
    last_seen = ordered[-1].timestamp if ordered else None

    expected_samples = 0
    missing_samples = 0
    largest_gap = 0.0
    gaps: list[Gap] = []

    if first_seen and last_seen:
        span = minutes_between(first_seen, last_seen)
        expected_samples = max(1, int(span // policy.nominal_interval_minutes) + 1)
        missing_samples = max(0, expected_samples - len(by_timestamp))
        for earlier, later in zip(ordered, ordered[1:]):
            gap_minutes = minutes_between(earlier.timestamp, later.timestamp)
            largest_gap = max(largest_gap, gap_minutes)
            if gap_minutes > policy.nominal_interval_minutes:
                gaps.append(
                    Gap(
                        start=earlier.timestamp,
                        end=later.timestamp,
                        minutes=gap_minutes,
                        within_tolerance=gap_minutes <= policy.max_gap_minutes,
                    )
                )

    return TruckQuality(
        truck_id=truck_id,
        raw_readings=len(readings),
        unique_timestamps=len(by_timestamp),
        duplicate_timestamps=duplicate_timestamps,
        out_of_order_readings=out_of_order,
        first_seen=first_seen,
        last_seen=last_seen,
        expected_samples=expected_samples,
        missing_samples=missing_samples,
        largest_gap_minutes=largest_gap,
        gaps=gaps,
    )


def analyze_data_quality(
    readings: Iterable[Reading],
    policy_for: Policy | PolicyBook | PolicyResolver,
) -> DataQualityReport:
    resolver = _resolve_policy(policy_for)
    readings_list = list(readings)
    grouped: dict[str, list[Reading]] = {}
    for reading in readings_list:
        grouped.setdefault(reading.truck_id, []).append(reading)

    trucks = [
        _truck_quality(truck_id, grouped[truck_id], resolver(truck_id))
        for truck_id in sorted(grouped)
    ]
    return DataQualityReport(
        readings=len(readings_list),
        unique_readings=len({(reading.truck_id, reading.timestamp) for reading in readings_list}),
        duplicate_timestamps=sum(truck.duplicate_timestamps for truck in trucks),
        out_of_order_readings=sum(truck.out_of_order_readings for truck in trucks),
        gaps_over_tolerance=sum(
            1 for truck in trucks for gap in truck.gaps if not gap.within_tolerance
        ),
        missing_samples=sum(truck.missing_samples for truck in trucks),
        trucks=trucks,
    )
