# Shared, recoverable operations dashboard

## React frontend and interactive verification lab

The new Vercel frontend is in `frontend/`. It provides a responsive store dashboard, mock traffic controls, store search/details, status distribution, exception summaries, recent events, and a verification mode with **Run all**, individual case buttons, actual backend output and downloadable evidence. See [DEPLOYMENT.md](DEPLOYMENT.md) for local startup, Vercel configuration, durable backend hosting and the five-minute demonstration.

The website runs real isolated tests; it does not manufacture successful results. The existing SQLite backend stays on a persistent host and Vercel serves the frontend/API proxy. `render.yaml` supplies an optional persistent backend deployment. Existing local API commands below still work.

FastAPI with authoritative SQLite order state, durable event decisions, direct aggregate dashboard reads and one-second SSE refresh. Redis and stored dashboard snapshots are supplementary; neither supplies current dashboard answers. No Docker or Redis is required for native execution.

## Windows / PowerShell

```powershell
cd C:\Code\mock-fynd
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pip check
if (-not (Test-Path .env)) { Copy-Item .env.example .env } # preserve existing settings
$round2Database = Join-Path (Get-Location) 'operations-v2.db'
$round2Settings = Get-Content .env
$round2Settings = $round2Settings -replace '^DATABASE_URL=.*$', ('DATABASE_URL=sqlite:///' + ($round2Database -replace '\\','/'))
[IO.File]::WriteAllLines((Join-Path (Get-Location) '.env'), $round2Settings, [Text.UTF8Encoding]::new($false))
.\.venv\Scripts\python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8001
```

Use a fresh database. Legacy event databases without versions require a separately designed migration and are rejected. The URL resolves to an absolute file path; all processes must use the **same absolute local file**. The ignored `.env` is explicitly loaded by Uvicorn `--env-file .env`; application imports do not load dotenv. Existing shell variables take precedence, so remove unintended shell overrides before startup. `uvicorn[standard]` already supplies `python-dotenv`; no additional dependency is needed. An unavailable Redis marks `/health` degraded while SQL ingestion/dashboard/SSE work.

For three processes, open three PowerShell terminals in the checkout and run one command per terminal using the same `.env`:

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8011
.\.venv\Scripts\python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8012
.\.venv\Scripts\python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8013
```

Use identical explicit overrides in all terminals, or no overrides. Process 8013 can be the independent reader; it still exposes the same API. Tests and `scripts/demo.py` keep their explicit isolated database settings; they do not load `.env`. The automated demo below starts/stops only its own processes and records evidence.

```powershell
.\.venv\Scripts\python -m pytest -q
$round2Demo = Join-Path (Get-Location) ('.round2-demo-' + [guid]::NewGuid().ToString('N'))
.\.venv\Scripts\python scripts/demo.py --directory $round2Demo --ports 8011 8012 8013 --hold
```

Open http://127.0.0.1:8013 for browser confirmation. Ctrl+C ends the demo and stops its processes; the isolated database/logs/evidence remain. Without `--hold`, it verifies and exits. Use a fresh directory every run. See [ROUND2-NOTES.md](ROUND2-NOTES.md) for the five-minute script and failure procedures.

## Docker

```powershell
docker compose config --quiet
docker compose up --build -d
Invoke-RestMethod http://127.0.0.1:8001/health/live
```

Compose now runs SQLite API plus Redis, with durable named volumes and mock traffic disabled. SQLite path inside the container is `/data/operations-v2.db`; Docker binds API to localhost:8001. Native and container databases are separate. Compose interpolates mock/summary settings from `.env` (shell overrides win), but explicitly fixes `DATABASE_URL` and `REDIS_URL` to container paths/addresses. It never passes the Windows SQLite path into the container; `sqlite_data` persists the container database. This is one container, not evidence of independent HTTP instances; the native demo provides that evidence. PostgreSQL is not supported or tested by this patch. Existing unrelated/orphan containers and volumes must not be removed casually. `docker compose stop api redis` stops this stack without deleting data.

## Contract and APIs

```json
{"event_id":"e1","order_id":"o1","store_id":"s1","version":1,"status":"open","occurred_at":"2026-01-01T00:00:00Z"}
```

All six fields are required. Reject unknown fields/statuses, blank IDs, boolean/noninteger/nonpositive versions and timestamps without a timezone. Valid IDs retain their exact spelling; timestamps normalize to UTC before complete-payload identity comparisons. Future/past timestamps are metadata, not order authority. IDs remain bounded at 100 characters, as in the original API.

Identity precedes state decisions: identical ID/payload => `duplicate`; changed payload => `event_id_conflict`, preserving the original. For first-seen IDs, immutable-store violation => `store_conflict`, then equal/lower version => `stale`, otherwise `applied`. Higher versions may correct any status and skip versions. Results preserve `received/applied/results`; `stored_decision` distinguishes the original decision from retry outcome. No current-state diagnostics are returned.

| Route | Behavior |
|---|---|
| `POST /events` | One committed event decision |
| `POST /events/batch` | Up to 5,000 validated events, one atomic batch, request order |
| `GET /events?limit=100` | Shared persisted first-seen events and decisions (max 1,000) |
| `GET /dashboard` | Direct authoritative counts and rule summary |
| `GET /summary` | Current authoritative rule summary |
| `GET /stream` | Direct shared-state SSE, one-second cadence |
| `GET /snapshots?limit=20` | Historical generated snapshots, max 100 |
| `GET /health` | Database/cache/background health |
| `POST /mock/start` | Fixture generator; body `{"events_per_second":50}` |
| `POST /mock/stop` | Stop this instance's generator |
| `POST /mock/seed` | Add explicit-version scenario events |
| `/`, `/docs` | Browser dashboard and API documentation |

`order_events` stores explicit contract columns, validated JSON and immutable first-seen decisions. `current_orders` stores one row/order, latest source version/status/event and `status_since`. Positive versions use constrained canonical decimal text in relational columns to avoid SQLite's signed 64-bit integer ceiling; comparison uses Python integers within the serialized transaction. `dashboard_snapshots` and Redis remain supplemental history/cache.

Each batch acquires `BEGIN IMMEDIATE` before decision reads, writes events and current orders, then commits before acknowledgement. WAL and FULL synchronous writes are configured. Only recognized SQLite BUSY/LOCKED failures retry a fully rolled-back transaction (four attempts with bounded waits). Exhaustion or SQL failure yields HTTP 503; clients may retry the same payload safely. Postcommit test-hook failures are outside retry/rollback scopes.

`total = open + packed + out_for_delivery + delivered + returned` for retained orders. Delivered and returned are inactive for summaries. Backlog defaults to open+packed >=30; delay is out-for-delivery age >30 minutes; imbalance is >=10 active with >=70% open. Same-status updates preserve `status_since`; status changes reset it to source `occurred_at`. Corrections, future timestamps or unreliable clocks can misrepresent delay age.

Dashboard/summary unavailable reads return 503 without cached success. SSE initial failure returns 503; an established stream emits `unavailable` events and resumes direct reads on recovery. Browser retained values are marked stale and show their last update time. Snapshot IDs are insertion order, not necessarily generation/freshness order across concurrent publishers.

SQLite serializes all writers. Use one local filesystem shared by processes on one host, not a network filesystem or replicas on different hosts. The five-second controlled-fixture objective is tested, not a throughput SLA. Production needs migrations, source reconciliation, retention, observability, load testing and a database/deployment design for multiple hosts. Optional extensions are deferred.

## Administrator authentication

Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in the ignored `.env` (deployment secrets in production); there is no default credential fallback. Sign in to the React frontend with the configured `fynd` administrator account. There is no sign-up. Backend operational APIs require a session cookie, and mutations also require a CSRF token. Old API keys no longer grant access. `/health/live` is the only public read endpoint. See [DEPLOYMENT.md](DEPLOYMENT.md#administrator-sign-in) for session behavior and configuration.

For the manual HTTP checks below, obtain a session first (use the port of your running backend):

```powershell
$adminCredential = Get-Credential -UserName fynd -Message 'Operations administrator'
$adminLogin = @{username=$adminCredential.UserName; password=$adminCredential.GetNetworkCredential().Password} | ConvertTo-Json
$adminIdentity = Invoke-RestMethod http://127.0.0.1:8011/auth/login -Method Post -ContentType 'application/json' -Headers @{'X-Requested-With'='fynd-console'} -Body $adminLogin -SessionVariable adminSession
$adminHeaders = @{'X-CSRF-Token'=$adminIdentity.csrf_token}
Remove-Variable adminLogin
```

## Configuration verification

```powershell
git check-ignore .env
git ls-files .env.example
docker compose config --quiet
$envCheck = @{event_id=('env-' + [guid]::NewGuid().ToString('N'));order_id=('env-order-' + [guid]::NewGuid().ToString('N'));store_id='env-check';version=1;status='open';occurred_at='2026-01-01T00:00:00Z'} | ConvertTo-Json -Compress
Invoke-RestMethod http://127.0.0.1:8011/events -Method Post -ContentType 'application/json' -Body $envCheck -WebSession $adminSession -Headers $adminHeaders
Invoke-RestMethod http://127.0.0.1:8012/events -Method Post -ContentType 'application/json' -Body $envCheck -WebSession $adminSession -Headers $adminHeaders # duplicate
Invoke-RestMethod http://127.0.0.1:8013/dashboard -WebSession $adminSession # env-check count includes this order
.\.venv\Scripts\python -m pytest -q
```

The verification creates a synthetic retained order in your local database; repeat with unique IDs. Uvicorn logs `Loading environment from '.env'`; inspect `.env` for the intended absolute path. Git ignores `.env`, database files, WAL/SHM and logs. Never commit machine-specific `.env` values or secrets.
