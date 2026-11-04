"""Command-line interface: replay, inspect, and serve."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from .config import Policy, PolicyBook, load_policy_book, load_settings
from .dataset import load_readings
from .engine import analyze_readings
from .models import AnalysisResult, Incident
from .pipeline import Pipeline
from .store import AlertStore
from .summarizer import Summarizer
from .timeutil import format_clock, format_duration, format_timestamp


def _policy_line(policy_book: PolicyBook) -> str:
    policy = policy_book.default
    return (
        f">{policy.threshold_c:.1f} C breach; <= {policy.clear_threshold_c:.1f} C clear; "
        f"sustained >= {policy.min_duration_minutes:.0f} min; "
        f">= {policy.min_breach_readings} breach readings; "
        f"gap tolerance {policy.max_gap_minutes:.0f} min; "
        f"recover after {policy.recovery_readings} clear readings"
    )


def _build_pipeline(args: argparse.Namespace) -> tuple[AlertStore, Pipeline, PolicyBook, Path]:
    settings = load_settings()
    policy_book = load_policy_book(args.policy or os.environ.get("COLDCHAIN_POLICY_FILE"))
    db_path = Path(args.db) if getattr(args, "db", None) else settings.db_path
    store = AlertStore(db_path)
    summarizer = Summarizer(
        llm_enabled=bool(getattr(args, "llm", False) or settings.llm_enabled),
        api_key=settings.openai_api_key,
        model=settings.llm_model,
        base_url=settings.openai_base_url,
    )
    pipeline = Pipeline(
        store=store,
        policy_book=policy_book,
        summarizer=summarizer,
        webhook_url=settings.webhook_url,
    )
    return store, pipeline, policy_book, db_path


def _print_quality(analysis: AnalysisResult) -> None:
    """Show what the engine had to repair before evaluating the data."""
    quality = analysis.data_quality
    if not quality:
        return

    print("DATA QUALITY")
    print("-" * 78)
    print(
        f"{quality['readings']} readings, {quality['unique_readings']} unique timestamps | "
        f"{quality['out_of_order_readings']} out of order | "
        f"{quality['duplicate_timestamps']} duplicate timestamps collapsed | "
        f"{quality['missing_samples']} missing expected samples | "
        f"{quality['gaps_over_tolerance']} gap(s) over tolerance"
    )

    notable = [
        truck
        for truck in quality["trucks"]
        if truck["out_of_order_readings"]
        or truck["duplicate_timestamps"]
        or truck["missing_samples"]
        or truck["gaps"]
    ]
    for truck in notable:
        notes: list[str] = []
        if truck["out_of_order_readings"]:
            notes.append(f"{truck['out_of_order_readings']} out-of-order reading(s)")
        if truck["duplicate_timestamps"]:
            notes.append(f"{truck['duplicate_timestamps']} duplicate timestamp(s)")
        if truck["missing_samples"]:
            notes.append(f"{truck['missing_samples']} missing sample(s)")
        if truck["gaps"]:
            gap_text = ", ".join(
                f"{gap['minutes']:.0f} min "
                f"({'within' if gap['within_tolerance'] else 'OVER'} tolerance)"
                for gap in truck["gaps"]
            )
            notes.append(f"gaps: {gap_text}")
        print(f"- {truck['truck_id']}: " + "; ".join(notes))

    if not notable:
        print("- No ordering, duplicate, or gap issues detected.")
    print()


def _parse_number_list(raw: str | None, label: str) -> list[float] | None:
    if not raw:
        return None
    values: list[float] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            value = float(chunk)
        except ValueError as exc:
            raise ValueError(f"{label} must be comma-separated numbers: {raw!r}") from exc
        if value <= 0:
            raise ValueError(f"{label} values must be positive")
        values.append(value)
    if not values:
        raise ValueError(f"{label} did not contain any numbers")
    return sorted(set(values))


def _variant_policy(policy: Policy, threshold_c: float, duration_minutes: float) -> Policy:
    """Same policy with the two decision knobs moved.

    Lowering the breach threshold can collide with the clear threshold, so the
    clear threshold follows down while keeping the existing hysteresis gap.
    """
    data = policy.to_dict()
    data["threshold_c"] = threshold_c
    data["clear_threshold_c"] = min(policy.clear_threshold_c, round(threshold_c - 0.5, 3))
    data["min_duration_minutes"] = duration_minutes
    return Policy.from_mapping(data)


def _variant_book(
    policy_book: PolicyBook, threshold_c: float, duration_minutes: float
) -> PolicyBook:
    return PolicyBook(
        default=_variant_policy(policy_book.default, threshold_c, duration_minutes),
        trucks={
            truck_id: _variant_policy(policy, threshold_c, duration_minutes)
            for truck_id, policy in policy_book.trucks.items()
        },
    )


def _print_incident(index: int, incident: Incident) -> None:
    print(
        f"[{index}] {incident.truck_id}  {incident.severity:<8} {incident.status:<8} "
        f"{format_clock(incident.run_start)}-{format_clock(incident.last_breach_at)} UTC "
        f"({format_duration(incident.exposure_minutes)})"
    )
    print(
        f"    peak {incident.peak_c:.1f} C | {incident.breach_count}/{incident.expected_samples} "
        f"expected breach readings ({incident.coverage:.0%} coverage) | "
        f"max gap {incident.max_gap_minutes:.0f} min"
    )
    print(f"    why: {incident.summary}")
    print()


def cmd_replay(args: argparse.Namespace) -> int:
    settings = load_settings()
    data_path = Path(args.data) if args.data else settings.dataset_path
    readings = load_readings(data_path)
    store, pipeline, policy_book, db_path = _build_pipeline(args)

    try:
        analysis = analyze_readings(readings, policy_book)
        if args.reset:
            store.reset()
        result = pipeline.ingest(readings)

        payload: dict[str, Any] = {
            "dataset": str(data_path),
            "database": str(db_path),
            "policy": policy_book.to_dict(),
            "analysis": analysis.to_dict(),
            "ingest": result.to_dict(),
        }

        if args.format == "json":
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return 0

        print("Nimbus Freight cold-chain replay")
        print(f"Dataset: {data_path}")
        print(f"Readings: {analysis.readings_count} across {len(analysis.trucks)} trucks")
        print(f"Policy: {_policy_line(policy_book)}")
        print()

        _print_quality(analysis)

        print(f"SUSTAINED INCIDENTS ({len(analysis.incidents)})")
        print("-" * 78)
        if analysis.incidents:
            for index, incident in enumerate(analysis.incidents, start=1):
                _print_incident(index, incident)
        else:
            print("None. No truck met the sustained-excursion criteria.")
            print()

        print(f"SUPPRESSED NOISE ({len(analysis.suppressed)})")
        print("-" * 78)
        if analysis.suppressed:
            for episode in analysis.suppressed:
                print(
                    f"- {episode.truck_id}  {format_clock(episode.start)}-"
                    f"{format_clock(episode.end)} UTC  "
                    f"({format_duration(episode.duration_minutes)}, "
                    f"{episode.breach_count} breach reading(s), peak {episode.peak_c:.1f} C)"
                )
                print(f"  reason: {episode.reason}")
        else:
            print("None.")

        incident_trucks = {incident.truck_id for incident in analysis.incidents}
        suppressed_trucks = {episode.truck_id for episode in analysis.suppressed}
        quiet_trucks = [
            truck for truck in analysis.trucks
            if truck not in incident_trucks and truck not in suppressed_trucks
        ]
        if quiet_trucks:
            print()
            print(f"NO THRESHOLD BREACH: {', '.join(quiet_trucks)}")

        counts = Counter(event["event_type"] for event in result.new_events)
        print()
        print("ALERT LIFECYCLE")
        print("-" * 78)
        print(
            "New events on this ingest: "
            f"{counts.get('OPENED', 0)} opened, "
            f"{counts.get('RESOLVED', 0)} resolved, "
            f"{counts.get('SUPERSEDED', 0)} superseded"
        )
        if counts:
            print("Replay again without --reset to verify deduplication (expected: 0 new events).")
        else:
            print("No new events: idempotent replay; these incidents were already recorded.")
        print(
            f"Database: {db_path} | incidents={result.stats.get('open_incidents', 0)} open, "
            f"{result.stats.get('resolved_incidents', 0)} resolved"
        )
        return 0
    finally:
        store.close()


def cmd_alerts(args: argparse.Namespace) -> int:
    store, _pipeline, _policy, db_path = _build_pipeline(args)
    try:
        incidents = store.list_incidents(
            status=args.status, truck_id=args.truck, limit=args.limit
        )
        if not incidents:
            print(f"No incidents found in {db_path} for status={args.status}.")
            return 0
        for index, incident in enumerate(incidents, start=1):
            print(
                f"[{index}] {incident['incident_id']}  {incident['truck_id']}  "
                f"{incident['severity']}  {incident['status']}  "
                f"{format_clock(incident['run_start'])}-{format_clock(incident['last_breach_at'])} UTC"
            )
            print(f"    {incident['summary']}")
        return 0
    finally:
        store.close()


def cmd_stats(args: argparse.Namespace) -> int:
    store, _pipeline, policy_book, db_path = _build_pipeline(args)
    try:
        stats = store.stats()
        print(f"Database: {db_path}")
        print(f"Policy: {_policy_line(policy_book)}")
        for key, value in stats.items():
            print(f"{key}: {value}")
        return 0
    finally:
        store.close()


def cmd_what_if(args: argparse.Namespace) -> int:
    """Sweep threshold x duration so the policy trade-off is visible, not implied."""
    settings = load_settings()
    data_path = Path(args.data) if args.data else settings.dataset_path
    readings = load_readings(data_path)
    policy_book = load_policy_book(args.policy or os.environ.get("COLDCHAIN_POLICY_FILE"))
    baseline = policy_book.default

    thresholds = _parse_number_list(args.thresholds, "--thresholds") or sorted(
        round(baseline.threshold_c + offset, 3)
        for offset in (-1.0, -0.5, 0.0, 0.5, 1.0)
    )
    durations = _parse_number_list(args.durations, "--durations") or sorted(
        {
            max(5.0, baseline.min_duration_minutes + offset)
            for offset in (-10.0, -5.0, 0.0, 5.0, 10.0)
        }
    )

    rows: list[dict[str, Any]] = []
    for threshold in thresholds:
        for duration in durations:
            analysis = analyze_readings(
                readings, _variant_book(policy_book, threshold, duration)
            )
            rows.append(
                {
                    "threshold_c": threshold,
                    "min_duration_minutes": duration,
                    "incident_count": len(analysis.incidents),
                    "suppressed_count": len(analysis.suppressed),
                    "trucks": sorted({incident.truck_id for incident in analysis.incidents}),
                }
            )

    payload = {
        "dataset": str(data_path),
        "readings": len(readings),
        "baseline": policy_book.to_dict(),
        "thresholds": thresholds,
        "durations": durations,
        "results": rows,
    }

    if args.format == "json":
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print("Policy what-if: sustained-incident sensitivity")
    print(f"Dataset: {data_path} ({len(readings)} readings)")
    print(f"Baseline: {_policy_line(policy_book)}")
    print()
    print("Cells show sustained incident counts; details below list affected trucks.")
    print()

    header = "threshold \\ duration".ljust(22)
    header += "".join(f">= {duration:.0f} min".rjust(12) for duration in durations)
    print(header)
    print("-" * len(header))

    grid = {
        (row["threshold_c"], row["min_duration_minutes"]): row for row in rows
    }
    for threshold in thresholds:
        line = f"> {threshold:.1f} C".ljust(22)
        line += "".join(
            str(grid[(threshold, duration)]["incident_count"]).rjust(12)
            for duration in durations
        )
        print(line)

    print()
    print("TRUCK SETS (combinations with at least one incident)")
    print("-" * 78)
    for row in rows:
        if not row["incident_count"]:
            continue
        trucks = ", ".join(row["trucks"])
        print(
            f"- > {row['threshold_c']:.1f} C / >= {row['min_duration_minutes']:.0f} min: "
            f"{row['incident_count']} incident(s) [{trucks}], "
            f"{row['suppressed_count']} suppressed"
        )
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed. Run: pip install -r requirements.txt", file=sys.stderr)
        return 1

    settings = load_settings()
    if args.db:
        os.environ["COLDCHAIN_DB"] = str(args.db)
    else:
        os.environ.setdefault("COLDCHAIN_DB", str(settings.db_path))
    if args.data:
        os.environ["COLDCHAIN_DATASET"] = str(args.data)
    else:
        os.environ.setdefault("COLDCHAIN_DATASET", str(settings.dataset_path))
    if args.policy:
        os.environ["COLDCHAIN_POLICY_FILE"] = str(args.policy)
    if args.demo:
        os.environ["COLDCHAIN_DEMO_MODE"] = "true"
        os.environ["COLDCHAIN_SEED_DEMO"] = "true"
    if args.llm:
        os.environ["COLDCHAIN_LLM_ENABLED"] = "true"

    print(f"Nimbus Freight cold-chain service: http://{args.host}:{args.port}")
    print("API docs: /docs | dashboard: / | health: /healthz")
    uvicorn.run(
        "coldchain.api:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="coldchain",
        description="Nimbus Freight sustained cold-chain excursion alerting",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(item: argparse.ArgumentParser, data: bool = False) -> None:
        item.add_argument("--db", help="SQLite database path (default: data/coldchain.db)")
        item.add_argument("--policy", help="Optional JSON policy file with per-truck overrides")
        item.add_argument("--llm", action="store_true", help="Use the optional LLM summary enhancement")
        if data:
            item.add_argument("--data", help="Sensor readings JSON file")

    replay = sub.add_parser("replay", help="Replay a dataset end-to-end and print alerts")
    add_common(replay, data=True)
    replay.add_argument("--reset", action="store_true", help="Clear existing data before replay")
    replay.add_argument(
        "--format", choices=("text", "json"), default="text", help="Output format"
    )
    replay.set_defaults(func=cmd_replay)

    alerts = sub.add_parser("alerts", help="List stored incidents")
    add_common(alerts)
    alerts.add_argument(
        "--status",
        default="all",
        choices=("all", "open", "resolved", "superseded"),
        help="Filter by status",
    )
    alerts.add_argument("--truck", help="Filter by truck ID")
    alerts.add_argument("--limit", type=int, default=100, help="Maximum incidents to show")
    alerts.set_defaults(func=cmd_alerts)

    stats = sub.add_parser("stats", help="Show database and policy summary")
    add_common(stats)
    stats.set_defaults(func=cmd_stats)

    what_if = sub.add_parser(
        "what-if",
        help="Sweep breach threshold and sustained window to show alert sensitivity",
    )
    what_if.add_argument("--data", help="Sensor readings JSON file")
    what_if.add_argument("--policy", help="Optional JSON policy file with per-truck overrides")
    what_if.add_argument(
        "--thresholds",
        help="Comma-separated breach thresholds in C (default: baseline +/- 1.0 in 0.5 steps)",
    )
    what_if.add_argument(
        "--durations",
        help="Comma-separated sustained windows in minutes (default: baseline +/- 10 in 5-min steps)",
    )
    what_if.add_argument(
        "--format", choices=("text", "json"), default="text", help="Output format"
    )
    what_if.set_defaults(func=cmd_what_if)

    serve = sub.add_parser("serve", help="Run the HTTP API and dashboard")
    add_common(serve, data=True)
    serve.add_argument("--host", default="0.0.0.0", help="Bind host")
    serve.add_argument("--port", type=int, default=8000, help="Bind port")
    serve.add_argument("--demo", action="store_true", help="Enable demo mode and seed the dataset")
    serve.add_argument("--reload", action="store_true", help="Enable Uvicorn auto-reload")
    serve.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
