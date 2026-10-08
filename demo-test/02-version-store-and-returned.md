# Case 2: Version ordering, store ownership and returned orders

## How we achieve this in the codebase

Each order has one authoritative row in `current_orders`. In [app/main.py](../app/main.py), `Operations._transaction()` first checks event identity. For a new event ID, it reads the order and evaluates the rules in this order: a different store produces `store_conflict`; an equal or lower version produces `stale`; otherwise the event is `applied`.

This makes store ownership fixed after the first applied event and makes version the authority for updates. There is no requirement to follow a status sequence or compare timestamps, so v3 can skip v2 and v4 can correct delivered back to packed. Only an applied event replaces the current row. First-seen stale and store-conflict events still enter `order_events` with their decision; identical retries therefore return `duplicate` with that original `stored_decision`.

`returned` is included in the `Event` status contract, database constraints and `STATUSES`. `snapshot()` groups current orders by store and counts each status, including returned. Updating an existing order changes its bucket while keeping its total at one; adding o2 creates a second order under s2.

**Code proof:** `test_required_fixture_and_retained_identity()` in [tests/test_round2.py](../tests/test_round2.py) exercises this sequence, including store-conflict precedence and retries of rejected events.

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

## Step 3. Newer delivered event

Input:

```powershell
Send-Event -EventId e3 -Version 3 -Status delivered
Show-Counts
```

Expected response: `applied=1`, outcome `applied`.

Expected dashboard: s1 total=1/delivered=1. All other status buckets are zero.

## Step 4. Late lower version

Input:

```powershell
Send-Event -EventId e2 -Version 2 -Status packed
Show-Counts
```

Expected response: `applied=0`, outcome `stale`.

Expected dashboard: s1 total=1/delivered=1. All other status buckets are zero.

## Step 5. Equal version, different ID

Input:

```powershell
Send-Event -EventId equal -Version 3
Show-Counts
```

Expected response: `applied=0`, outcome `stale`.

Expected dashboard: s1 total=1/delivered=1. All other status buckets are zero.

## Step 6. Higher-version correction

Input:

```powershell
Send-Event -EventId e4 -Version 4 -Status packed
Show-Counts
```

Expected response: `applied=1`, outcome `applied`.

Expected dashboard: s1 total=1/packed=1. All other status buckets are zero.

## Step 7. Attempt store change

Input:

```powershell
Send-Event -EventId e5 -StoreId s2 -Version 5
Show-Counts
```

Expected response: `applied=0`, outcome `store_conflict`.

Expected dashboard: s1 total=1/packed=1; no s2 order. All other status buckets are zero.

## Step 8. Store conflict precedes stale check

Input:

```powershell
Send-Event -EventId lower-other -StoreId s2
Show-Counts
```

Expected response: `applied=0`, outcome `store_conflict` despite lower version.

Expected dashboard: s1 total=1/packed=1. All other status buckets are zero.

## Step 9. Another order, returned

Input:

```powershell
Send-Event -EventId e6 -OrderId o2 -StoreId s2 -Status returned
Show-Counts
```

Expected response: `applied=1`, outcome `applied`.

Expected dashboard: s1 total=1/packed=1; s2 total=1/returned=1. All other status buckets are zero.

## Step 10. Retry retained stale event

Input:

```powershell
Send-Event -EventId e2 -Version 2 -Status packed
Show-Counts
```

Expected response: outcome `duplicate`, stored_decision `stale`, applied=0.

Expected dashboard: Unchanged. All other status buckets are zero.

## Step 11. Retry retained store conflict

Input:

```powershell
Send-Event -EventId e5 -StoreId s2 -Version 5
Show-Counts
```

Expected response: outcome `duplicate`, stored_decision `store_conflict`, applied=0.

Expected dashboard: Unchanged. All other status buckets are zero.

## Automated proof

```powershell
.\.venv\Scripts\python -m pytest tests/test_round2.py::test_required_fixture_and_retained_identity -v
```

Expected: 1 passed. The automated test covers the complete sequence on an isolated database.
