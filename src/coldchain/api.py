"""FastAPI service and dashboard API."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from .config import Settings, load_settings
from .models import Reading
from .pipeline import Pipeline
from .store import AlertStore
from .summarizer import Summarizer
from .timeutil import format_timestamp

logger = logging.getLogger(__name__)
STATIC_INDEX = Path(__file__).parent / "static" / "index.html"


class ReadingIn(BaseModel):
    truck_id: str = Field(..., min_length=1, max_length=64)
    timestamp: datetime
    temperature_c: float
    humidity_pct: float | None = Field(default=None, ge=0, le=100)

    def to_reading(self) -> Reading:
        return Reading.from_mapping(
            {
                "truck_id": self.truck_id,
                "timestamp": self.timestamp,
                "temperature_c": self.temperature_c,
                "humidity_pct": self.humidity_pct,
            }
        )


class BatchIn(BaseModel):
    readings: list[ReadingIn] = Field(..., min_length=1)


def create_app(settings: Settings | None = None, store: AlertStore | None = None) -> FastAPI:
    settings = settings or load_settings()
    store = store or AlertStore(settings.db_path)
    summarizer = Summarizer(
        llm_enabled=settings.llm_enabled,
        api_key=settings.openai_api_key,
        model=settings.llm_model,
        base_url=settings.openai_base_url,
    )
    pipeline = Pipeline(
        store=store,
        policy_book=settings.policy_book,
        summarizer=summarizer,
        webhook_url=settings.webhook_url,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.seed_demo and store.is_empty():
            if settings.dataset_path.exists():
                result = pipeline.replay_file(settings.dataset_path)
                logger.info(
                    "Seeded demo dataset: %s readings, %s new events",
                    result.readings_received,
                    len(result.new_events),
                )
            else:
                logger.warning("Seed dataset not found: %s", settings.dataset_path)
        yield
        store.close()

    app = FastAPI(
        title="Nimbus Freight Cold-Chain Alerting",
        description=(
            "Event-time cold-chain monitor that alerts only on sustained temperature "
            "excursions and deduplicates an ongoing incident."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.store = store
    app.state.pipeline = pipeline

    @app.get("/", include_in_schema=False)
    def dashboard() -> Response:
        if STATIC_INDEX.exists():
            return FileResponse(STATIC_INDEX, media_type="text/html")
        return HTMLResponse("<h1>Nimbus cold-chain alerting API</h1>", status_code=200)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/healthz")
    def healthz() -> dict:
        stats = store.stats()
        return {
            "status": "ok",
            "readings": stats["readings"],
            "open_incidents": stats["open_incidents"],
        }

    @app.get("/api/v1/dashboard")
    def dashboard_data() -> dict:
        return {
            "generated_at": format_timestamp(datetime.now(timezone.utc)),
            "demo_mode": settings.demo_mode,
            "policy": settings.policy_book.to_dict(),
            "stats": store.stats(),
            "incidents": store.list_incidents(status="all", limit=100),
            "trucks": store.truck_overview(),
            "recent_events": store.list_events(limit=20),
        }

    @app.get("/api/v1/stats")
    def stats() -> dict:
        return store.stats()

    @app.get("/api/v1/policy")
    def policy() -> dict:
        return settings.policy_book.to_dict()

    @app.get("/api/v1/trucks")
    def trucks() -> list[dict]:
        return store.truck_overview()

    @app.get("/api/v1/incidents")
    def incidents(
        status: str = Query(default="all", description="all, open, resolved, or superseded"),
        truck_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict]:
        try:
            return store.list_incidents(status=status, truck_id=truck_id, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/alerts")
    def alerts(
        status: str = Query(default="open", description="all, open, resolved, or superseded"),
        truck_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict]:
        try:
            return store.list_incidents(status=status, truck_id=truck_id, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/v1/incidents/{incident_id}")
    def incident_detail(incident_id: str) -> dict:
        incident = store.get_incident(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="Incident not found")
        return incident

    @app.get("/api/v1/events")
    def events(limit: int = Query(default=20, ge=1, le=200)) -> list[dict]:
        return store.list_events(limit=limit)

    @app.post("/api/v1/readings")
    def ingest_reading(reading: ReadingIn) -> dict:
        try:
            result = pipeline.ingest([reading.to_reading()])
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return result.to_dict()

    @app.post("/api/v1/readings/batch")
    def ingest_batch(batch: BatchIn) -> dict:
        if len(batch.readings) > 10_000:
            raise HTTPException(status_code=413, detail="Batch is limited to 10,000 readings")
        try:
            parsed = [reading.to_reading() for reading in batch.readings]
            result = pipeline.ingest(parsed)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return result.to_dict()

    @app.post("/api/v1/demo/replay")
    def demo_replay(
        reset: bool = Query(default=True, description="Clear the demo database before replay"),
    ) -> dict:
        if not settings.demo_mode:
            raise HTTPException(
                status_code=403,
                detail="Demo replay is disabled. Set COLDCHAIN_DEMO_MODE=true to enable it.",
            )
        if not settings.dataset_path.exists():
            raise HTTPException(status_code=404, detail="Demo dataset not found")
        analysis = pipeline.analyze_file(settings.dataset_path)
        result = pipeline.replay_file(settings.dataset_path, reset=reset)
        return {"ingest": result.to_dict(), "analysis": analysis.to_dict()}

    return app
