# Case 3: Validation, timestamp identity and large versions

## How we achieve this in the codebase

Validation happens before persistence through the `Event` Pydantic model in [app/main.py](../app/main.py). All six fields are required. `extra="forbid"` rejects unknown fields; `StrictInt` with `gt=0` rejects boolean, float, string and nonpositive versions; the status `Literal` permits only the five supported statuses. ID fields have length bounds, and `nonblank()` rejects whitespace-only values while returning valid IDs unchanged.

`timestamp_representation()` accepts only strings or datetime objects. `aware()` requires a timezone and converts the timestamp to UTC. `_transaction()` compares `model_dump(mode="json")` with the retained payload, so equivalent instants with different timezone offsets have the same identity. In contrast, `" e1 "` and `"e1"` remain different IDs.

FastAPI validates request bodies with this model and returns HTTP 422 for invalid inputs before invoking the write path. `ingest_batch()` also requires 1–5,000 events, and `MockConfig` bounds the mock rate. Versions are stored as canonical decimal text in both relational tables, constrained by `contract_constraints()`, and converted to Python integers for comparison and order reads. This supports the demonstrated versions beyond SQLite's signed 64-bit integer range.

**Code proof:** the validation, required-field, timestamp-identity and large-version tests in [tests/test_round2.py](../tests/test_round2.py) check model behavior and persistence; `test_batch_and_validation()` in [tests/test_api.py](../tests/test_api.py) checks HTTP rejection.

## Step 1. Prepare Python

```powershell
cd C:\Code\mock-fynd
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -r requirements.txt
```

These tests create isolated temporary databases and disable mock traffic. No running API is required.

## Step 2. Validation cases

Each row starts from the valid six-field e1 payload, changes only the indicated field, and uses a fresh test database. API validation errors return HTTP 422. Direct model tests expect `ValidationError`. No invalid request changes order counts.

| Input change | Expected result |
|---|---|
| version=true | Reject boolean |
| version=1.0 | Reject floating-point version |
| version="1" | Reject string version |
| version=0 | Reject zero |
| version=-1 | Reject negative version |
| status="unknown" | Reject unknown status |
| event_id contains only spaces/tab | Reject blank event ID |
| order_id contains only newline | Reject blank order ID |
| store_id=" " | Reject blank store ID |
| Add extra=1 | Reject unknown field |
| occurred_at="2026-01-01T00:00:00" | Reject timestamp without timezone |
| occurred_at=123 | Reject numeric timestamp |
| Omit event_id | Reject missing required event ID |
| Omit version | Reject missing required version |
| Omit occurred_at | Reject missing required timestamp |
| Submit batch `[]` | HTTP 422 |
| POST /mock/start with events_per_second=0 | HTTP 422 |
| Omit both version and occurred_at | HTTP 422 |

```powershell
.\.venv\Scripts\python -m pytest tests/test_round2.py::test_strict_validation tests/test_round2.py::test_required_fields tests/test_api.py::test_batch_and_validation -v
```

The contract requires all six fields; the listed parameterized missing-field cases explicitly test event_id, version and occurred_at.


## Step 3. Timestamp identity, exact IDs and large versions

| Test | Input sequence | Expected result |
|---|---|---|
| test_normalized_timestamp_identity_and_exact_ids | ID `" e1 "`, timestamp `2026-01-01T00:00:00Z`; retry same ID at `2026-01-01T05:30:00+05:30`; then ID `"e1"` at v1 | applied, duplicate, stale; exact spaced ID retained as current source |
| test_positive_versions_beyond_sqlite_integer_range | e1 v=2**80 open; next v=2**80+1 returned | Both applied; final version=1208925819614629174706177; order returned |

```powershell
.\.venv\Scripts\python -m pytest tests/test_round2.py::test_normalized_timestamp_identity_and_exact_ids tests/test_round2.py::test_positive_versions_beyond_sqlite_integer_range -v
```


## Final check

Each pytest command must finish with all selected tests passed and exit code 0. An assertion failure means the expected result was not met. The existing Starlette/httpx deprecation warning may appear.
