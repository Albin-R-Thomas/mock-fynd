# Case 7: Three HTTP processes, SSE and complete test evidence

## How we achieve this in the codebase

`run_demo()` in [scripts/demo.py](../scripts/demo.py) launches three separate Uvicorn processes with the same absolute SQLite `DATABASE_URL`, mock traffic disabled and an unavailable test Redis endpoint. Two processes receive writes and the third reads the dashboard and SSE stream. The script waits for database health before sending requests and uses a thread barrier to race writes through real HTTP connections.

Cross-process visibility comes from the database. In [app/main.py](../app/main.py), `/dashboard` calls `shared_snapshot()` to aggregate committed current orders directly. `/stream` sends an initial database snapshot, then reads again every second. It does not depend on another process's in-memory state or Redis publication. The browser's `EventSource` in [app/dashboard.html](../app/dashboard.html) renders stream messages and marks retained values stale on unavailable or connection-error events.

The demo measures dashboard visibility after the initial write response and SSE visibility after a correction response, asserting each checked update arrives in under five seconds. It then kills and restarts a writer against the same file and verifies that retrying its committed event is a duplicate. It saves counts, race outcomes and timings to `evidence.json`, retains server logs, and cleans up its owned processes on exit.

**Code proof:** [tests/test_http_instances.py](../tests/test_http_instances.py) invokes `run_demo()` with a temporary directory and checks its reported timings. The full pytest command combines this integration proof with the model, API, concurrency and crash tests. `--junitxml` records the actual test outcomes; browser appearance is still checked manually. The measured fixture timings are evidence for these checked updates, not a general throughput guarantee.

## Step 1. Prepare Python

```powershell
cd C:\Code\mock-fynd
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -r requirements.txt
```

These tests create isolated temporary databases and disable mock traffic. No running API is required.

## Step 2. Three real HTTP processes, dashboard and SSE

```powershell
.\.venv\Scripts\python -m pytest tests/test_http_instances.py::test_three_http_process_dashboard_sse_and_restart -v -s
```

Input: the demo launches three independent HTTP servers on one fresh local database, submits initial/retry/conflicting requests, races identical returned events and v2/v3 updates, applies a v4 packed correction, then kills/restarts one API process and retries its committed event.

Expected:

- Identical race: one applied and one duplicate.
- Version race: o1 reaches v3/delivered before the v4 correction.
- Final s1 total=1/packed=1; s2 total=1/returned=1.
- Independent dashboard and SSE reader each observe their checked update in less than five seconds.
- Retry after writer restart is duplicate; counts remain unchanged.

To retain the report and logs for a presentation:

```powershell
$demoEvidence = Join-Path $PWD ('.round2-demo-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python scripts/demo.py --directory $demoEvidence --ports 8011 8012 8013 --hold
```

The script prints its report and writes `evidence.json` inside that directory. In another terminal, open `http://127.0.0.1:8013` and compare the displayed counts with the report. Browser appearance requires manual confirmation; the automated test checks HTTP/SSE behavior. Ctrl+C in the launcher stops its owned processes.


## Step 3. Complete suite and evidence

```powershell
New-Item -ItemType Directory -Force demo-test | Out-Null
.\.venv\Scripts\python -m pytest -v --junitxml=demo-test/results.xml
if ($LASTEXITCODE -ne 0) { throw 'FAIL: test suite failed; inspect the pytest output' }
Write-Host 'PASS: all automated cases passed; evidence: demo-test/results.xml' -ForegroundColor Green
```

The current suite collects 36 cases, including parameterized validation, writer permutations and crash phases. Expected: all cases pass. `results.xml` records the actual outcome of this run; expected results in this document are not a substitute for executing the tests. Pytest fixtures use temporary databases and disable mock traffic.

For the manual retry and version scenarios, follow files 01 and 02; each explains how to start its own fresh database.

## Final check

Each pytest command must finish with all selected tests passed and exit code 0. An assertion failure means the expected result was not met. The existing Starlette/httpx deprecation warning may appear.
