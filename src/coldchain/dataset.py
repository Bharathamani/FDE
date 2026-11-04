"""Dataset loading for replay and tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Reading


def load_readings(path: str | Path) -> list[Reading]:
    dataset_path = Path(path)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    raw: Any = json.loads(dataset_path.read_text(encoding="utf-8"))
    items = raw.get("readings", []) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError("Dataset must be a list of readings or an object with a 'readings' list")

    readings: list[Reading] = []
    for index, item in enumerate(items):
        try:
            readings.append(Reading.from_mapping(item))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid reading at index {index}: {exc}") from exc
    return readings
