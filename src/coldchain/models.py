"""Domain models shared by the engine, store, API, and CLI."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from .timeutil import format_timestamp, parse_timestamp

STATUS_OPEN = "OPEN"
STATUS_RESOLVED = "RESOLVED"
STATUS_SUPERSEDED = "SUPERSEDED"

SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@dataclass(frozen=True)
class Reading:
    truck_id: str
    timestamp: datetime
    temperature_c: float
    humidity_pct: float | None = None

    def __post_init__(self) -> None:
        truck_id = str(self.truck_id).strip()
        if not truck_id:
            raise ValueError("truck_id is required")

        timestamp = parse_timestamp(self.timestamp)
        temperature = float(self.temperature_c)
        if not math.isfinite(temperature):
            raise ValueError("temperature_c must be a finite number")
        if temperature < -80 or temperature > 80:
            raise ValueError("temperature_c is outside the plausible sensor range (-80..80 C)")

        humidity = self.humidity_pct
        if humidity is not None:
            humidity = float(humidity)
            if not math.isfinite(humidity) or humidity < 0 or humidity > 100:
                raise ValueError("humidity_pct must be between 0 and 100")

        object.__setattr__(self, "truck_id", truck_id)
        object.__setattr__(self, "timestamp", timestamp)
        object.__setattr__(self, "temperature_c", temperature)
        object.__setattr__(self, "humidity_pct", humidity)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Reading":
        truck_id = raw.get("truck_id", raw.get("truckId"))
        timestamp = raw.get("timestamp", raw.get("ts"))
        temperature = raw.get("temperature_c", raw.get("temperature"))
        if truck_id is None or timestamp is None or temperature is None:
            raise ValueError("reading requires truck_id, timestamp, and temperature_c")
        return cls(
            truck_id=str(truck_id),
            timestamp=parse_timestamp(timestamp),
            temperature_c=float(temperature),
            humidity_pct=raw.get("humidity_pct", raw.get("humidity")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "truck_id": self.truck_id,
            "timestamp": format_timestamp(self.timestamp),
            "temperature_c": self.temperature_c,
            "humidity_pct": self.humidity_pct,
        }


@dataclass(frozen=True)
class BreachSample:
    timestamp: datetime
    temperature_c: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": format_timestamp(self.timestamp),
            "temperature_c": self.temperature_c,
        }


@dataclass(frozen=True)
class DataGap:
    start: datetime
    end: datetime
    minutes: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": format_timestamp(self.start),
            "end": format_timestamp(self.end),
            "minutes": round(self.minutes, 2),
        }


@dataclass
class Incident:
    incident_id: str
    truck_id: str
    status: str
    severity: str
    run_start: datetime
    detected_at: datetime | None
    last_breach_at: datetime
    resolved_at: datetime | None
    peak_c: float
    breach_count: int
    exposure_minutes: float
    coverage: float
    max_gap_minutes: float
    threshold_c: float
    clear_threshold_c: float
    summary: str = ""
    breach_samples: list[BreachSample] = field(default_factory=list)
    gaps: list[DataGap] = field(default_factory=list)
    expected_samples: int = 0

    def evidence_dict(self) -> dict[str, Any]:
        return {
            "breach_samples": [sample.to_dict() for sample in self.breach_samples],
            "gaps": [gap.to_dict() for gap in self.gaps],
            "expected_samples": self.expected_samples,
            "threshold_c": self.threshold_c,
            "clear_threshold_c": self.clear_threshold_c,
        }

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "incident_id": self.incident_id,
            "truck_id": self.truck_id,
            "status": self.status,
            "severity": self.severity,
            "run_start": format_timestamp(self.run_start),
            "detected_at": format_timestamp(self.detected_at) if self.detected_at else None,
            "last_breach_at": format_timestamp(self.last_breach_at),
            "resolved_at": format_timestamp(self.resolved_at) if self.resolved_at else None,
            "peak_c": round(self.peak_c, 2),
            "breach_count": self.breach_count,
            "exposure_minutes": round(self.exposure_minutes, 2),
            "coverage": round(self.coverage, 3),
            "max_gap_minutes": round(self.max_gap_minutes, 2),
            "threshold_c": self.threshold_c,
            "clear_threshold_c": self.clear_threshold_c,
            "expected_samples": self.expected_samples,
            "summary": self.summary,
            "breach_samples": [sample.to_dict() for sample in self.breach_samples],
            "gaps": [gap.to_dict() for gap in self.gaps],
        }
        return data


@dataclass(frozen=True)
class SuppressedEpisode:
    truck_id: str
    start: datetime
    end: datetime
    peak_c: float
    breach_count: int
    duration_minutes: float
    max_gap_minutes: float
    reason_code: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "truck_id": self.truck_id,
            "start": format_timestamp(self.start),
            "end": format_timestamp(self.end),
            "peak_c": round(self.peak_c, 2),
            "breach_count": self.breach_count,
            "duration_minutes": round(self.duration_minutes, 2),
            "max_gap_minutes": round(self.max_gap_minutes, 2),
            "reason_code": self.reason_code,
            "reason": self.reason,
        }


@dataclass
class AnalysisResult:
    incidents: list[Incident]
    suppressed: list[SuppressedEpisode]
    readings_count: int
    trucks: list[str]
    data_quality: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "readings_count": self.readings_count,
            "trucks": self.trucks,
            "data_quality": self.data_quality,
            "incidents": [incident.to_dict() for incident in self.incidents],
            "suppressed": [episode.to_dict() for episode in self.suppressed],
        }
