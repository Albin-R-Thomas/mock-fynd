import asyncio
import json
import logging
import os
import random
from collections import Counter, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from redis.asyncio import Redis
from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, create_engine, insert, select, text

log = logging.getLogger("operations")
STATUSES = ("open", "packed", "out_for_delivery", "delivered")
metadata = MetaData()
events_table = Table("order_events", metadata,
    Column("id", Integer, primary_key=True), Column("event_id", String(100), unique=True),
    Column("payload", JSON, nullable=False), Column("outcome", String(30)))
snapshots_table = Table("dashboard_snapshots", metadata,
    Column("id", Integer, primary_key=True), Column("payload", JSON, nullable=False))


def now():
    return datetime.now(timezone.utc)


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1, max_length=100)
    order_id: str = Field(min_length=1, max_length=100)
    store_id: str = Field(min_length=1, max_length=100)
    status: Literal["open", "packed", "out_for_delivery", "delivered"]
    occurred_at: datetime = Field(default_factory=now)

    @field_validator("occurred_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        if value > now() + timedelta(seconds=5):
            raise ValueError("occurred_at cannot be in the future")
        return value.astimezone(timezone.utc)


class MockConfig(BaseModel):
    events_per_second: int = Field(default=50, ge=1, le=2000)


class Operations:
    def __init__(self):
        url = os.getenv("DATABASE_URL", "sqlite:///./operations.db")
        self.engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
        self.redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                                    socket_connect_timeout=0.3, socket_timeout=0.3)
        self.lock = asyncio.Lock()
        self.orders = {}
        self.counts = {}
        self.recent = deque(maxlen=1000)
        self.latest = {}
        self.redis_ok = False
        self.last_error = None
        self.mock_enabled = os.getenv("MOCK_AUTOSTART", "true").lower() == "true"
        self.rate = int(os.getenv("MOCK_EVENTS_PER_SECOND", "50"))

    def apply(self, payload):
        previous = self.orders.get(payload["order_id"])
        counts = self.counts.setdefault(payload["store_id"], Counter({s: 0 for s in STATUSES}))
        if previous:
            counts[previous["status"]] -= 1
        counts[payload["status"]] += 1
        self.orders[payload["order_id"]] = {**payload, "status_since":
            previous["status_since"] if previous and previous["status"] == payload["status"] else payload["occurred_at"]}

    def restore(self):
        metadata.create_all(self.engine)
        with self.engine.connect() as connection:
            for row in connection.execute(select(events_table).order_by(events_table.c.id)):
                if row.outcome == "applied":
                    self.apply(row.payload)
                self.recent.append({**row.payload, "outcome": row.outcome})

    def persist_events(self, batch):
        results, accepted = [], []
        staged = {}
        with self.engine.begin() as connection:
            existing = {}
            ids = list({event.event_id for event in batch})
            for offset in range(0, len(ids), 500):
                rows = connection.execute(select(events_table.c.event_id, events_table.c.payload)
                    .where(events_table.c.event_id.in_(ids[offset:offset + 500])))
                existing.update((row.event_id, row.payload) for row in rows)
            new_rows = []
            for event in batch:
                payload = event.model_dump(mode="json")
                prior = existing.get(event.event_id)
                if prior is not None:
                    same = prior == payload or ("occurred_at" not in event.model_fields_set and
                        {k: v for k, v in prior.items() if k != "occurred_at"} ==
                        {k: v for k, v in payload.items() if k != "occurred_at"})
                    outcome = "duplicate" if same else "event_id_conflict"
                else:
                    previous = staged.get(event.order_id, self.orders.get(event.order_id))
                    outcome = "applied"
                    if previous:
                        if previous["store_id"] != event.store_id:
                            outcome = "store_conflict"
                        elif event.occurred_at <= datetime.fromisoformat(previous["occurred_at"]):
                            outcome = "stale"
                        elif STATUSES.index(event.status) < STATUSES.index(previous["status"]):
                            outcome = "status_regression"
                    new_rows.append({"event_id": event.event_id, "payload": payload, "outcome": outcome})
                    existing[event.event_id] = payload
                    accepted.append((payload, outcome))
                    if outcome == "applied":
                        staged[event.order_id] = payload
                results.append({"event_id": event.event_id, "outcome": outcome})
            if new_rows:
                connection.execute(insert(events_table), new_rows)
        for payload, outcome in accepted:
            if outcome == "applied":
                self.apply(payload)
            self.recent.append({**payload, "outcome": outcome})
        return {"received": len(batch), "applied": sum(r["outcome"] == "applied" for r in results), "results": results}

    async def ingest(self, batch):
        async with self.lock:
            return await asyncio.to_thread(self.persist_events, batch)

    def snapshot(self):
        timestamp = now()
        delay_minutes = int(os.getenv("DELAY_MINUTES", "30"))
        backlog_threshold = int(os.getenv("BACKLOG_THRESHOLD", "30"))
        delayed = Counter()
        for order in self.orders.values():
            if order["status"] == "out_for_delivery" and (timestamp - datetime.fromisoformat(order["status_since"])).total_seconds() > delay_minutes * 60:
                delayed[order["store_id"]] += 1
        stores, anomalies = [], []
        for store_id, counts in sorted(self.counts.items()):
            total = sum(counts.values())
            active = total - counts["delivered"]
            stores.append({"store_id": store_id, "total": total, **dict(counts), "delayed_deliveries": delayed[store_id]})
            rules = [
                ("backlog", counts["open"] + counts["packed"] >= backlog_threshold,
                 {"backlog": counts["open"] + counts["packed"], "threshold": backlog_threshold}, "Review packing capacity and dispatch staffing."),
                ("delivery_delay", delayed[store_id] > 0,
                 {"delayed_orders": delayed[store_id], "threshold_minutes": delay_minutes}, "Check courier availability and contact delayed drivers."),
                ("status_imbalance", active >= 10 and counts["open"] / max(active, 1) >= 0.7,
                 {"open_share": round(counts["open"] / max(active, 1), 3), "threshold": 0.7, "active_orders": active}, "Investigate orders waiting to be packed.")]
            for kind, triggered, evidence, action in rules:
                if triggered:
                    anomalies.append({"store_id": store_id, "type": kind, "severity": "high" if kind == "delivery_delay" else "warning", "evidence": evidence, "recommended_action": action})
        summary = {"generated_at": timestamp.isoformat(), "method": "deterministic_rules", "anomaly_count": len(anomalies),
                   "text": f"{len(stores)} stores monitored; {len(anomalies)} anomalies detected. " + (" ".join(f"{a['store_id']}: {a['type']}." for a in anomalies) or "No thresholds exceeded."), "anomalies": anomalies}
        return {"generated_at": timestamp.isoformat(), "stores": stores, "summary": summary}

    async def publish(self):
        async with self.lock:
            snapshot = self.snapshot()
            def save():
                with self.engine.begin() as connection:
                    result = connection.execute(insert(snapshots_table).values(payload=snapshot))
                    return result.inserted_primary_key[0]
            snapshot["snapshot_id"] = await asyncio.to_thread(save)
            self.latest = snapshot
            recent = list(self.recent)
        try:
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.set("operations:dashboard", json.dumps(snapshot), ex=60)
                pipe.set("operations:summary", json.dumps(snapshot["summary"]), ex=60)
                pipe.set("operations:events:recent", json.dumps(recent), ex=60)
                pipe.publish("operations:updates", json.dumps(snapshot))
                await pipe.execute()
            self.redis_ok = True
        except Exception:
            self.redis_ok = False

    async def seed(self):
        batch = []
        for store, status, count, age in [("store-001", "open", 45, 10), ("store-001", "packed", 5, 5),
                ("store-002", "out_for_delivery", 12, 45), ("store-002", "delivered", 25, 2),
                ("store-003", "open", 6, 2), ("store-003", "packed", 8, 2), ("store-003", "delivered", 30, 1)]:
            batch.extend(Event(order_id=f"seed-{uuid4()}", store_id=store, status=status,
                               occurred_at=now() - timedelta(minutes=age)) for _ in range(count))
        return await self.ingest(batch)

    async def mock_tick(self):
        async with self.lock:
            active = [o for o in self.orders.values() if o["status"] != "delivered" and not o["order_id"].startswith("seed-")]
        random.shuffle(active)
        batch = []
        for _ in range(self.rate):
            if active and random.random() < 0.75:
                order = active.pop()
                batch.append(Event(order_id=order["order_id"], store_id=order["store_id"], status=STATUSES[STATUSES.index(order["status"]) + 1]))
            else:
                batch.append(Event(order_id=f"mock-{uuid4()}", store_id=f"store-{random.randint(1, 3):03}", status="open"))
        await self.ingest(batch)

    async def run(self):
        while True:
            start = asyncio.get_running_loop().time()
            try:
                if self.mock_enabled:
                    await self.mock_tick()
                await self.publish()
                self.last_error = None
            except Exception:
                self.last_error = "Background processing failed; see server logs"
                log.exception("Snapshot loop failed")
            await asyncio.sleep(max(0.01, 1 - (asyncio.get_running_loop().time() - start)))


@asynccontextmanager
async def lifespan(app):
    service = Operations()
    app.state.ops = service
    await asyncio.to_thread(service.restore)
    if service.mock_enabled and not service.orders:
        await service.seed()
    await service.publish()
    task = asyncio.create_task(service.run())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await service.redis.aclose()
    service.engine.dispose()


app = FastAPI(title="Live Operations Demo", version="1.0.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard_page():
    return Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")


@app.get("/health")
async def health():
    service = app.state.ops
    try:
        def check():
            with service.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        await asyncio.to_thread(check)
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok and service.redis_ok and not service.last_error else "degraded",
            "database": db_ok, "redis": service.redis_ok, "background_error": service.last_error,
            "last_snapshot_at": service.latest.get("generated_at"), "mock_enabled": service.mock_enabled,
            "events_per_second": service.rate}


@app.post("/events")
async def ingest_event(event: Event):
    return await app.state.ops.ingest([event])


@app.post("/events/batch")
async def ingest_batch(events: Annotated[list[Event], Field(min_length=1, max_length=5000)]):
    return await app.state.ops.ingest(events)


@app.get("/events")
async def recent_events(limit: int = Query(100, ge=1, le=1000)):
    return list(app.state.ops.recent)[-limit:][::-1]


@app.get("/dashboard")
async def dashboard():
    return app.state.ops.latest


@app.get("/summary")
async def summary():
    return app.state.ops.latest["summary"]


@app.get("/snapshots")
async def history(limit: int = Query(20, ge=1, le=100)):
    def read():
        with app.state.ops.engine.connect() as connection:
            return [{**row.payload, "snapshot_id": row.id} for row in connection.execute(select(snapshots_table).order_by(snapshots_table.c.id.desc()).limit(limit))]
    return await asyncio.to_thread(read)


@app.get("/stream")
async def stream():
    async def generate():
        previous = None
        while True:
            data = app.state.ops.latest
            if data.get("snapshot_id") != previous:
                previous = data.get("snapshot_id")
                yield f"id: {previous}\ndata: {json.dumps(data)}\n\n"
            await asyncio.sleep(1)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/mock/start")
async def start_mock(config: MockConfig):
    app.state.ops.rate = config.events_per_second
    app.state.ops.mock_enabled = True
    return {"running": True, "events_per_second": config.events_per_second}


@app.post("/mock/stop")
async def stop_mock():
    app.state.ops.mock_enabled = False
    return {"running": False}


@app.post("/mock/seed")
async def seed_mock():
    result = await app.state.ops.seed()
    await app.state.ops.publish()
    return result
