"""Timestamp helpers.

All timestamps are normalised to UTC. The source data uses naive ISO strings;
we deliberately treat those as UTC rather than machine-local time so results are
reproducible across laptops, CI runners, and containers.
"""
from __future__ import annotations

from datetime import datetime, timezone


def parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_timestamp(value: datetime) -> str:
    """Canonical storage format: 2026-07-20T08:10:00Z."""
    return parse_timestamp(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def format_clock(value: datetime) -> str:
    return parse_timestamp(value).strftime("%H:%M")


def minutes_between(start: datetime, end: datetime) -> float:
    return (parse_timestamp(end) - parse_timestamp(start)).total_seconds() / 60.0


def format_duration(minutes: float) -> str:
    rounded = int(round(minutes))
    if rounded < 60:
        return f"{rounded} min"
    hours, remainder = divmod(rounded, 60)
    if remainder == 0:
        return f"{hours} hr"
    return f"{hours} hr {remainder} min"
