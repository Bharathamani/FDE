from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coldchain.config import Policy, PolicyBook, Settings

try:
    from fastapi.testclient import TestClient

    from coldchain.api import create_app

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover - optional runtime dependency
    FASTAPI_AVAILABLE = False
    TestClient = None
    create_app = None

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "sensor_readings.json"


def settings_for(db_path: Path, demo: bool = False, seed: bool = False) -> Settings:
    return Settings(
        db_path=db_path,
        dataset_path=DATA,
        policy_book=PolicyBook(default=Policy()),
        demo_mode=demo,
        seed_demo=seed,
    )


def dataset_payload() -> list[dict]:
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    return raw["readings"]


@unittest.skipUnless(FASTAPI_AVAILABLE, "FastAPI/httpx are not installed")
class ApiTests(unittest.TestCase):
    def test_ingest_list_dashboard_and_idempotency(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(settings=settings_for(Path(temp_dir) / "api.db"))
            with TestClient(app) as client:
                health = client.get("/healthz")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json()["status"], "ok")

                response = client.post(
                    "/api/v1/readings/batch", json={"readings": dataset_payload()}
                )
                self.assertEqual(response.status_code, 200, response.text)
                first = response.json()
                self.assertEqual(first["stats"]["readings"], 54)
                opened = [
                    event for event in first["new_events"] if event["event_type"] == "OPENED"
                ]
                self.assertEqual(len(opened), 3)

                incidents = client.get("/api/v1/incidents?status=all").json()
                self.assertEqual(len(incidents), 3)
                self.assertEqual({item["truck_id"] for item in incidents}, {"T-01", "T-04", "T-05"})

                detail = client.get(f"/api/v1/incidents/{incidents[0]['incident_id']}")
                self.assertEqual(detail.status_code, 200)
                self.assertIn("summary", detail.json())

                retry = client.post(
                    "/api/v1/readings/batch", json={"readings": dataset_payload()}
                )
                self.assertEqual(retry.status_code, 200)
                self.assertEqual(retry.json()["new_events"], [])

                dashboard = client.get("/api/v1/dashboard").json()
                self.assertEqual(dashboard["stats"]["readings"], 54)
                self.assertFalse(dashboard["demo_mode"])
                self.assertEqual(len(dashboard["incidents"]), 3)
                self.assertEqual(len(dashboard["trucks"]), 5)

                replay = client.post("/api/v1/demo/replay?reset=true")
                self.assertEqual(replay.status_code, 403)

    def test_demo_replay_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(
                settings=settings_for(Path(temp_dir) / "demo.db", demo=True, seed=True)
            )
            with TestClient(app) as client:
                dashboard = client.get("/api/v1/dashboard").json()
                self.assertEqual(dashboard["stats"]["readings"], 54)
                self.assertEqual(len(dashboard["incidents"]), 3)

                response = client.post("/api/v1/demo/replay?reset=true")
                self.assertEqual(response.status_code, 200, response.text)
                payload = response.json()
                self.assertEqual(len(payload["analysis"]["incidents"]), 3)
                self.assertEqual(len(payload["analysis"]["suppressed"]), 2)
                self.assertEqual(payload["analysis"]["data_quality"]["readings"], 54)
                self.assertEqual(
                    payload["analysis"]["data_quality"]["out_of_order_readings"], 5
                )
                self.assertEqual(
                    [event["event_type"] for event in payload["ingest"]["new_events"]].count(
                        "OPENED"
                    ),
                    3,
                )

    def test_invalid_reading_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(settings=settings_for(Path(temp_dir) / "invalid.db"))
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/readings",
                    json={
                        "truck_id": "T-99",
                        "timestamp": "2026-07-20T08:00:00Z",
                        "temperature_c": 999,
                    },
                )
                self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
