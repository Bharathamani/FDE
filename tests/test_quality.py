from __future__ import annotations

import contextlib
import io
import json
import unittest
from pathlib import Path

from coldchain.cli import main
from coldchain.config import Policy
from coldchain.dataset import load_readings
from coldchain.engine import analyze_readings
from coldchain.models import Reading
from coldchain.quality import analyze_data_quality

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "sensor_readings.json"


def reading(truck_id: str, hhmm: str, temperature_c: float) -> Reading:
    return Reading.from_mapping(
        {
            "truck_id": truck_id,
            "timestamp": f"2026-07-20T{hhmm}:00",
            "temperature_c": temperature_c,
        }
    )


class DatasetQualityTests(unittest.TestCase):
    def test_dataset_quality_summary(self):
        report = analyze_data_quality(load_readings(DATA), Policy())

        self.assertEqual(report.readings, 54)
        self.assertEqual(report.unique_readings, 54)
        self.assertEqual(report.duplicate_timestamps, 0)
        self.assertEqual(report.out_of_order_readings, 5)
        self.assertEqual(report.gaps_over_tolerance, 0)
        self.assertEqual(report.missing_samples, 1)

        by_truck = {truck.truck_id: truck for truck in report.trucks}
        self.assertEqual(by_truck["T-05"].out_of_order_readings, 5)
        self.assertEqual(by_truck["T-05"].missing_samples, 0)

        t04 = by_truck["T-04"]
        self.assertEqual(t04.missing_samples, 1)
        self.assertAlmostEqual(t04.largest_gap_minutes, 10)
        self.assertEqual(len(t04.gaps), 1)
        self.assertTrue(t04.gaps[0].within_tolerance)
        self.assertEqual(t04.gaps[0].start.strftime("%H:%M"), "08:30")
        self.assertEqual(t04.gaps[0].end.strftime("%H:%M"), "08:40")

        self.assertEqual(by_truck["T-01"].missing_samples, 0)
        self.assertEqual(by_truck["T-01"].out_of_order_readings, 0)

    def test_analysis_result_carries_quality_report(self):
        result = analyze_readings(load_readings(DATA), Policy())
        self.assertIsNotNone(result.data_quality)
        self.assertEqual(result.data_quality["readings"], 54)
        self.assertEqual(result.data_quality["out_of_order_readings"], 5)

    def test_duplicate_timestamps_are_reported_and_collapsed(self):
        readings = [
            reading("T-99", "08:00", 8.5),
            reading("T-99", "08:05", 8.6),
            reading("T-99", "08:05", 8.7),  # sensor retry / duplicate timestamp
            reading("T-99", "08:10", 8.8),
            reading("T-99", "08:15", 8.9),
            reading("T-99", "08:20", 9.0),
        ]
        report = analyze_data_quality(readings, Policy())
        self.assertEqual(report.readings, 6)
        self.assertEqual(report.unique_readings, 5)
        self.assertEqual(report.duplicate_timestamps, 1)
        self.assertEqual(report.missing_samples, 0)

        analysis = analyze_readings(readings, Policy())
        self.assertEqual(analysis.readings_count, 6)
        self.assertEqual(len(analysis.incidents), 1)
        self.assertEqual(analysis.incidents[0].breach_count, 5)

    def test_gap_over_tolerance_is_reported(self):
        readings = [
            reading("T-99", "08:00", 6.0),
            reading("T-99", "08:30", 6.5),  # 30-minute gap > 15-minute tolerance
        ]
        report = analyze_data_quality(readings, Policy())
        self.assertEqual(report.gaps_over_tolerance, 1)
        self.assertEqual(report.missing_samples, 5)

        truck = report.trucks[0]
        self.assertEqual(len(truck.gaps), 1)
        self.assertFalse(truck.gaps[0].within_tolerance)
        self.assertAlmostEqual(truck.gaps[0].minutes, 30)


class WhatIfTests(unittest.TestCase):
    def test_what_if_json_sweeps_threshold_and_duration(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(
                [
                    "what-if",
                    "--data",
                    str(DATA),
                    "--thresholds",
                    "8.0,8.5",
                    "--durations",
                    "15,25",
                    "--format",
                    "json",
                ]
            )

        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["readings"], 54)
        self.assertEqual(len(payload["results"]), 4)

        grid = {
            (row["threshold_c"], row["min_duration_minutes"]): row
            for row in payload["results"]
        }
        self.assertEqual(grid[(8.0, 15.0)]["trucks"], ["T-01", "T-04", "T-05"])
        self.assertEqual(grid[(8.0, 25.0)]["trucks"], ["T-01", "T-05"])
        self.assertEqual(grid[(8.5, 15.0)]["trucks"], ["T-01", "T-04", "T-05"])


if __name__ == "__main__":
    unittest.main()
