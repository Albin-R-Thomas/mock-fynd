# Live operations demo

FastAPI demo with mock order traffic, an in-memory per-store buffer, SQL event/snapshot history, Redis caching, and a live browser dashboard. No AI key required: summaries are structured, rule-based JSON ready to pass to an LLM.

## Run the complete stack

```sh
docker compose up --build -d
```

- Dashboard: http://localhost:8001
- Swagger / try every API: http://localhost:8001/docs
- Health: http://localhost:8001/health

Compose runs PostgreSQL, Redis with AOF, and **one** API worker. Named volumes retain data across restarts. Mock traffic starts at 50 events/second. The initial scenario includes a packing backlog, delayed deliveries, and a healthy store. Seed orders intentionally remain stuck to keep anomalies visible.

## Run Python locally (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
docker compose up -d redis
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Local Python defaults to SQLite in `operations.db`. Set environment variables from `.env.example` in your shell (the file is documentation, not automatically loaded). To use your DB, set `DATABASE_URL` to `postgresql+psycopg://user:password@host:5432/database` and `REDIS_URL` to your Redis connection URL. Tables are created on startup. No existing DB credentials were supplied.

## APIs

| Method | Route | Purpose |
|---|---|---|
| POST | `/events` | Persist and apply one event |
| POST | `/events/batch` | Persist and apply up to 5,000 events |
| GET | `/events?limit=100` | Latest events, including ignored outcomes (max 1,000) |
| GET | `/dashboard` | Latest one-second snapshot of all stores, plus summary |
| GET | `/summary` | AI-ready anomaly summary with evidence and recommendations |
| GET | `/stream` | Server-sent snapshot stream, one-second polling cadence |
| GET | `/snapshots?limit=20` | SQL snapshot and summary history (max 100) |
| POST | `/mock/start` | Start traffic; body `{"events_per_second":50}` |
| POST | `/mock/stop` | Pause traffic; snapshots continue |
| POST | `/mock/seed` | Add another scenario with all three anomaly types |
| GET | `/health` | SQL, Redis, generator, last snapshot and loop health |

Example event (omit `occurred_at` to use current UTC time):

```json
{
  "event_id": "evt-1001",
  "order_id": "order-1001",
  "store_id": "store-001",
  "status": "open",
  "occurred_at": "2026-10-01T09:00:00Z"
}
```

Use a new event ID for subsequent status updates. Retry with the same ID and identical payload for deduplication. If the timestamp is omitted on a retry, the original stored timestamp is retained; preferably supply the source timestamp. Accepted outcomes include `applied`, `duplicate`, `stale`, `status_regression`, `store_conflict`, and `event_id_conflict`. Results are per event; ignored events do not change counts. First-seen events may start at any status. Forward status jumps are allowed. Equal/older timestamps and regressions are ignored. Orders cannot move between stores.

## Counts and anomalies

Each distinct order contributes to exactly one status; `total = open + packed + out_for_delivery + delivered`. Total is cumulative for the demo, not a rolling window. Ingest commits SQL before updating the buffer; one lock serializes batches and snapshots. Restart replays accepted SQL events. Updates appear on the next tick.

- **Backlog:** open + packed >= `BACKLOG_THRESHOLD` (default 30).
- **Delivery delay:** out-for-delivery status age > `DELAY_MINUTES` (default 30). Repeated same-status events do not reset its age.
- **Status imbalance:** at least 10 active orders and >= 70% of active orders still open. Active excludes delivered orders.

These are configurable static heuristics, not learned anomaly detection. Each summary includes generation time, method, readable text, severity, evidence, and suggested action.

## Persistence

- SQL `order_events`: durable event payloads and application outcomes; unique event ID.
- SQL `dashboard_snapshots`: snapshot and summary every tick.
- Redis `operations:dashboard`: latest full snapshot.
- Redis `operations:summary`: latest summary.
- Redis `operations:events:recent`: last 1,000 persisted events as JSON.
- Redis `operations:updates`: pub/sub snapshot notifications.

Redis keys expire after 60 seconds and refresh each tick. Redis is a recent-data cache; SQL is the durable history. Redis outages mark health degraded, while SQL ingestion and snapshots continue; the next successful tick repopulates the cache. Redis does not retain all historical events. SQL failure prevents an ingestion acknowledgement and is not silently converted into success.

```sh
docker compose exec redis redis-cli GET operations:summary
docker compose exec db psql -U demo -d operations -c "SELECT count(*) FROM order_events;"
docker compose exec db psql -U demo -d operations -c "SELECT count(*) FROM dashboard_snapshots;"
```

## Validation and scope

```powershell
.\.venv\Scripts\python -m pytest -q
```

This is an unauthenticated local demo. Use **one API process / replica** because the buffer is process-local. It accepts batched traffic, but no production throughput SLA is claimed. Snapshots target a one-second cadence; slow SQL may extend it. Order state and SQL history grow until a retention/archive policy is added. Restart rebuild time grows with event history. Add authentication, migrations, retention, a durable consumer architecture, and load testing before production use. SSE always sends the latest snapshot; it is not a replayable event log.

Implementation references: [FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/) and [Redis asyncio client](https://redis.io/docs/latest/develop/clients/redis-py/async/).


