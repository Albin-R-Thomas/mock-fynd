# Round 2 implementation notes

## Design and transaction boundary

SQLite on one shared absolute local file; independent SQLAlchemy engines/connections per service. Fresh schema: constrained relational `order_events` with unique event ID and original decision/payload; `current_orders` with primary-key order ID, explicit latest state and `status_since`; existing snapshot history retained. Versions are canonical positive decimal TEXT (constraint rejects nondigits/leading zero), converted to integer for authority comparisons, supporting values beyond SQLite INTEGER range. API versions remain strict positive integers.

`BEGIN IMMEDIATE` obtains write ownership **before** identity/order reads, so two processors cannot both decide against an absent/obsolete row. A batch applies in request order. First-seen decisions and state updates commit together. Exceptions before commit roll the entire batch back; only SQLite BUSY/LOCKED retries a complete rolled-back batch, up to four attempts, with 0.2s connection lock timeout and 0.05/0.10/0.15s backoff. Exhaustion gives 503. Other SQL failures give explicit 503 without arbitrary retry. After-commit hooks are outside rollback/retry scopes; lost acknowledgements remain committed and an identical retry returns duplicate plus original `stored_decision`.

WAL permits readers during a writer; synchronous FULL protects acknowledged commits to SQLite's local-storage guarantees, contingent on filesystem/hardware honoring flushes. The process-kill tests establish software crash recovery, not power-loss or faulty storage guarantees. Serial write ownership protects all interleavings, beyond the finite races executed. No in-memory state/cache controls decisions or dashboard counts.

Dashboard uses one aggregate SELECT for total/five status counts/delays. `/dashboard`, `/summary`, `/events` and SSE use shared database reads. SSE refreshes every second independently of background mock/Redis work. Failed dashboard/summary/initial stream reads return 503; established SSE emits unavailable and retries each second. Cached values are not served by APIs; browser retained values are labelled stale. Historical snapshot insertion order need not equal freshness order.

## Implementation sequence and files

1. `app/main.py`: strict contract, five statuses, fresh constrained tables; atomic serialized batches and bounded lock retry.
2. `app/main.py`: authoritative aggregate reads, shared history, direct SSE, updated explicit-version mock events.
3. `tests/test_api.py`: retain five original test purposes, update incompatible contract assertions.
4. `tests/test_round2.py`, `scripts/crash_worker.py`: fixture matrix, independent connections/races, private injected failures and synchronized genuine process kills.
5. `scripts/demo.py`, `tests/test_http_instances.py`: three actual HTTP processes, dashboard/SSE latency, races, API process kill/restart and retry.
6. `app/dashboard.html`: returned counts and stale/unavailable presentation. `compose.yaml`, `.env.example`: SQLite path and default paused fixtures. `README.md`, this file, `.gitignore`: execution, limits, demo/evidence and generated artifact ignores.

## Verified environment and exact executed checks

Windows PowerShell, existing `.venv`, Python 3.11.9. Baseline `.\.venv\Scripts\python -m pytest -q`: **5 passed**, one Starlette/httpx deprecation warning. `.\.venv\Scripts\python --version`; `.\.venv\Scripts\python -m pip check`: no broken requirements. `.\.venv\Scripts\python -m pip install -r requirements.txt`: requirements already satisfied (FastAPI 0.142.2, SQLAlchemy 2.1.1, Pydantic 2.13.5, Uvicorn 0.54.0, Redis 6.4.0, pytest 8.4.2). No dependency changes.

Expanded `.\.venv\Scripts\python -m pytest -q`: **36 passed**, one existing deprecation warning, 30.91s. Earlier intermediate run: 2 failed/34 passed (demo duplicated keyword and old snapshot test's freshness assumption); both corrected. Final repeat: **36 passed**, one warning, **28.45s**; final `pip check` found no broken requirements and `git diff --check` passed (only a Git LF/CRLF advisory). No unresolved automated failures.

`docker info`: daemon available, unlike earlier inspection. `docker compose config --quiet`: valid. `docker compose up --build -d`: built/started SQLite API and Redis. `docker compose ps`: API running, Redis healthy. `Invoke-RestMethod http://127.0.0.1:8001/health`: status ok, database/Redis true. `Invoke-RestMethod http://127.0.0.1:8001/dashboard`: authoritative empty counts initially.

Container persistence check executed:

```powershell
$round2Payload = @{event_id='compose-check';order_id='compose-order';store_id='compose-store';version=1;status='returned';occurred_at='2026-01-01T00:00:00Z'} | ConvertTo-Json -Compress
Invoke-RestMethod -Uri http://127.0.0.1:8001/events -Method Post -ContentType 'application/json' -Body $round2Payload
# applied=1
docker compose restart api
Invoke-RestMethod -Uri http://127.0.0.1:8001/events -Method Post -ContentType 'application/json' -Body $round2Payload
# duplicate; applied=0
Invoke-RestMethod http://127.0.0.1:8001/dashboard
# compose-store total=1 / returned=1
```

`.\.venv\Scripts\python scripts/demo.py --directory .round2-browser --hold --ports 8011 8012 8013` executed. Three actual native processes: dashboard visibility **0.938s**, live SSE visibility **0.265s**, safe identical-event race, final v3 delivered then v4 correction to packed, API kill/restart retry duplicate. Reader counts s1 total1/packed1; s2 total1/returned1. Generated `.round2-browser/evidence.json` and logs are ignored local artifacts, not required committed fixtures. The automated HTTP test independently repeats this workflow in a temporary directory.

**Evidence gap:** browser visual confirmation was attempted but the computer-use tool reported `Browser unavailable` and no enabled browser/app surfaces. HTTP HTML/SSE behavior was tested; do not claim visual confirmation. Complete the manual browser step below during review. No PostgreSQL, power-loss, network-filesystem or production-load tests performed. Existing unrelated/orphan Docker database container was not removed; Compose API/Redis were left running for review. Owned held native demo processes were subsequently stopped using `taskkill /PID 15144 /T /F` after verifying that PID was the `.round2-browser` demo launcher; no unrelated processes were stopped.

## Required-evidence coverage and changed original assertions

| Evidence | Test |
|---|---|
| e1 retry and conflicting identity; e3 delivered/e2 stale; equal v3; v4 correction; e5 store boundary; e6 returned | `test_required_fixture_and_retained_identity` |
| Stale/store-conflict original decision and payload retained; immutable event IDs and store precedence over low version | Same fixture test |
| Strict positive versions/required IDs/timestamps/unknown fields; UTC identity/exact IDs | Validation and normalization tests |
| Independent-connection identical event race | `test_independent_connection_identical_race` |
| Concurrent v2/v3, both input permutations, final v3/count1 | `test_independent_connection_version_race` |
| Whole-batch precommit exception rollback and successful retry | `test_batch_order_and_whole_batch_exception_rollback` |
| Commit succeeds but acknowledgement lost; no retry of hook | `test_after_commit_lost_ack_no_rollback_or_retry` |
| Connection recreation, new service, independent reader | `test_connection_recreation_and_service_restart` |
| Genuine synchronized process kills before/after commit | `test_actual_process_kill` (two cases) |
| Full transaction lock retry and bounded explicit exhaustion | Lock tests |
| Same-status status_since; old timestamps permitted with higher version; returned inactive | Metadata test and original delay test |
| API-level unavailable reads/writes and initial SSE failure | `test_database_failure_explicit_http` |
| Three actual HTTP instances, GET/SSE under five seconds, process restart | `test_three_http_process_dashboard_sse_and_restart` |

All five original test functions remain. Explicit versions/timestamps replace defaults. Timestamp stale/regression assertions become version stale and permitted higher-version corrections. Counts are asserted via shared reads rather than removed process-local dictionaries. Missing timestamp is now 422. Restart checks persisted orders/events rather than replayed memory. Historical snapshots are checked for the produced scenario, not assumed to be fresher than direct reads or to have identical generation timestamps.

## Reproducible setup/run/test and failure lab

See README PowerShell setup. No Docker prerequisite for SQLite. Use one fresh absolute path for all native instances and `MOCK_AUTOSTART=false`. Reproduce failures separately:

```powershell
.\.venv\Scripts\python -m pytest -q tests/test_round2.py -k 'whole_batch_exception'
.\.venv\Scripts\python -m pytest -q tests/test_round2.py -k 'lost_ack'
.\.venv\Scripts\python -m pytest -q tests/test_round2.py -k 'connection_recreation'
.\.venv\Scripts\python -m pytest -q tests/test_round2.py -k 'actual_process_kill'
.\.venv\Scripts\python -m pytest -q tests/test_http_instances.py -s
```

The crash test starts `scripts/crash_worker.py` with constructor-only callbacks, waits for a ready-file signal exactly at precommit or postcommit/preack, then calls `Popen.kill()` and waits for exit. Recreating the service verifies no precommit events/orders survived; after-commit batch decisions/state survive and retry produces only duplicates. The worker never acknowledges. Injection is disabled by default: hooks are only explicit Python constructor callbacks, no environment switch/request field/header/route. Ordinary requests cannot enable them. These are lab controls, not an authentication boundary for arbitrary code execution.

## Five-minute demonstration

Start a fresh isolated directory before the presentation (no implementation time limit):

```powershell
$round2Demo = Join-Path (Get-Location) ('.round2-demo-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python scripts/demo.py --directory $round2Demo --ports 8011 8012 8013 --hold
```

- **0:00â€“0:45:** Explain three independent processes/shared absolute file and transaction ownership. Show `evidence.json` path and the exact command; script performs a normal update, retry and conflicting identity.
- **0:45â€“1:45:** Show concurrent identical event results (one applied/one duplicate), v2/v3 final delivered and v4 correction; counts are retained orders, not event count.
- **1:45â€“2:30:** Open http://127.0.0.1:8013 manually; confirm s1 packed1, s2 returned1, total2 and five status columns. In a second terminal, POST a fresh v5 `out_for_delivery` event to port8012; watch independent reader SSE change within five seconds. Confirm recorded GET/SSE latency, not a production SLA.
- **2:30â€“3:45:** Run `pytest -q tests/test_round2.py -k 'whole_batch_exception or lost_ack or actual_process_kill'`; explain exception rollback versus actual kill and committed lost acknowledgement.
- **3:45â€“4:30:** Show API restart/retry evidence and `pytest -q tests/test_round2.py -k connection_recreation`. State what each establishes.
- **4:30â€“5:00:** Show complete test results, SQLite limitations, metadata caveat, browser verification status and remaining production work. Ctrl+C cleans up owned native processes; keep the database/logs for review.

The second-terminal browser update command:

```powershell
$round2Update = @{event_id='demo-browser-v5';order_id='o1';store_id='s1';version=5;status='out_for_delivery';occurred_at='2026-01-01T00:00:00Z'} | ConvertTo-Json -Compress
Invoke-RestMethod -Uri http://127.0.0.1:8012/events -Method Post -ContentType 'application/json' -Body $round2Update
```

## Limitations / remaining production work

SQLite writes serialize; local filesystem/one host only, no network share or cross-host SQLite replication. Reads are authoritative but long queries, slow storage, large batches or load can exceed the controlled local freshness objective. Snapshot history grows per process/second; orders/events also grow without retention. Summary timestamps and `status_since` come from source metadata and may misrepresent real delay after corrections/unreliable clocks. Multiple mock generators are not the one trusted source; leave them off for contract demonstration. Fresh database only; no migration/backfill. Endpoints are unauthenticated local demo APIs. Schema initialization expects normal local startup; production migrations/coordination are pending. Dependency ranges are not a lockfile; one known deprecation warning remains.

Production needs authentication/authorization, source reconciliation and ID/version ownership, migrations, backup/restore and power-loss testing, retention/archive policy, observability, load testing and multi-host database/deployment design. PostgreSQL dependency is retained from the original requirements but PostgreSQL URLs are explicitly unsupported here. Optional extensions deferred. No commit created; candidate brief preserved. No unresolved core contract decisions; manual browser confirmation remains an environment-blocked delivery check.


## Environment configuration follow-up

Native startup now explicitly uses Uvicorn `--env-file .env`. No application import loads dotenv, and Uvicorn's default `load_dotenv` preserves shell variables. `uvicorn[standard]` already supplies python-dotenv; requirements and demo/test launchers are unchanged. Local ignored `.env` contains an absolute local SQLite URL, paused mock traffic, Redis URL, rate and summary thresholds. `.env.example` has portable examples; no machine-specific values are committed. README includes UTF-8 without BOM setup and separate commands for ports 8011/8012/8013, plus POST/retry/reader verification.

Compose explicitly retains `sqlite:////data/operations-v2.db`, `redis://redis:6379/0` and the named SQLite volume. Only mock autostart/rate and summary thresholds interpolate from shell/.env. Windows DATABASE_URL is never injected into a container. Compose syntax/rendering was checked separately; this follow-up did not recreate the running Docker services.

Commands executed during this follow-up:

```powershell
.\.venv\Scripts\python -m uvicorn --help
git ls-files .env.example
git check-ignore .env
docker compose config --quiet
docker compose config
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m pip check
git diff --check
.\.venv\Scripts\python -c "from uvicorn import Config; import os; Config('app.main:app', env_file='.env'); print('DATABASE_URL='+os.environ['DATABASE_URL']); print('MOCK_AUTOSTART='+os.environ['MOCK_AUTOSTART'])"
```

Read-only inspection also used `git status --short`, `Get-Content` on configuration/docs/demo, `rg` for environment loading, and Python `inspect.getsource` on Uvicorn Config. Inline Python subprocess verification executed the native command below on automatically selected unused ports (three independent instances), with supported shell settings removed so loading came from `.env`:

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port <allocated-port>
```

Results: Uvicorn logged environment loading; configured absolute file and disabled mock verified. Processor A applied a unique synthetic event, processor B returned duplicate, independent reader returned its authoritative count, and direct SQLite inspection found the stored event in the configured file. This leaves a synthetic `env-check` order in the ignored native database. A separate startup with explicit temporary DATABASE_URL, rate=7 and Redis port6399 preserved these overrides and used an empty isolated database. Owned verification processes were stopped; temporary override artifacts were retained outside the repository. Two initial ad hoc cleanup attempts raised Windows file-lock errors after successful assertions; the retained-artifact rerun passed and explicitly stopped its process tree. These were harness cleanup errors, not automated suite failures.

Final suite: **36 passed, one existing Starlette/httpx warning, 25.17s**. `pip check`: no broken requirements. `.env` ignored, `.env.example` tracked. Compose validated and rendered the intended container paths/volume plus supported thresholds. `git diff --check` passed. Event contract, schema, transactions and original implementation changes were untouched. No commit created. Prior manual browser confirmation gap remains unchanged.

## Frontend and verification lab addition

Added a separate React/Vite frontend for Vercel with a store overview and a 16-case verification lab. Every case executes allowlisted backend tests in an isolated temporary database; Run all executes sequentially and exports actual results. The original transactional implementation remains the authority. New middleware protects mutations when API_SECRET is set. Optional public-demo settings limit traffic, requests and historical snapshots without deleting retained order/event truth.

Vercel serves static assets and a same-origin server-side proxy; SQLite stays on a persistent backend host. render.yaml provides an optional single-instance Docker deployment with a persistent disk. Tests and worker scripts are included in the image. DEPLOYMENT.md records setup, limits, hosting steps and the five-minute demonstration.

Latest results: 46 Python tests passed on Windows and 46 passed in the Linux Docker image; 2 proxy and 4 frontend interaction tests passed; all 16 lab cases passed through the local frontend API route; production build passed; npm audit reported zero vulnerabilities. See VERIFICATION-RESULTS.md for timings and limitations. Visual browser review and public hosting verification remain outstanding because no browser was connected, Vercel was signed out and no public backend was provided. No production deployment or Git commit was created.
