# Case 6: Status age, batch API, summaries and database failures

## How we achieve this in the codebase

In [app/main.py](../app/main.py), `Operations._transaction()` preserves `status_since` when an applied update keeps the same status. A status change sets it to the new event's normalized `occurred_at`. Repeated delivery updates therefore cannot hide the original delay. Version controls acceptance, so a higher-version correction can supply an older timestamp.

`snapshot()` uses one aggregate SQL query over `current_orders` for store totals, status buckets and delayed deliveries. Delay counts include only out-for-delivery orders older than the configured threshold. Delivered and returned orders are excluded from the active-order count used for imbalance. The function builds deterministic backlog, delivery-delay and status-imbalance summaries from these aggregates.

`ingest_batch()` validates the full request and sends it through the same atomic write path as single events. `seed()` constructs the 131 scenario events; `seed_mock()` ingests them and calls `publish()`, which saves a historical snapshot and attempts Redis updates. Redis failure sets `redis_ok=false`; dashboard and summary endpoints read SQL through `shared_snapshot()`.

`shared_snapshot()` converts SQL read failures to HTTP 503, while `acknowledge()` maps `DatabaseUnavailable` write failures to 503. `stream()` checks the initial snapshot before returning successful stream headers. The `/` route serves [app/dashboard.html](../app/dashboard.html).

**Code proof:** [tests/test_api.py](../tests/test_api.py) checks HTTP behavior, age preservation, seed summaries, history and restart reads. The status-metadata and database-failure tests in [tests/test_round2.py](../tests/test_round2.py) check corrections, inactive returned orders and explicit 503 responses using injected failures.

## Step 1. Prepare Python

```powershell
cd C:\Code\mock-fynd
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -r requirements.txt
```

These tests create isolated temporary databases and disable mock traffic. No running API is required.

## Step 2. Status age and returned orders

| Test | Input | Expected result |
|---|---|---|
| test_same_status_does_not_hide_delay | out_for_delivery 45 minutes ago; newer same-status update timestamp now | delayed_deliveries=1; same-status update preserves original age |
| test_status_since_metadata_and_inactive_returned | e1 delivery Jan 1; e2/v2 delivery Jan 2; e3/v3 returned; e4/v4 packed with Jan 1, 2025 timestamp | e2 preserves Jan 1 status_since; returned has zero delayed deliveries; higher-version correction accepts older timestamp and sets status_since to Jan 1, 2025 |

```powershell
.\.venv\Scripts\python -m pytest tests/test_api.py::test_same_status_does_not_hide_delay tests/test_round2.py::test_status_since_metadata_and_inactive_returned -v
```


## Step 3. API, batch, summary and unavailable database

| Test | Input | Expected result |
|---|---|---|
| test_idempotency_ordering_and_counts | e1 apply/retry/conflict; e2/v3 packed; e3/v1 stale; e4/v4 open; e5 other store | Final s1 total=1/open=1/packed=0 |
| test_batch_and_validation | Ordered batch e1/v1/open then e2/v2/delivered | applied=2; one delivered order; malformed inputs described in 03-validation-and-normalization.md rejected |
| test_seed_summary_history_and_storage | POST /mock/seed on fresh DB with unavailable test Redis | applied=131; 131 events; anomaly types backlog, delivery_delay, status_imbalance; matching historical snapshot exists; health redis=false; GET / returns 200 |
| test_restart_restores_state | Apply e1 through API; initialize fresh Operations on same database | Identical orders; one retained event |
| test_database_failure_explicit_http | Inject failing database snapshot reads and failing persistence | GET /dashboard, /summary, initial /stream and POST /events each return HTTP 503 |

```powershell
.\.venv\Scripts\python -m pytest tests/test_api.py tests/test_round2.py::test_database_failure_explicit_http -v
```

For every store, total must equal open + packed + out_for_delivery + delivered + returned.


## Final check

Each pytest command must finish with all selected tests passed and exit code 0. An assertion failure means the expected result was not met. The existing Starlette/httpx deprecation warning may appear.
