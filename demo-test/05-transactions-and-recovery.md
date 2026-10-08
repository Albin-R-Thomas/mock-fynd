# Case 5: Transactions, retries and crash recovery

## How we achieve this in the codebase

In [app/main.py](../app/main.py), `Operations._transaction()` processes the entire batch in request order on one connection under `BEGIN IMMEDIATE`. Later events in the batch see earlier writes. Both retained event decisions and current-order changes commit together. Exceptions inside the transaction trigger rollback, so a precommit failure leaves none of the batch persisted.

`persist_events()` retries the whole transaction only for recognized SQLite BUSY/LOCKED `OperationalError` codes, with at most four attempts and increasing short waits. Other SQL failures and exhausted contention become `DatabaseUnavailable`. The `after_commit` hook runs outside the retry and rollback scopes: a lost acknowledgement after commit leaves durable data, and the client's identical retry is a duplicate.

`restore()` configures WAL journaling and FULL synchronous writes and prepares the schema. Current state remains in the database when engines are disposed or services restart; `read_orders()` reads those persisted rows directly.

**Code proof:** [tests/test_round2.py](../tests/test_round2.py) injects constructor-only `before_commit` and `after_commit` hooks, holds an independent write lock, and simulates a recognized lock error. For actual crash tests, [scripts/crash_worker.py](../scripts/crash_worker.py) writes a readiness file at the chosen hook and pauses. The parent kills the worker, opens a fresh service on the same database, and verifies rollback before commit or retained data after commit, followed by a safe retry. These hooks are not exposed through HTTP.

## Step 1. Prepare Python

```powershell
cd C:\Code\mock-fynd
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -r requirements.txt
```

These tests create isolated temporary databases and disable mock traffic. No running API is required.

## Step 2. Transactions, retry and crash recovery

Failure injection uses private Python test hooks. Run these through pytest; HTTP requests cannot enable the hooks.

| Test | Input / failure | Expected result |
|---|---|---|
| test_batch_order_and_whole_batch_exception_rollback | Batch e1/v1/open then e2/v2/packed; raise RuntimeError before commit | Hook called once; zero events/orders after rollback; remove hook and retry: applied=2, final v2 |
| test_after_commit_lost_ack_no_rollback_or_retry | e1 commits; after-commit hook raises RuntimeError | Hook called once; event remains durable; retry after removing hook is duplicate; total=1/open=1 |
| test_connection_recreation_and_service_restart | Apply e1; dispose connections; apply e2/v2/returned; recreate services | e2 applied; restarted services read identical orders; total=1/returned=1 |
| test_actual_process_kill[before_commit] | Child attempts e1/open + e2/v2/packed batch; kill at precommit signal | Restart sees zero events/orders; retry applied=2; final total=1/packed=1 |
| test_actual_process_kill[after_commit] | Same batch; kill at postcommit signal | Restart sees two events/one order; retry applied=0; final total=1/packed=1 |
| test_lock_exhaustion_is_explicit_and_rolls_back | Independent connection holds BEGIN IMMEDIATE while writer submits e1 | DatabaseUnavailable with contention; no retained events; release lock and retry: applied |
| test_recognized_lock_retries_complete_transaction | Inject SQLITE_BUSY before first commit only | Whole transaction retries; hook called twice; outcome applied; exactly one event |

```powershell
.\.venv\Scripts\python -m pytest tests/test_round2.py -k 'whole_batch_exception or lost_ack or connection_recreation or actual_process_kill or lock' -v
```

Process-kill tests demonstrate software crash recovery at controlled transaction boundaries, not hardware power-loss durability.


## Final check

Each pytest command must finish with all selected tests passed and exit code 0. An assertion failure means the expected result was not met. The existing Starlette/httpx deprecation warning may appear.
