"""Minimal outbound notification hook.

This is deliberately small: the interesting logic is the alert lifecycle, not
the transport. A Slack-compatible webhook is enough to demonstrate how an
OPENED/RESOLVED event leaves the system.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def post_webhook(url: str, payload: dict[str, Any], timeout_seconds: float = 3.0) -> bool:
    event_type = payload.get("event_type", "ALERT")
    text = (
        f"[{event_type}] {payload.get('truck_id', '?')} "
        f"{payload.get('severity', '')}: {payload.get('summary', '')}"
    ).strip()
    body = {"text": text, "coldchain_event": payload}
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return 200 <= response.status < 300
    except Exception as exc:  # noqa: BLE001 - notification failures must not fail ingest
        logger.warning("Webhook notification failed: %s", exc)
        return False
