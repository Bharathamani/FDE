"""Pure, deterministic sustained-excursion detection.

The engine is event-time based and has no database or wall-clock dependency. It
sorts each truck's readings, deduplicates equal timestamps, and walks a small
per-truck state machine. This makes replay, tests, and late-arriving data
recomputation use exactly the same logic.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable

from .config import Policy, PolicyBook
from .models import (
    STATUS_OPEN,
    STATUS_RESOLVED,
    AnalysisResult,
    BreachSample,
    DataGap,
    Incident,
    Reading,
    SuppressedEpisode,
)
from .quality import analyze_data_quality
from .summarizer import build_summary
from .timeutil import format_clock, minutes_between

PolicyResolver = Callable[[str], Policy]


@dataclass
class _Run:
    start: datetime
    last_breach: datetime
    peak_c: float
    samples: list[BreachSample] = field(default_factory=list)
    gaps: list[DataGap] = field(default_factory=list)
    breach_count: int = 1
    max_gap_minutes: float = 0.0
    state: str = "PENDING"
    detected_at: datetime | None = None
    recovery_count: int = 0


def _resolve_policy(policy_for: Policy | PolicyBook | PolicyResolver) -> PolicyResolver:
    if isinstance(policy_for, Policy):
        return lambda _truck_id: policy_for
    if isinstance(policy_for, PolicyBook):
        return policy_for.for_truck
    return policy_for


def _incident_id(truck_id: str, run_start: datetime) -> str:
    digest = hashlib.sha1(
        f"{truck_id}|{run_start.isoformat()}".encode("utf-8")
    ).hexdigest()[:12].upper()
    return f"INC-{digest}"


def _expected_samples(exposure_minutes: float, policy: Policy) -> int:
    return max(1, int(exposure_minutes // policy.nominal_interval_minutes) + 1)


def _coverage(breach_count: int, expected_samples: int) -> float:
    return min(1.0, breach_count / expected_samples) if expected_samples else 0.0


def _severity(peak_c: float, exposure_minutes: float, policy: Policy) -> str:
    excess = peak_c - policy.threshold_c
    if excess >= 4.0 or exposure_minutes >= 60:
        return "CRITICAL"
    if excess >= 2.0 or exposure_minutes >= 30:
        return "HIGH"
    return "MEDIUM"


def _condition_met(run: _Run, policy: Policy) -> bool:
    exposure = minutes_between(run.start, run.last_breach)
    expected = _expected_samples(exposure, policy)
    coverage = _coverage(run.breach_count, expected)
    return (
        exposure >= policy.min_duration_minutes
        and run.breach_count >= policy.min_breach_readings
        and coverage >= policy.min_coverage
        and run.max_gap_minutes <= policy.max_gap_minutes
    )


def _finalize(
    run: _Run,
    truck_id: str,
    policy: Policy,
    status: str,
    resolved_at: datetime | None = None,
) -> Incident:
    exposure = minutes_between(run.start, run.last_breach)
    expected = _expected_samples(exposure, policy)
    coverage = _coverage(run.breach_count, expected)
    incident = Incident(
        incident_id=_incident_id(truck_id, run.start),
        truck_id=truck_id,
        status=status,
        severity=_severity(run.peak_c, exposure, policy),
        run_start=run.start,
        detected_at=run.detected_at or run.last_breach,
        last_breach_at=run.last_breach,
        resolved_at=resolved_at,
        peak_c=run.peak_c,
        breach_count=run.breach_count,
        exposure_minutes=exposure,
        coverage=coverage,
        max_gap_minutes=run.max_gap_minutes,
        threshold_c=policy.threshold_c,
        clear_threshold_c=policy.clear_threshold_c,
        breach_samples=list(run.samples),
        gaps=list(run.gaps),
        expected_samples=expected,
    )
    incident.summary = build_summary(incident)
    return incident


def _suppressed(
    run: _Run,
    truck_id: str,
    policy: Policy,
    reason_code: str,
    reason: str,
) -> SuppressedEpisode:
    duration = minutes_between(run.start, run.last_breach)
    return SuppressedEpisode(
        truck_id=truck_id,
        start=run.start,
        end=run.last_breach,
        peak_c=run.peak_c,
        breach_count=run.breach_count,
        duration_minutes=duration,
        max_gap_minutes=run.max_gap_minutes,
        reason_code=reason_code,
        reason=reason,
    )


def _dedupe_and_sort(readings: Iterable[Reading]) -> list[Reading]:
    by_timestamp: dict[datetime, Reading] = {}
    for reading in readings:
        by_timestamp[reading.timestamp] = reading
    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


def analyze_truck(
    truck_id: str,
    readings: Iterable[Reading],
    policy: Policy,
) -> tuple[list[Incident], list[SuppressedEpisode]]:
    ordered = _dedupe_and_sort(readings)
    incidents: list[Incident] = []
    suppressed: list[SuppressedEpisode] = []
    run: _Run | None = None
    last_timestamp: datetime | None = None

    for reading in ordered:
        gap_minutes = (
            minutes_between(last_timestamp, reading.timestamp) if last_timestamp else 0.0
        )

        if run is not None and gap_minutes > 0:
            run.max_gap_minutes = max(run.max_gap_minutes, gap_minutes)
            if gap_minutes > policy.max_gap_minutes:
                if run.state == "PENDING":
                    suppressed.append(
                        _suppressed(
                            run,
                            truck_id,
                            policy,
                            "data_gap",
                            (
                                f"a {gap_minutes:.0f}-minute data gap exceeded the "
                                f"{policy.max_gap_minutes:.0f}-minute tolerance before the "
                                "sustained threshold was met"
                            ),
                        )
                    )
                    run = None
                else:
                    # Once an alert is active, missing data must not silently resolve
                    # it. Keep the incident open, record the gap, and require fresh
                    # recovery evidence after connectivity returns.
                    run.gaps.append(
                        DataGap(
                            start=last_timestamp,
                            end=reading.timestamp,
                            minutes=gap_minutes,
                        )
                    )
                    run.recovery_count = 0

        if reading.temperature_c > policy.threshold_c:
            if run is None:
                run = _Run(
                    start=reading.timestamp,
                    last_breach=reading.timestamp,
                    peak_c=reading.temperature_c,
                    samples=[
                        BreachSample(
                            timestamp=reading.timestamp,
                            temperature_c=reading.temperature_c,
                        )
                    ],
                )
            else:
                run.last_breach = reading.timestamp
                run.breach_count += 1
                run.peak_c = max(run.peak_c, reading.temperature_c)
                run.samples.append(
                    BreachSample(
                        timestamp=reading.timestamp,
                        temperature_c=reading.temperature_c,
                    )
                )
                run.recovery_count = 0

            if run.state == "PENDING" and _condition_met(run, policy):
                run.state = "ACTIVE"
                run.detected_at = reading.timestamp

        elif reading.temperature_c <= policy.clear_threshold_c:
            if run is not None:
                if run.state == "ACTIVE":
                    run.recovery_count += 1
                    if run.recovery_count >= policy.recovery_readings:
                        incidents.append(
                            _finalize(
                                run,
                                truck_id,
                                policy,
                                STATUS_RESOLVED,
                                resolved_at=reading.timestamp,
                            )
                        )
                        run = None
                else:
                    suppressed.append(
                        _suppressed(
                            run,
                            truck_id,
                            policy,
                            "brief_excursion",
                            (
                                f"temperature returned to <= {policy.clear_threshold_c:.1f} C "
                                f"before the {policy.min_duration_minutes:.0f}-minute "
                                "sustained threshold was met"
                            ),
                        )
                    )
                    run = None

        else:
            # Hysteresis band. This is not recovered (so an active incident stays
            # open) but it is also not a new breach reading.
            if run is not None and run.state == "ACTIVE":
                run.recovery_count = 0

        last_timestamp = reading.timestamp

    if run is not None:
        if run.state == "ACTIVE":
            incidents.append(_finalize(run, truck_id, policy, STATUS_OPEN))
        else:
            suppressed.append(
                _suppressed(
                    run,
                    truck_id,
                    policy,
                    "insufficient_data",
                    (
                        "the sustained-excursion threshold was not met before the "
                        "available data ended"
                    ),
                )
            )

    return incidents, suppressed


def analyze_readings(
    readings: Iterable[Reading],
    policy_for: Policy | PolicyBook | PolicyResolver,
) -> AnalysisResult:
    resolver = _resolve_policy(policy_for)
    grouped: dict[str, list[Reading]] = {}
    readings_list = list(readings)
    for reading in readings_list:
        grouped.setdefault(reading.truck_id, []).append(reading)

    incidents: list[Incident] = []
    suppressed: list[SuppressedEpisode] = []

    for truck_id in sorted(grouped):
        truck_incidents, truck_suppressed = analyze_truck(
            truck_id, grouped[truck_id], resolver(truck_id)
        )
        incidents.extend(truck_incidents)
        suppressed.extend(truck_suppressed)

    incidents.sort(key=lambda item: (item.detected_at or item.run_start, item.truck_id))
    suppressed.sort(key=lambda item: (item.start, item.truck_id))
    quality = analyze_data_quality(readings_list, resolver)

    return AnalysisResult(
        incidents=incidents,
        suppressed=suppressed,
        readings_count=len(readings_list),
        trucks=sorted(grouped),
        data_quality=quality.to_dict(),
    )


def describe_reason(incident: Incident) -> str:
    """One-line explanation used by the CLI and dashboard."""
    return (
        f"{incident.truck_id} was above {incident.threshold_c:.1f} C from "
        f"{format_clock(incident.run_start)} to {format_clock(incident.last_breach_at)} "
        f"({incident.exposure_minutes:.0f} min), peak {incident.peak_c:.1f} C, "
        f"{incident.breach_count}/{incident.expected_samples} expected breach readings"
    )
