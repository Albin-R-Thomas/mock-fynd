# Case 4: Concurrent writers

## How we achieve this in the codebase

Concurrency is coordinated by SQLite across independent connections. In [app/main.py](../app/main.py), `Operations._transaction()` executes `BEGIN IMMEDIATE` before reading event identity or order state. This acquires write ownership even when the target row does not yet exist. A competing writer must wait or retry, then evaluate its event against the preceding writer's committed state.

For identical submissions, the first writer inserts the event and order; the second finds the retained payload and returns `duplicate`. For the v2/v3 race, v2 can apply before v3, or become stale after v3. In either commit order, the final version is 3. Unique `event_id` and primary-key `order_id` constraints provide additional database enforcement. `persist_events()` retries recognized lock failures with bounded waits.

**Code proof:** the `factory()` fixture in [tests/test_round2.py](../tests/test_round2.py) creates separate `Operations` instances and connection pools using the same temporary file. `race()` uses a thread barrier and `ThreadPoolExecutor` to release submissions together. The identical-race and version-race tests use another service to verify retained history and dashboard counts, and repeat the version race with writer assignments reversed.

This design serializes writers to one shared local SQLite file; the demo does not establish coordination between separate database files or hosts.

## Step 1. Prepare Python

```powershell
cd C:\Code\mock-fynd
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -r requirements.txt
```

These tests create isolated temporary databases and disable mock traffic. No running API is required.

## Step 2. Concurrent writers

These tests use independent service connections sharing one fresh database.

| Test | Input | Expected result |
|---|---|---|
| test_independent_connection_identical_race | Two writers concurrently submit identical e1/o1/s1/v1/open | Exactly one applied and one duplicate; one event; s1 total=1/open=1 |
| test_independent_connection_version_race[False] | After e1, race e2/v2/packed and e3/v3/delivered | Final version=3; total=1/delivered=1; three retained events |
| test_independent_connection_version_race[True] | Same inputs with writer assignments reversed | Same final state; v2 may apply or be stale depending on commit order; v3 applies |

```powershell
.\.venv\Scripts\python -m pytest tests/test_round2.py -k 'independent_connection' -v
```


## Final check

Each pytest command must finish with all selected tests passed and exit code 0. An assertion failure means the expected result was not met. The existing Starlette/httpx deprecation warning may appear.
