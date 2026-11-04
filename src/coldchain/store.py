"""SQLite persistence and alert-event deduplication.

Readings are keyed by (truck_id, timestamp), so replaying the same dataset or
receiving a retried sensor message is idempotent. Incidents are keyed by a
deterministic hash of truck + episode start, which lets the service recompute a
truck after late or out-of-order data without duplicating alerts.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import (
    STATUS_OPEN,
    STATUS_RESOLVED,
    STATUS_SUPERSEDED,
    Incident,
    Reading,
)
from .timeutil import format_timestamp, parse_timestamp

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    truck_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    temperature_c REAL NOT NULL,
    humidity_pct REAL,
    ingested_at TEXT NOT NULL,
    PRIMARY KEY (truck_id, timestamp)
);

CREATE TABLE IF NOT EXISTS incidents (
    incident_id TEXT PRIMARY KEY,
    truck_id TEXT NOT NULL,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    run_start TEXT NOT NULL,
    detected_at TEXT,
    last_breach_at TEXT NOT NULL,
    resolved_at TEXT,
    peak_c REAL NOT NULL,
    breach_count INTEGER NOT NULL,
    exposure_minutes REAL NOT NULL,
    coverage REAL NOT NULL,
    max_gap_minutes REAL NOT NULL,
    threshold_c REAL NOT NULL,
    clear_threshold_c REAL NOT NULL,
    summary TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incidents_truck_status
    ON incidents(truck_id, status);

CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(incident_id, event_type)
);

CREATE INDEX IF NOT EXISTS idx_alert_events_incident
    ON alert_events(incident_id);
"""


def _now() -> str:
    return format_timestamp(datetime.now(timezone.utc))


class AlertStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def __enter__(self) -> "AlertStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Readings
    # ------------------------------------------------------------------
    def upsert_readings(self, readings: Iterable[Reading]) -> list[str]:
        now = _now()
        trucks: set[str] = set()
        with self._lock:
            for reading in readings:
                self._conn.execute(
                    """
                    INSERT INTO readings
                        (truck_id, timestamp, temperature_c, humidity_pct, ingested_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(truck_id, timestamp) DO UPDATE SET
                        temperature_c = excluded.temperature_c,
                        humidity_pct = excluded.humidity_pct,
                        ingested_at = excluded.ingested_at
                    """,
                    (
                        reading.truck_id,
                        format_timestamp(reading.timestamp),
                        reading.temperature_c,
                        reading.humidity_pct,
                        now,
                    ),
                )
                trucks.add(reading.truck_id)
            self._conn.commit()
        return sorted(trucks)

    def get_truck_readings(self, truck_id: str) -> list[Reading]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT truck_id, timestamp, temperature_c, humidity_pct
                FROM readings
                WHERE truck_id = ?
                ORDER BY timestamp ASC
                """,
                (truck_id,),
            ).fetchall()
        return [
            Reading(
                truck_id=row["truck_id"],
                timestamp=parse_timestamp(row["timestamp"]),
                temperature_c=row["temperature_c"],
                humidity_pct=row["humidity_pct"],
            )
            for row in rows
        ]

    def is_empty(self) -> bool:
        with self._lock:
            readings = self._conn.execute("SELECT COUNT(*) AS n FROM readings").fetchone()["n"]
            incidents = self._conn.execute("SELECT COUNT(*) AS n FROM incidents").fetchone()["n"]
        return readings == 0 and incidents == 0

    # ------------------------------------------------------------------
    # Incidents and alert events
    # ------------------------------------------------------------------
    def save_analysis(self, truck_id: str, incidents: Iterable[Incident]) -> list[dict[str, Any]]:
        now = _now()
        events: list[dict[str, Any]] = []

        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM incidents WHERE truck_id = ?", (truck_id,)
            ).fetchall()
            existing = {row["incident_id"]: row for row in rows}
            seen: set[str] = set()

            for incident in incidents:
                seen.add(incident.incident_id)
                evidence = json.dumps(
                    incident.evidence_dict(), separators=(",", ":"), ensure_ascii=False
                )
                row = existing.get(incident.incident_id)

                if row is None:
                    self._conn.execute(
                        """
                        INSERT INTO incidents (
                            incident_id, truck_id, status, severity, run_start,
                            detected_at, last_breach_at, resolved_at, peak_c,
                            breach_count, exposure_minutes, coverage, max_gap_minutes,
                            threshold_c, clear_threshold_c, summary, evidence_json,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            incident.incident_id,
                            incident.truck_id,
                            incident.status,
                            incident.severity,
                            format_timestamp(incident.run_start),
                            format_timestamp(incident.detected_at) if incident.detected_at else None,
                            format_timestamp(incident.last_breach_at),
                            format_timestamp(incident.resolved_at) if incident.resolved_at else None,
                            incident.peak_c,
                            incident.breach_count,
                            incident.exposure_minutes,
                            incident.coverage,
                            incident.max_gap_minutes,
                            incident.threshold_c,
                            incident.clear_threshold_c,
                            incident.summary,
                            evidence,
                            now,
                            now,
                        ),
                    )
                    opened = self._insert_event(
                        "OPENED",
                        incident,
                        incident.detected_at or incident.run_start,
                        now,
                    )
                    if opened:
                        events.append(opened)
                    if incident.status == STATUS_RESOLVED and incident.resolved_at:
                        resolved = self._insert_event(
                            "RESOLVED", incident, incident.resolved_at, now
                        )
                        if resolved:
                            events.append(resolved)
                else:
                    self._conn.execute(
                        """
                        UPDATE incidents SET
                            status = ?, severity = ?, detected_at = ?, last_breach_at = ?,
                            resolved_at = ?, peak_c = ?, breach_count = ?,
                            exposure_minutes = ?, coverage = ?, max_gap_minutes = ?,
                            threshold_c = ?, clear_threshold_c = ?, summary = ?,
                            evidence_json = ?, updated_at = ?
                        WHERE incident_id = ?
                        """,
                        (
                            incident.status,
                            incident.severity,
                            format_timestamp(incident.detected_at) if incident.detected_at else None,
                            format_timestamp(incident.last_breach_at),
                            format_timestamp(incident.resolved_at) if incident.resolved_at else None,
                            incident.peak_c,
                            incident.breach_count,
                            incident.exposure_minutes,
                            incident.coverage,
                            incident.max_gap_minutes,
                            incident.threshold_c,
                            incident.clear_threshold_c,
                            incident.summary,
                            evidence,
                            now,
                            incident.incident_id,
                        ),
                    )
                    if row["status"] != STATUS_RESOLVED and incident.status == STATUS_RESOLVED:
                        resolved = self._insert_event(
                            "RESOLVED",
                            incident,
                            incident.resolved_at or incident.last_breach_at,
                            now,
                        )
                        if resolved:
                            events.append(resolved)

            # A recomputation can invalidate an episode (for example, when a late
            # reading shows that a "breach" was a sensor spike). Keep the audit row
            # but remove it from the active/resolved list.
            for incident_id, row in existing.items():
                if incident_id in seen or row["status"] == STATUS_SUPERSEDED:
                    continue
                self._conn.execute(
                    "UPDATE incidents SET status = ?, updated_at = ? WHERE incident_id = ?",
                    (STATUS_SUPERSEDED, now, incident_id),
                )
                payload = {
                    "event_type": "SUPERSEDED",
                    "incident_id": incident_id,
                    "truck_id": row["truck_id"],
                    "severity": row["severity"],
                    "status": STATUS_SUPERSEDED,
                    "summary": row["summary"],
                    "occurred_at": now,
                }
                cursor = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO alert_events
                        (incident_id, event_type, occurred_at, created_at, payload_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        incident_id,
                        "SUPERSEDED",
                        now,
                        now,
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                if cursor.rowcount:
                    events.append(payload)

            self._conn.commit()

        return events

    def _insert_event(
        self,
        event_type: str,
        incident: Incident,
        occurred_at: datetime,
        created_at: str,
    ) -> dict[str, Any] | None:
        payload = {
            "event_type": event_type,
            "incident_id": incident.incident_id,
            "truck_id": incident.truck_id,
            "severity": incident.severity,
            "status": incident.status,
            "summary": incident.summary,
            "occurred_at": format_timestamp(occurred_at),
        }
        cursor = self._conn.execute(
            """
            INSERT OR IGNORE INTO alert_events
                (incident_id, event_type, occurred_at, created_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                incident.incident_id,
                event_type,
                format_timestamp(occurred_at),
                created_at,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        return payload if cursor.rowcount else None

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            evidence = json.loads(data.pop("evidence_json") or "{}")
        except json.JSONDecodeError:
            evidence = {}
        data["breach_samples"] = evidence.get("breach_samples", [])
        data["gaps"] = evidence.get("gaps", [])
        data["expected_samples"] = evidence.get("expected_samples", 0)
        data["evidence"] = evidence
        return data

    def list_incidents(
        self,
        status: str | None = None,
        truck_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        normalised = (status or "all").strip().upper()

        if normalised == "ALL":
            clauses.append("status != ?")
            params.append(STATUS_SUPERSEDED)
        elif normalised in {STATUS_OPEN, STATUS_RESOLVED, STATUS_SUPERSEDED}:
            clauses.append("status = ?")
            params.append(normalised)
        else:
            raise ValueError("status must be one of: all, open, resolved, superseded")

        if truck_id:
            clauses.append("truck_id = ?")
            params.append(truck_id)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"""
            SELECT * FROM incidents
            {where}
            ORDER BY
                CASE status WHEN 'OPEN' THEN 0 WHEN 'RESOLVED' THEN 1 ELSE 2 END,
                CASE severity
                    WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1
                    WHEN 'MEDIUM' THEN 2 ELSE 3
                END,
                detected_at DESC
            LIMIT ?
        """
        params.append(max(1, int(limit)))

        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def list_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT payload_json FROM alert_events
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            try:
                events.append(json.loads(row["payload_json"]))
            except json.JSONDecodeError:
                continue
        return events

    # ------------------------------------------------------------------
    # Read models for the dashboard / API
    # ------------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        with self._lock:
            readings = self._conn.execute("SELECT COUNT(*) AS n FROM readings").fetchone()["n"]
            trucks = self._conn.execute(
                "SELECT COUNT(DISTINCT truck_id) AS n FROM readings"
            ).fetchone()["n"]
            open_incidents = self._conn.execute(
                "SELECT COUNT(*) AS n FROM incidents WHERE status = ?", (STATUS_OPEN,)
            ).fetchone()["n"]
            resolved_incidents = self._conn.execute(
                "SELECT COUNT(*) AS n FROM incidents WHERE status = ?", (STATUS_RESOLVED,)
            ).fetchone()["n"]
            critical_open = self._conn.execute(
                "SELECT COUNT(*) AS n FROM incidents WHERE status = ? AND severity = 'CRITICAL'",
                (STATUS_OPEN,),
            ).fetchone()["n"]
            alert_events = self._conn.execute(
                "SELECT COUNT(*) AS n FROM alert_events"
            ).fetchone()["n"]
            last_reading = self._conn.execute(
                "SELECT MAX(timestamp) AS ts FROM readings"
            ).fetchone()["ts"]
        return {
            "readings": readings,
            "trucks": trucks,
            "open_incidents": open_incidents,
            "resolved_incidents": resolved_incidents,
            "critical_open": critical_open,
            "alert_events": alert_events,
            "last_reading_at": last_reading,
        }

    def truck_overview(self) -> list[dict[str, Any]]:
        with self._lock:
            trucks = self._conn.execute(
                "SELECT DISTINCT truck_id FROM readings ORDER BY truck_id"
            ).fetchall()
            result: list[dict[str, Any]] = []
            for truck in trucks:
                truck_id = truck["truck_id"]
                latest = self._conn.execute(
                    """
                    SELECT timestamp, temperature_c, humidity_pct
                    FROM readings WHERE truck_id = ?
                    ORDER BY timestamp DESC LIMIT 1
                    """,
                    (truck_id,),
                ).fetchone()
                open_incident = self._conn.execute(
                    """
                    SELECT incident_id, severity, run_start, detected_at, peak_c
                    FROM incidents
                    WHERE truck_id = ? AND status = ?
                    ORDER BY detected_at DESC LIMIT 1
                    """,
                    (truck_id, STATUS_OPEN),
                ).fetchone()
                resolved_count = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM incidents WHERE truck_id = ? AND status = ?",
                    (truck_id, STATUS_RESOLVED),
                ).fetchone()["n"]

                result.append(
                    {
                        "truck_id": truck_id,
                        "status": "ALERT" if open_incident else "OK",
                        "latest_temperature_c": latest["temperature_c"] if latest else None,
                        "humidity_pct": latest["humidity_pct"] if latest else None,
                        "last_reading_at": latest["timestamp"] if latest else None,
                        "open_incident_id": open_incident["incident_id"] if open_incident else None,
                        "open_severity": open_incident["severity"] if open_incident else None,
                        "open_since": open_incident["run_start"] if open_incident else None,
                        "resolved_incidents": resolved_count,
                    }
                )
        return result

    # ------------------------------------------------------------------
    # Demo reset
    # ------------------------------------------------------------------
    def reset(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM alert_events")
            self._conn.execute("DELETE FROM incidents")
            self._conn.execute("DELETE FROM readings")
            self._conn.execute(
                "DELETE FROM sqlite_sequence WHERE name = 'alert_events'"
            )
            self._conn.commit()
