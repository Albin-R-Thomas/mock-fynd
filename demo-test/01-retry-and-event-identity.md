# Case 1: Retry and event identity

## How we achieve this in the codebase

The API retains the first decision for each event ID so that retrying a committed request cannot apply it again. In [app/main.py](../app/main.py), `ingest_event()` passes the validated `Event` through `acknowledge()` and `Operations.ingest()` to `persist_events()` and `_transaction()`.

Inside `_transaction()`, the service looks up `event_id` in `order_events` before checking the current order. It compares the complete normalized payload with the stored JSON: an identical payload returns `duplicate`; a changed payload returns `event_id_conflict`. Both paths leave the original event and order unchanged and return the original outcome as `stored_decision`. The database also enforces a unique event ID.

For a first-seen applicable event, the event history and `current_orders` row are written in one transaction and committed before the response. `snapshot()` counts rows in `current_orders`, which has one primary-key row per order, so a retry cannot increase the dashboard total. `applied` counts only results whose outcome is `applied`.

**Code proof:** `test_required_fixture_and_retained_identity()` in [tests/test_round2.py](../tests/test_round2.py) checks the responses, retained payloads and counts through independent writer and reader services sharing one database.

Run this file on its own fresh database. Use PowerShell and follow the steps in order.

## Step 1. Prepare the environment


```powershell
cd C:\Code\mock-fynd
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -r requirements.txt
```

In Terminal A, start a fresh database with mock traffic disabled:

```powershell
$demoDatabase = Join-Path $PWD ('demo-test-' + [guid]::NewGuid().ToString('N') + '.db')
$env:DATABASE_URL = 'sqlite:///' + ($demoDatabase -replace '\\','/')
$env:MOCK_AUTOSTART = 'false'
$env:REDIS_URL = 'redis://localhost:6399/0'
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8021
```

Keep Terminal A running. In Terminal B:

```powershell
cd C:\Code\mock-fynd
$base = 'http://127.0.0.1:8021'
function Send-Event {
    param(
        [string]$EventId = 'e1', [string]$OrderId = 'o1',
        [string]$StoreId = 's1', [int]$Version = 1,
        [string]$Status = 'open'
    )
    $body = @{
        event_id=$EventId; order_id=$OrderId; store_id=$StoreId
        version=$Version; status=$Status; occurred_at='2026-01-01T00:00:00Z'
    } | ConvertTo-Json -Compress
    $response = Invoke-RestMethod "$base/events" -Method Post -ContentType 'application/json' -Body $body
    $response | ConvertTo-Json -Depth 10
}
function Show-Counts {
    (Invoke-RestMethod "$base/dashboard").stores |
        Select-Object store_id,total,open,packed,out_for_delivery,delivered,returned |
        Format-Table
}
```

`received` is the number submitted; `applied` is the number changing order state. Rejected first-seen events are retained in event history. A duplicate returns the original `stored_decision`. Counts describe retained orders, not the number of events.


## Step 2. Initial event

Input:

```powershell
Send-Event
Show-Counts
```

Expected response: `received=1`, `applied=1`, outcome `applied`.

Expected dashboard: s1 total=1/open=1. All other status buckets are zero.

## Step 3. Identical retry

Input:

```powershell
Send-Event
Show-Counts
```

Expected response: `applied=0`, outcome `duplicate`, stored_decision `applied`.

Expected dashboard: s1 total=1/open=1. All other status buckets are zero.

## Verify the retry counts

Run this immediately after Step 3, before changing the payload:

```powershell
$store = @((Invoke-RestMethod "$base/dashboard").stores | Where-Object store_id -eq 's1')
if ($store.Count -ne 1 -or $store[0].total -ne 1 -or $store[0].open -ne 1) {
    throw 'FAIL: expected s1 total=1/open=1'
}
Write-Host 'PASS: s1 total=1/open=1' -ForegroundColor Green
```

Expected: the green PASS message; retry does not add another order.

## Step 4. Changed payload with same ID

Input:

```powershell
Send-Event -Status packed
Show-Counts
```

Expected response: `applied=0`, outcome `event_id_conflict`; original e1 unchanged.

Expected dashboard: s1 total=1/open=1. All other status buckets are zero.

## Automated proof

```powershell
.\.venv\Scripts\python -m pytest tests/test_round2.py::test_required_fixture_and_retained_identity -v
```

Expected: 1 passed. The automated test covers the complete sequence on an isolated database.
