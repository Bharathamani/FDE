"""Human-readable alert summaries.

A deterministic template is always available. An optional LLM enhancement can
rewrite that template into a shorter ops-friendly note when credentials are
configured; failures always fall back to the template.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

from .models import Incident, STATUS_OPEN
from .timeutil import format_clock, format_duration

logger = logging.getLogger(__name__)


def build_summary(incident: Incident) -> str:
    window = f"{format_clock(incident.run_start)}-{format_clock(incident.last_breach_at)} UTC"
    duration = format_duration(incident.exposure_minutes)
    excess = incident.peak_c - incident.threshold_c
    expected = incident.expected_samples or incident.breach_count
    coverage = round(incident.coverage * 100)

    text = (
        f"{incident.truck_id} exceeded the {incident.threshold_c:.1f} C safe limit for "
        f"{duration} ({window}), peaking at {incident.peak_c:.1f} C "
        f"({excess:+.1f} C over the limit). {incident.breach_count} of {expected} expected "
        f"readings ({coverage}%) were above the limit."
    )

    if incident.gaps:
        largest = max(gap.minutes for gap in incident.gaps)
        text += f" A {largest:.0f}-minute sensor data gap was tolerated within the episode."
    if incident.status != STATUS_OPEN:
        resolved = format_clock(incident.resolved_at) if incident.resolved_at else "unknown"
        text += f" The excursion cleared at {resolved} UTC."
    else:
        text += " The excursion was still open at the end of the available data."

    text += (
        " Suggested next step: verify the reefer setpoint and compressor, check door seals, "
        "and review cargo exposure against the customer SLA."
    )
    return text


class Summarizer:
    """Template-first summarizer with an optional, cached LLM enhancement."""

    def __init__(
        self,
        llm_enabled: bool = False,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.llm_enabled = bool(llm_enabled and api_key)
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._cache: dict[tuple[Any, ...], str] = {}

    def summarize(self, incident: Incident) -> str:
        baseline = build_summary(incident)
        if not self.llm_enabled:
            return baseline

        cache_key = (
            incident.incident_id,
            incident.status,
            round(incident.peak_c, 1),
            round(incident.exposure_minutes, 1),
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        try:
            summary = self._call_llm(incident, baseline)
        except Exception as exc:  # noqa: BLE001 - fall back on any provider failure
            logger.warning("LLM summary failed; using template: %s", exc)
            summary = baseline

        self._cache[cache_key] = summary
        return summary

    def _call_llm(self, incident: Incident, baseline: str) -> str:
        prompt = (
            f"Severity: {incident.severity}\n"
            f"Status: {incident.status}\n"
            f"Truck: {incident.truck_id}\n"
            f"Threshold: {incident.threshold_c:.1f} C\n"
            f"Peak: {incident.peak_c:.1f} C\n"
            f"Duration above threshold: {incident.exposure_minutes:.0f} minutes\n"
            f"Breach readings: {incident.breach_count} (coverage {incident.coverage:.0%})\n"
            f"Data gaps: {len(incident.gaps)}\n\n"
            f"Draft summary: {baseline}"
        )
        payload = {
            "model": self.model,
            "temperature": 0.2,
            "max_tokens": 180,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You write concise cold-chain operations alerts. In 2-3 plain-English "
                        "sentences, state the truck, how long it was above the limit, the peak, "
                        "whether it is still open, and one practical next check. Do not invent "
                        "a root cause or add facts not present in the data."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        return str(content).strip() or baseline
