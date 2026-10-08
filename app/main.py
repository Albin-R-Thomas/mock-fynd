import asyncio
import json
import logging
import os
import random
import sqlite3
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import JSONResponse
import secrets
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from redis.asyncio import Redis
from sqlalchemy import JSON, CheckConstraint, Column, Integer, MetaData, String, Table, UniqueConstraint, PrimaryKeyConstraint, create_engine, insert, select, text, func, case
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from sqlalchemy.engine import make_url
from app import auth

log = logging.getLogger("operations")
STATUSES = ("open", "packed", "out_for_delivery", "delivered", "returned")
metadata = MetaData()


def contract_columns():
    return [Column("event_id", String(100), nullable=False),
            Column("order_id", String(100), nullable=False),
            Column("store_id", String(100), nullable=False),
            # Canonical decimal text avoids SQLite's signed-64-bit INTEGER ceiling.
            Column("version", String, nullable=False),
            Column("status", String(30), nullable=False),
            Column("occurred_at", String, nullable=False)]


def contract_constraints():
    return [CheckConstraint("typeof(version) = 'text' AND version GLOB '[1-9]*' AND version NOT GLOB '*[^0-9]*'"),
            CheckConstraint("status IN ('open','packed','out_for_delivery','delivered','returned')"),
            *[CheckConstraint(f"length(trim({field})) > 0 AND length({field}) <= 100")
              for field in ("event_id", "order_id", "store_id")]]


events_table = Table("order_events", metadata,
    Column("id", Integer, primary_key=True), *contract_columns(),
    Column("payload", JSON, nullable=False), Column("outcome", String(30), nullable=False),
    CheckConstraint("outcome IN ('applied','stale','store_conflict')"),
    *contract_constraints())
events_table.append_constraint(UniqueConstraint('event_id'))
orders_table = Table("current_orders", metadata, *contract_columns(),
    Column("status_since", String, nullable=False), *contract_constraints())
orders_table.append_constraint(PrimaryKeyConstraint('order_id'))
snapshots_table = Table("dashboard_snapshots", metadata,
    Column("id", Integer, primary_key=True), Column("payload", JSON, nullable=False))


class DatabaseUnavailable(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc)


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=100)
    order_id: str = Field(min_length=1, max_length=100)
    store_id: str = Field(min_length=1, max_length=100)
    version: StrictInt = Field(gt=0)
    status: Literal["open", "packed", "out_for_delivery", "delivered", "returned"]
    occurred_at: datetime

    @field_validator("event_id", "order_id", "store_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("IDs must not be whitespace-only")
        return value

    @field_validator("occurred_at", mode="before")
    @classmethod
    def timestamp_representation(cls, value):
        if not isinstance(value, (str, datetime)):
            raise ValueError("occurred_at must be an explicit timezone-aware timestamp")
        return value

    @field_validator("occurred_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value.astimezone(timezone.utc)


class MockConfig(BaseModel):
    events_per_second: int = Field(default=50, ge=1, le=2000)


class Operations:
    def __init__(self, *, test_hooks=None):
        # Hooks are constructor-only, never read from a request or environment.
        self.test_hooks = test_hooks or {}
        url = make_url(os.getenv("DATABASE_URL", "sqlite:///./operations-v2.db"))
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
            raise ValueError("Round 2 requires a shared local SQLite file")
        self.database_path = Path(url.database).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url.set(database=str(self.database_path)),
            connect_args={"check_same_thread": False, "timeout": 0.2})
        self.redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                                    socket_connect_timeout=0.3, socket_timeout=0.3)
        self.latest = {}
        self.redis_ok = False
        self.last_error = None
        self.mock_enabled = os.getenv("MOCK_AUTOSTART", "false").lower() == "true"
        self.rate = int(os.getenv("MOCK_EVENTS_PER_SECOND", "50"))
        self.mock_started_at = time.monotonic()

    def restore(self):
        with self.engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.exec_driver_sql("PRAGMA synchronous=FULL")
            connection.commit()
        metadata.create_all(self.engine)
        with self.engine.connect() as connection:
            columns = {r[1] for r in connection.exec_driver_sql("PRAGMA table_info(order_events)")}
            if "version" not in columns:
                raise ValueError("Legacy database: choose a fresh Round 2 database; no migration is provided")

    def _hook(self, name):
        callback = self.test_hooks.get(name)
        if callback:
            callback()

    def _transaction(self, batch):
        results = []
        with self.engine.connect() as connection:
            # Write ownership precedes identity/order reads, including absent rows.
            connection.exec_driver_sql("PRAGMA synchronous=FULL")
            connection.commit()
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                for event in batch:
                    payload = event.model_dump(mode="json")
                    prior = connection.execute(select(events_table).where(
                        events_table.c.event_id == event.event_id)).mappings().first()
                    if prior:
                        outcome = "duplicate" if prior["payload"] == payload else "event_id_conflict"
                        stored_decision = prior["outcome"]
                    else:
                        current = connection.execute(select(orders_table).where(
                            orders_table.c.order_id == event.order_id)).mappings().first()
                        outcome = ("store_conflict" if current and current["store_id"] != event.store_id
                                   else "stale" if current and event.version <= int(current["version"])
                                   else "applied")
                        connection.execute(insert(events_table).values(**{**payload, "version": str(event.version)}, payload=payload, outcome=outcome))
                        stored_decision = outcome
                        if outcome == "applied":
                            status_since = (current["status_since"] if current and current["status"] == event.status
                                            else payload["occurred_at"])
                            values = {**payload, "version": str(event.version), "status_since": status_since}
                            if current:
                                connection.execute(orders_table.update().where(
                                    orders_table.c.order_id == event.order_id).values(**values))
                            else:
                                connection.execute(insert(orders_table).values(**values))
                    results.append({"event_id": event.event_id, "outcome": outcome,
                                    "stored_decision": stored_decision})
                self._hook("before_commit")
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
        return {"received": len(batch), "applied": sum(r["outcome"] == "applied" for r in results), "results": results}

    def persist_events(self, batch):
        for attempt in range(4):
            try:
                result = self._transaction(batch)
                break
            except OperationalError as exc:
                code = getattr(exc.orig, "sqlite_errorcode", None)
                if code is None or code & 255 not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise DatabaseUnavailable("Database transaction failed") from exc
                if attempt == 3:
                    raise DatabaseUnavailable("SQLite write contention: retry the identical batch") from exc
                time.sleep(0.05 * (attempt + 1))
            except SQLAlchemyError as exc:
                raise DatabaseUnavailable("Database transaction failed") from exc
        # Outside both rollback and retry scopes: durable commit already happened.
        self._hook("after_commit")
        return result

    async def ingest(self, batch):
        return await asyncio.to_thread(self.persist_events, batch)

    def read_events(self, limit=100):
        with self.engine.connect() as connection:
            return [{**r.payload, "outcome": r.outcome} for r in connection.execute(
                select(events_table).order_by(events_table.c.id.desc()).limit(limit))]

    def read_orders(self):
        with self.engine.connect() as connection:
            return [{**dict(r), "version": int(r["version"])} for r in connection.execute(select(orders_table)).mappings()]

    def snapshot(self):
        timestamp = now()
        delay_minutes = int(os.getenv("DELAY_MINUTES", "30"))
        backlog_threshold = int(os.getenv("BACKLOG_THRESHOLD", "30"))
        stores, anomalies = [], []
        # A single aggregate SELECT gives all related counts one SQLite read snapshot.
        with self.engine.connect() as connection:
            rows = connection.execute(select(orders_table.c.store_id,
                func.count().label("total"),
                *[func.sum(case((orders_table.c.status == status, 1), else_=0)).label(status)
                  for status in STATUSES],
                func.sum(case(((orders_table.c.status == "out_for_delivery") &
                    (func.julianday(orders_table.c.status_since) < func.julianday((timestamp - timedelta(minutes=delay_minutes)).isoformat())), 1), else_=0)).label("delayed_deliveries")
                ).group_by(orders_table.c.store_id).order_by(orders_table.c.store_id)).mappings().all()
        for row in rows:
            store_id = row["store_id"]
            counts = {status: row[status] for status in STATUSES}
            delayed = {store_id: row["delayed_deliveries"]}
            total = row["total"]
            active = total - counts["delivered"] - counts["returned"]
            stores.append(dict(row))
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
        snapshot = await asyncio.to_thread(self.snapshot)
        def save():
            with self.engine.begin() as connection:
                result = connection.execute(insert(snapshots_table).values(payload=snapshot))
                keep = int(os.getenv("SNAPSHOT_RETENTION", "0"))
                if keep > 0:
                    recent_ids = select(snapshots_table.c.id).order_by(snapshots_table.c.id.desc()).limit(keep)
                    connection.execute(snapshots_table.delete().where(snapshots_table.c.id.not_in(recent_ids)))
                return result.inserted_primary_key[0]
        snapshot["snapshot_id"] = await asyncio.to_thread(save)
        self.latest = snapshot
        recent = await asyncio.to_thread(self.read_events, 1000)
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
            batch.extend(Event(event_id=str(uuid4()), version=1, order_id=f"seed-{uuid4()}", store_id=store, status=status,
                               occurred_at=now() - timedelta(minutes=age)) for _ in range(count))
        return await self.ingest(batch)

    async def mock_tick(self):
        active = [o for o in await asyncio.to_thread(self.read_orders) if o["status"] not in ("delivered", "returned") and not o["order_id"].startswith("seed-")]
        random.shuffle(active)
        batch = []
        for _ in range(self.rate):
            if active and random.random() < 0.75:
                order = active.pop()
                batch.append(Event(event_id=str(uuid4()), version=order["version"] + 1, occurred_at=now(), order_id=order["order_id"], store_id=order["store_id"], status=STATUSES[STATUSES.index(order["status"]) + 1]))
            else:
                batch.append(Event(event_id=str(uuid4()), version=1, occurred_at=now(), order_id=f"mock-{uuid4()}", store_id=f"store-{random.randint(1, 3):03}", status="open"))
        await self.ingest(batch)

    async def run(self):
        while True:
            start = asyncio.get_running_loop().time()
            try:
                if self.mock_enabled:
                    maximum_runtime = int(os.getenv("MOCK_MAX_RUNTIME_SECONDS", "0"))
                    if maximum_runtime and time.monotonic() - self.mock_started_at >= maximum_runtime:
                        self.mock_enabled = False
                    else:
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
    await asyncio.to_thread(auth.metadata.create_all, service.engine)
    if service.mock_enabled and not await asyncio.to_thread(service.read_orders):
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

from app.verification import router as verification_router
app.include_router(verification_router)
app.include_router(auth.router)
mutation_times = deque()


@app.middleware("http")
async def protect_mutations(request: Request, call_next):
    try:
        await auth.authorize(request)
    except HTTPException as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail":exc.detail}, headers={"Cache-Control":"no-store", **(exc.headers or {})})
    limit = int(os.getenv("DEMO_MUTATIONS_PER_MINUTE", "0"))
    if request.method == "POST" and limit and not request.url.path.startswith('/auth/'):
        timestamp = time.monotonic()
        while mutation_times and mutation_times[0] < timestamp - 60:
            mutation_times.popleft()
        if len(mutation_times) >= limit:
            return JSONResponse(status_code=429, content={"detail": "Demo is busy. Retry in a minute."}, headers={"Retry-After": "60"})
        mutation_times.append(timestamp)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get('/health/live', include_in_schema=False)
async def liveness():
    return {'status':'ok'}


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


async def acknowledge(events):
    try:
        return await app.state.ops.ingest(events)
    except DatabaseUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from exc


async def shared_snapshot():
    try:
        return await asyncio.to_thread(app.state.ops.snapshot)
    except SQLAlchemyError as exc:
        raise HTTPException(503, detail="Authoritative database unavailable; no cached data served") from exc


@app.post("/events")
async def ingest_event(event: Event):
    return await acknowledge([event])


@app.post("/events/batch")
async def ingest_batch(events: Annotated[list[Event], Field(min_length=1, max_length=5000)]):
    return await acknowledge(events)


@app.get("/events")
async def recent_events(limit: int = Query(100, ge=1, le=1000)):
    return await asyncio.to_thread(app.state.ops.read_events, limit)


@app.get("/dashboard")
async def dashboard():
    return await shared_snapshot()


@app.get("/summary")
async def summary():
    return (await shared_snapshot())["summary"]


@app.get("/snapshots")
async def history(limit: int = Query(20, ge=1, le=100)):
    def read():
        with app.state.ops.engine.connect() as connection:
            return [{**row.payload, "snapshot_id": row.id} for row in connection.execute(select(snapshots_table).order_by(snapshots_table.c.id.desc()).limit(limit))]
    return await asyncio.to_thread(read)


@app.get("/stream")
async def stream(request: Request):
    # Verify availability before sending successful HTTP headers.
    initial = await shared_snapshot()
    async def generate():
        data = initial
        while True:
            if data is not None:
                yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(1)
            try:
                session = await asyncio.to_thread(auth.session_for, request)
            except SQLAlchemyError:
                data = None
                yield 'event: unavailable\ndata: {"detail":"Authentication storage unavailable"}\n\n'
                continue
            if not session:
                yield 'event: unauthorized\ndata: {"detail":"Session expired"}\n\n'
                return
            try:
                data = await shared_snapshot()
            except HTTPException:
                data = None
                yield 'event: unavailable\ndata: {"detail":"Authoritative database unavailable"}\n\n'
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/mock/start")
async def start_mock(config: MockConfig):
    maximum_rate = int(os.getenv("MOCK_MAX_EVENTS_PER_SECOND", "2000"))
    if config.events_per_second > maximum_rate:
        raise HTTPException(422, detail=f"This deployment allows at most {maximum_rate} events per second")
    app.state.ops.rate = config.events_per_second
    app.state.ops.mock_enabled = True
    app.state.ops.mock_started_at = time.monotonic()
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


from app.security import SecurityEnvelope
app.add_middleware(SecurityEnvelope)
