from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from coldchain.config import Policy, PolicyBook
from coldchain.dataset import load_readings
from coldchain.engine import analyze_readings
from coldchain.models import Reading
from coldchain.pipeline import Pipeline
from coldchain.store import AlertStore
from coldchain.summarizer import Summarizer

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "sensor_readings.json"


def reading(truck_id: str, hhmm: str, temperature_c: float) -> Reading:
    return Reading.from_mapping(
        {
            "truck_id": truck_id,
            "timestamp": f"2026-07-20T{hhmm}:00",
            "temperature_c": temperature_c,
            "humidity_pct": 55,
        }
    )


def policy(**overrides):
    data = Policy().to_dict()
    data.update(overrides)
    return Policy.from_mapping(data)


def alerting_run(truck_id: str = "T-99", start: str = "08:00"):
    """Five readings from 08:00 to 08:20, all above 8 C."""
    return [
        reading(truck_id, "08:00", 8.5),
        reading(truck_id, "08:05", 8.6),
        reading(truck_id, "08:10", 8.7),
        reading(truck_id, "08:15", 8.8),
        reading(truck_id, "08:20", 8.9),
    ]


class DatasetGoldenTests(unittest.TestCase):
    def test_dataset_produces_expected_sustained_incidents(self):
        result = analyze_readings(load_readings(DATA), Policy())
        by_truck = {incident.truck_id: incident for incident in result.incidents}

        self.assertEqual(set(by_truck), {"T-01", "T-04", "T-05"})
        self.assertEqual(result.readings_count, 54)
        self.assertEqual(result.trucks, ["T-01", "T-02", "T-03", "T-04", "T-05"])

        t01 = by_truck["T-01"]
        self.assertEqual(t01.status, "RESOLVED")
        self.assertEqual(t01.severity, "HIGH")
        self.assertEqual(t01.breach_count, 7)
        self.assertEqual(t01.expected_samples, 7)
        self.assertAlmostEqual(t01.exposure_minutes, 30)
        self.assertAlmostEqual(t01.peak_c, 9.6)
        self.assertEqual(t01.resolved_at.strftime("%H:%M"), "08:50")

        t04 = by_truck["T-04"]
        self.assertEqual(t04.status, "OPEN")
        self.assertEqual(t04.breach_count, 4)
        self.assertEqual(t04.expected_samples, 5)
        self.assertAlmostEqual(t04.exposure_minutes, 20)
        self.assertAlmostEqual(t04.max_gap_minutes, 10)
        self.assertAlmostEqual(t04.coverage, 0.8)
        self.assertAlmostEqual(t04.peak_c, 9.1)

        t05 = by_truck["T-05"]
        self.assertEqual(t05.status, "RESOLVED")
        self.assertAlmostEqual(t05.exposure_minutes, 30)

    def test_dataset_suppresses_blips_and_ignores_t03(self):
        result = analyze_readings(load_readings(DATA), Policy())
        by_truck = {(episode.truck_id, episode.start.strftime("%H:%M")): episode
                    for episode in result.suppressed}

        self.assertEqual(len(result.suppressed), 2)
        self.assertIn(("T-02", "08:10"), by_truck)
        self.assertIn(("T-04", "08:05"), by_truck)
        self.assertEqual(by_truck[("T-02", "08:10")].reason_code, "brief_excursion")
        self.assertEqual(by_truck[("T-04", "08:05")].reason_code, "brief_excursion")
        self.assertNotIn("T-03", {incident.truck_id for incident in result.incidents})

    def test_summary_contains_operational_facts(self):
        result = analyze_readings(load_readings(DATA), Policy())
        t01 = next(incident for incident in result.incidents if incident.truck_id == "T-01")
        self.assertIn("T-01", t01.summary)
        self.assertIn("30 min", t01.summary)
        self.assertIn("9.6 C", t01.summary)
        self.assertIn("cleared at 08:50", t01.summary)


class OrderingAndGapTests(unittest.TestCase):
    def test_out_of_order_readings_are_sorted(self):
        dataset = load_readings(DATA)
        t05 = [item for item in dataset if item.truck_id == "T-05"]
        expected = analyze_readings(t05, Policy())
        actual = analyze_readings(list(reversed(t05)), Policy())

        self.assertEqual(len(expected.incidents), 1)
        self.assertEqual(len(actual.incidents), 1)
        self.assertEqual(
            expected.incidents[0].to_dict()["incident_id"],
            actual.incidents[0].to_dict()["incident_id"],
        )
        self.assertEqual(
            expected.incidents[0].exposure_minutes,
            actual.incidents[0].exposure_minutes,
        )

    def test_single_spike_is_suppressed(self):
        readings = [
            reading("T-99", "08:00", 6.0),
            reading("T-99", "08:05", 8.9),
            reading("T-99", "08:10", 6.2),
        ]
        result = analyze_readings(readings, Policy())
        self.assertEqual(result.incidents, [])
        self.assertEqual(len(result.suppressed), 1)
        self.assertEqual(result.suppressed[0].reason_code, "brief_excursion")
        self.assertEqual(result.suppressed[0].breach_count, 1)

    def test_large_gap_breaks_a_pending_run(self):
        readings = [
            reading("T-99", "08:00", 8.5),
            reading("T-99", "08:05", 8.6),
            reading("T-99", "08:10", 8.7),
            # 25-minute gap exceeds the 15-minute tolerance; the first run is
            # not yet sustained, so it must not be counted as continuous.
            reading("T-99", "08:35", 8.8),
        ]
        result = analyze_readings(readings, Policy())
        self.assertEqual(result.incidents, [])
        codes = {episode.reason_code for episode in result.suppressed}
        self.assertEqual(codes, {"data_gap", "insufficient_data"})

    def test_active_incident_survives_large_gap_and_can_resolve(self):
        readings = alerting_run() + [
            reading("T-99", "08:45", 9.1),  # 25-minute gap after active alert
            reading("T-99", "08:50", 7.0),
            reading("T-99", "08:55", 7.0),
        ]
        result = analyze_readings(readings, Policy())
        self.assertEqual(len(result.incidents), 1)
        incident = result.incidents[0]
        self.assertEqual(incident.status, "RESOLVED")
        self.assertEqual(len(incident.gaps), 1)
        self.assertAlmostEqual(incident.gaps[0].minutes, 25)
        self.assertEqual(incident.resolved_at.strftime("%H:%M"), "08:55")

    def test_sparse_breach_readings_do_not_alert(self):
        readings = [
            reading("T-99", "08:00", 8.5),
            reading("T-99", "08:05", 7.8),
            reading("T-99", "08:10", 7.8),
            reading("T-99", "08:15", 7.8),
            reading("T-99", "08:20", 7.8),
            reading("T-99", "08:25", 7.8),
            reading("T-99", "08:30", 8.5),
        ]
        result = analyze_readings(readings, Policy())
        self.assertEqual(result.incidents, [])
        self.assertEqual(result.suppressed[0].reason_code, "insufficient_data")


class HysteresisAndRecoveryTests(unittest.TestCase):
    def test_hysteresis_prevents_flapping_and_duplicate_alerts(self):
        readings = alerting_run() + [
            reading("T-99", "08:25", 7.8),  # in the 7.5-8.0 hysteresis band
            reading("T-99", "08:30", 7.7),
            reading("T-99", "08:35", 8.2),  # same incident, not a second alert
            reading("T-99", "08:40", 7.0),
            reading("T-99", "08:45", 7.0),
        ]
        result = analyze_readings(readings, Policy())
        self.assertEqual(len(result.incidents), 1)
        incident = result.incidents[0]
        self.assertEqual(incident.status, "RESOLVED")
        self.assertEqual(incident.breach_count, 6)
        self.assertEqual(incident.resolved_at.strftime("%H:%M"), "08:45")

    def test_one_clear_reading_is_not_enough_to_resolve(self):
        readings = alerting_run() + [reading("T-99", "08:25", 7.0)]
        result = analyze_readings(readings, Policy())
        self.assertEqual(len(result.incidents), 1)
        self.assertEqual(result.incidents[0].status, "OPEN")

    def test_two_clear_readings_resolve(self):
        readings = alerting_run() + [
            reading("T-99", "08:25", 7.0),
            reading("T-99", "08:30", 7.0),
        ]
        result = analyze_readings(readings, Policy())
        self.assertEqual(result.incidents[0].status, "RESOLVED")

    def test_per_truck_policy_override_is_respected(self):
        dataset = load_readings(DATA)
        book = PolicyBook(
            default=Policy(),
            trucks={"T-04": policy(min_duration_minutes=25)},
        )
        result = analyze_readings(dataset, book)
        self.assertEqual(
            {incident.truck_id for incident in result.incidents}, {"T-01", "T-05"}
        )


class PersistenceTests(unittest.TestCase):
    def test_pipeline_replay_is_idempotent(self):
        dataset = load_readings(DATA)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AlertStore(Path(temp_dir) / "test.db")
            try:
                pipeline = Pipeline(store, PolicyBook(default=Policy()))
                first = pipeline.ingest(dataset)
                second = pipeline.ingest(dataset)

                self.assertEqual(
                    [event["event_type"] for event in first.new_events].count("OPENED"), 3
                )
                self.assertEqual(
                    [event["event_type"] for event in first.new_events].count("RESOLVED"), 2
                )
                self.assertEqual(second.new_events, [])
                self.assertEqual(len(store.list_incidents(status="all")), 3)
                self.assertEqual(store.stats()["readings"], 54)
            finally:
                store.close()

    def test_late_out_of_order_ingest_recomputes_without_duplicate_events(self):
        dataset = load_readings(DATA)
        with tempfile.TemporaryDirectory() as temp_dir:
            store = AlertStore(Path(temp_dir) / "test.db")
            try:
                pipeline = Pipeline(store, PolicyBook(default=Policy()))
                truck_readings = [item for item in dataset if item.truck_id == "T-05"]
                pipeline.ingest(list(reversed(truck_readings)))
                first_open = [
                    incident
                    for incident in store.list_incidents(status="all")
                    if incident["truck_id"] == "T-05"
                ]
                self.assertEqual(len(first_open), 1)

                # A retry in canonical order must update, not duplicate.
                result = pipeline.ingest(truck_readings)
                self.assertEqual(result.new_events, [])
                incidents = [
                    incident
                    for incident in store.list_incidents(status="all")
                    if incident["truck_id"] == "T-05"
                ]
                self.assertEqual(len(incidents), 1)
            finally:
                store.close()


class SummarizerTests(unittest.TestCase):
    def test_template_summary_never_requires_an_api_key(self):
        result = analyze_readings(alerting_run(), Policy())
        summary = Summarizer().summarize(result.incidents[0])
        self.assertIn("T-99", summary)
        self.assertIn("20 min", summary)


if __name__ == "__main__":
    unittest.main()
