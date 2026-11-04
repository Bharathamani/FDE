"""Ingestion orchestration: persist readings, recompute per truck, dedupe events."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .config import PolicyBook
from .dataset import load_readings
from .engine import analyze_readings, analyze_truck
from .models import AnalysisResult, Reading
from .notify import post_webhook
from .store import AlertStore
from .summarizer import Summarizer


@dataclass
class IngestResult:
    readings_received: int
    readings_accepted: int
    trucks: list[str] = field(default_factory=list)
    new_events: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "readings_received": self.readings_received,
            "readings_accepted": self.readings_accepted,
            "trucks": self.trucks,
            "new_events": self.new_events,
            "stats": self.stats,
        }


class Pipeline:
    def __init__(
        self,
        store: AlertStore,
        policy_book: PolicyBook,
        summarizer: Summarizer | None = None,
        webhook_url: str | None = None,
    ) -> None:
        self.store = store
        self.policy_book = policy_book
        self.summarizer = summarizer
        self.webhook_url = webhook_url

    def ingest(self, readings: Iterable[Reading]) -> IngestResult:
        readings_list = list(readings)
        if not readings_list:
            return IngestResult(0, 0, stats=self.store.stats())

        trucks = self.store.upsert_readings(readings_list)
        events: list[dict[str, Any]] = []

        for truck_id in trucks:
            policy = self.policy_book.for_truck(truck_id)
            stored = self.store.get_truck_readings(truck_id)
            incidents, _suppressed = analyze_truck(truck_id, stored, policy)
            if self.summarizer is not None:
                for incident in incidents:
                    incident.summary = self.summarizer.summarize(incident)
            events.extend(self.store.save_analysis(truck_id, incidents))

        if self.webhook_url:
            for event in events:
                if event.get("event_type") in {"OPENED", "RESOLVED"}:
                    post_webhook(self.webhook_url, event)

        return IngestResult(
            readings_received=len(readings_list),
            readings_accepted=len(readings_list),
            trucks=trucks,
            new_events=events,
            stats=self.store.stats(),
        )

    def replay_file(self, path: str | Path, reset: bool = False) -> IngestResult:
        if reset:
            self.store.reset()
        return self.ingest(load_readings(path))

    def analyze_file(self, path: str | Path) -> AnalysisResult:
        return analyze_readings(load_readings(path), self.policy_book)
