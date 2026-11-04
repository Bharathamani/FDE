"""Policy and runtime configuration.

The defaults are intentionally explicit: they are the assumptions an FDE would
put in front of the customer for sign-off, not hidden magic numbers.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class Policy:
    threshold_c: float = 8.0
    clear_threshold_c: float = 7.5
    min_duration_minutes: float = 20.0
    min_breach_readings: int = 3
    max_gap_minutes: float = 15.0
    recovery_readings: int = 2
    nominal_interval_minutes: float = 5.0
    min_coverage: float = 0.5

    def __post_init__(self) -> None:
        if self.threshold_c <= self.clear_threshold_c:
            raise ValueError("threshold_c must be greater than clear_threshold_c")
        if self.min_duration_minutes <= 0:
            raise ValueError("min_duration_minutes must be positive")
        if self.min_breach_readings < 2:
            raise ValueError("min_breach_readings must be at least 2")
        if self.max_gap_minutes <= 0:
            raise ValueError("max_gap_minutes must be positive")
        if self.recovery_readings < 1:
            raise ValueError("recovery_readings must be at least 1")
        if self.nominal_interval_minutes <= 0:
            raise ValueError("nominal_interval_minutes must be positive")
        if not 0 <= self.min_coverage <= 1:
            raise ValueError("min_coverage must be between 0 and 1")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "Policy":
        if not data:
            return cls()
        allowed = {item.name for item in fields(cls)}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"Unknown policy field(s): {', '.join(sorted(unknown))}")

        int_fields = {"min_breach_readings", "recovery_readings"}
        float_fields = {
            "threshold_c",
            "clear_threshold_c",
            "min_duration_minutes",
            "max_gap_minutes",
            "nominal_interval_minutes",
            "min_coverage",
        }
        kwargs: dict[str, Any] = {}
        for item in fields(cls):
            if item.name not in data:
                continue
            value = data[item.name]
            if item.name in int_fields:
                kwargs[item.name] = int(value)
            elif item.name in float_fields:
                kwargs[item.name] = float(value)
            else:
                kwargs[item.name] = value
        return cls(**kwargs)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PolicyBook:
    default: Policy = field(default_factory=Policy)
    trucks: Mapping[str, Policy] = field(default_factory=dict)

    def for_truck(self, truck_id: str) -> Policy:
        return self.trucks.get(truck_id, self.default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "default": self.default.to_dict(),
            "trucks": {truck: policy.to_dict() for truck, policy in self.trucks.items()},
        }


_ENV_POLICY_FIELDS = {
    "COLDCHAIN_THRESHOLD_C": "threshold_c",
    "COLDCHAIN_CLEAR_THRESHOLD_C": "clear_threshold_c",
    "COLDCHAIN_MIN_DURATION_MIN": "min_duration_minutes",
    "COLDCHAIN_MIN_BREACH_READINGS": "min_breach_readings",
    "COLDCHAIN_MAX_GAP_MIN": "max_gap_minutes",
    "COLDCHAIN_RECOVERY_READINGS": "recovery_readings",
    "COLDCHAIN_NOMINAL_INTERVAL_MIN": "nominal_interval_minutes",
    "COLDCHAIN_MIN_COVERAGE": "min_coverage",
}


def _apply_env(base: Policy) -> Policy:
    raw = base.to_dict()
    for env_name, field_name in _ENV_POLICY_FIELDS.items():
        if env_name in os.environ:
            raw[field_name] = os.environ[env_name]
    return Policy.from_mapping(raw)


def load_policy_book(path: str | Path | None = None) -> PolicyBook:
    """Load default policy plus optional per-truck overrides.

    Precedence: built-in defaults < file default < environment variables <
    per-truck file overrides. Environment variables are global by design; the
    file is the place for truck/cargo-specific rules.
    """
    base = Policy()
    truck_overrides: Mapping[str, Mapping[str, Any]] = {}

    if path:
        policy_path = Path(path)
        if not policy_path.exists():
            raise FileNotFoundError(f"Policy file not found: {policy_path}")
        raw = json.loads(policy_path.read_text(encoding="utf-8"))
        default_overrides = raw.get("default", {})
        base = Policy.from_mapping({**base.to_dict(), **default_overrides})
        truck_overrides = raw.get("trucks", {})

    base = _apply_env(base)
    trucks = {
        str(truck_id): Policy.from_mapping({**base.to_dict(), **override})
        for truck_id, override in truck_overrides.items()
    }
    return PolicyBook(default=base, trucks=trucks)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    db_path: Path
    dataset_path: Path
    policy_book: PolicyBook
    demo_mode: bool = False
    seed_demo: bool = False
    llm_enabled: bool = False
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    webhook_url: str | None = None


def load_settings() -> Settings:
    policy_file = os.environ.get("COLDCHAIN_POLICY_FILE") or None
    return Settings(
        db_path=Path(os.environ.get("COLDCHAIN_DB", "data/coldchain.db")),
        dataset_path=Path(os.environ.get("COLDCHAIN_DATASET", "data/sensor_readings.json")),
        policy_book=load_policy_book(policy_file),
        demo_mode=_env_bool("COLDCHAIN_DEMO_MODE", False),
        seed_demo=_env_bool("COLDCHAIN_SEED_DEMO", False),
        llm_enabled=_env_bool("COLDCHAIN_LLM_ENABLED", False),
        llm_model=os.environ.get("COLDCHAIN_LLM_MODEL", "gpt-4o-mini"),
        openai_api_key=os.environ.get("OPENAI_API_KEY"),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        webhook_url=os.environ.get("COLDCHAIN_WEBHOOK_URL") or None,
    )
