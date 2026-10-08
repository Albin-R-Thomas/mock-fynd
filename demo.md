# End-to-end demo: shared orders, live updates and safe retries

Follow the numbered walkthrough below in order. The script first runs automated scenarios; you then demonstrate a live update yourself. Keep its launcher terminal open throughout the demo.

## 1. Understand the three ports

| Port | Role in this demo |
|---|---|
| 8011 | API writer: receives order updates |
| 8012 | Another API writer: receives updates and competing requests |
| 8013 | Reader: serves the dashboard and live SSE feed |

All three run the same application and support both API and SSE endpoints. Their roles come from how `scripts/demo.py` uses them. `--ports` accepts three ports; the launcher passes each to Uvicorn and uses the third for dashboard and `/stream` reads.

They share one local SQLite database. SSE (Server-Sent Events) keeps a browser connection open and sends refreshed dashboard data every second. Updates written through 8011 or 8012 therefore appear through 8013.

`event_id` identifies an update; `order_id` identifies the order; `version` tells us which update is newer. Ten updates to the same order still count as one order.

## 2. Prepare three terminals

The main walkthrough uses **PowerShell**. Open three terminals, call them A, B and C, and run `cd C:\Code\mock-fynd` in each. If your prompt looks like `$`, you may be using Git Bash: switch to PowerShell or use the Git Bash commands in step 9. Do not mix shell syntax.

In Terminal A:

```powershell
cd C:\Code\mock-fynd
if (-not (Test-Path .venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m pip check
```

These commands call the virtual environment's Python directly; activation is optional. No Docker or Redis setup is needed for this demo.

## 3. Start the demo — Terminal A

Stop any previous demo with Ctrl+C in its launcher first, so ports 8011–8013 are free.

```powershell
$round2Demo = Join-Path (Get-Location) ('.round2-demo-' + [guid]::NewGuid().ToString('N'))
Write-Host "Demo directory: $round2Demo"
.\.venv\Scripts\python scripts/demo.py --directory $round2Demo --ports 8011 8012 8013 --hold
```

Wait for the JSON report followed by:

```text
Three instances remain running for browser confirmation. Ctrl+C stops only these processes.
```

**Keep Terminal A open.** Without `--hold`, the script finishes its checks and stops all three servers. A later curl request would then fail to connect.

The script creates an isolated database, disables mock traffic and does not load `.env`. Before printing its report it applies an initial order, checks duplicate/conflicting events, runs two races, applies a v4 correction through SSE, and kills/restarts writer 8011 to check a safe retry. A traceback or assertion failure means the demo failed; inspect it before presenting.

## 4. Show the race results — Terminal A report

These races already ran automatically. The script uses two threads waiting at a shared barrier, then sends requests to 8011 and 8012.

### Identical race

Find `identical_race` in the JSON report. Look at `results[0].outcome` in its two responses. Exactly one must be `applied` and the other `duplicate`; either server may win.

Both requests contain the same event for order `o2` in store `s2`, status `returned`. One order is saved.

Say: “Both servers received the same update at almost the same time. One saved it, and the other recognized it as already saved.”

### Version race

Find `version_race`. Its first response is for v2 on 8011; its second is for v3 on 8012. Responses are listed in request order, not completion order.

| Request | Update to order o1 | Expected outcome |
|---|---|---|
| First | v2, packed | applied if it commits before v3; otherwise stale |
| Second | v3, delivered | applied |

Both possible sequences are correct:

- v2 commits first: both updates apply; v3 replaces v2.
- v3 commits first: v3 applies; v2 is rejected as stale.

The script checks that the order is delivered after this race. There is still one retained order for `s1`.

Say: “The latest version wins, even if the older request arrives late.”

### Why the final dashboard shows packed

After checking v3, the script sends **v4 = packed** and checks that the SSE reader sees it. A newer version can correct an earlier status, including delivered back to packed.

The final report and browser show v4's packed state. The intermediate delivered state is checked in `scripts/demo.py`; it is no longer the current dashboard state when the script pauses.

## 5. Open the dashboard and SSE feed

In Terminal B:

```powershell
Invoke-RestMethod http://127.0.0.1:8013/dashboard | ConvertTo-Json -Depth 10
Start-Process 'http://127.0.0.1:8013'
```

Before the manual update, expect `s1: total1/packed1` and `s2: total1/returned1`. There are two orders overall.

In Terminal C:

```powershell
curl.exe -N http://127.0.0.1:8013/stream
```

Leave it running. SSE messages contain `data:` lines with dashboard JSON. `-N` makes curl display incoming data immediately. Ctrl+C closes this continuous feed.

## 6. Send a live update — Terminal B

Run this once per fresh demo:

```powershell
$round2Update = @{
    event_id = 'demo-browser-v5'
    order_id = 'o1'
    store_id = 's1'
    version = 5
    status = 'out_for_delivery'
    occurred_at = [DateTime]::UtcNow.ToString('o')
} | ConvertTo-Json -Compress

Invoke-RestMethod http://127.0.0.1:8012/events `
    -Method Post -ContentType 'application/json' -Body $round2Update |
    ConvertTo-Json -Depth 10
```

Expected: `applied: 1` and `results[0].outcome: applied`. In the browser and Terminal C, `s1` changes to `total: 1`, `packed: 0`, `out_for_delivery: 1`.

Say: “I wrote through 8012. The separate reader on 8013 picked up the change without refreshing the browser.”

## 7. Retry through the other writer — Terminal B

Reuse the exact `$round2Update` variable. Do not rebuild it, because a new timestamp would change the payload.

```powershell
Invoke-RestMethod http://127.0.0.1:8011/events `
    -Method Post -ContentType 'application/json' -Body $round2Update |
    ConvertTo-Json -Depth 10
```

Expected: `applied: 0`, `outcome: duplicate`, `stored_decision: applied`. Counts stay the same.

Say: “A client can safely retry the same update through another server. The shared database remembers that it was committed.”

## 8. Show recovery, save evidence and stop

Show `api_restart_retry: duplicate` in Terminal A's report. The script killed writer 8011, restarted it against the same database and retried the committed v4 event. Its identity survived restart.

For transaction-boundary and lock checks, run in Terminal B:

```powershell
.\.venv\Scripts\python -m pytest -q tests/test_round2.py -k 'whole_batch_exception or lost_ack or actual_process_kill'
.\.venv\Scripts\python -m pytest -q tests/test_round2.py -k 'recognized_lock or lock_exhaustion or connection_recreation'
.\.venv\Scripts\python -m pytest -q
```

Expected: selected tests and the full suite pass. These tests use isolated data. Before commit, failures roll back the entire batch. After commit, saved data survives and identical retries are duplicates. Recognized database lock failures retry up to four attempts; exhaustion returns HTTP 503. The process-kill tests synchronize with private test hooks; the ordinary demo restart alone does not prove transaction-boundary behavior.

Keep the directory printed in step 3. It contains `evidence.json`, `shared.db`, and `server-0.log` / `server-1.log` / `server-2.log` for ports 8011 / 8012 / 8013 respectively. To inspect evidence from Terminal B, substitute your actual directory:

```powershell
Get-Content 'C:\Code\mock-fynd\.round2-demo-YOUR-ID\evidence.json' -Raw
```

Evidence describes the automated run before your manual v5 update; later API calls do not rewrite it. Press Ctrl+C in Terminal C, then in Terminal A. Data and logs remain. Use a fresh directory next time; the script refuses to overwrite an existing demo database.

## 9. Git Bash equivalents

If you prefer Git Bash, use these instead of the PowerShell walkthrough. The virtual environment must already exist; create it with `python -m venv .venv` if needed.

Terminal A:

```bash
cd /c/Code/mock-fynd
./.venv/Scripts/python.exe -m pip install -r requirements.txt
demo_dir=".round2-demo-$(date +%s)-$RANDOM"
echo "Demo directory: $demo_dir"
./.venv/Scripts/python.exe scripts/demo.py --directory "$demo_dir" --ports 8011 8012 8013 --hold
```

Wait for the hold message and open http://127.0.0.1:8013 in your browser. Terminal C:

```bash
curl.exe -N http://127.0.0.1:8013/stream
```

Terminal B: create the payload once, then send and retry the same bytes:

```bash
cd /c/Code/mock-fynd
update=$(./.venv/Scripts/python.exe -c 'import datetime,json; print(json.dumps(dict(event_id="demo-browser-v5", order_id="o1", store_id="s1", version=5, status="out_for_delivery", occurred_at=datetime.datetime.now(datetime.timezone.utc).isoformat())))')
curl.exe -sS http://127.0.0.1:8012/events -H 'Content-Type: application/json' --data-raw "$update"
curl.exe -sS http://127.0.0.1:8011/events -H 'Content-Type: application/json' --data-raw "$update"
curl.exe -sS http://127.0.0.1:8013/dashboard | ./.venv/Scripts/python.exe -m json.tool
```

Expected: first request applied, second duplicate, then s1 total1/out_for_delivery1. For test commands, use `./.venv/Scripts/python.exe -m pytest` with the same arguments as step 8.

## 10. Troubleshooting

| Symptom | Resolution |
|---|---|
| curl cannot connect to 8013 | Start the launcher with `--hold`, wait for its success message and keep Terminal A open. Inspect any startup traceback. |
| Address already in use | Stop your previous demo in its launcher terminal before starting another. |
| Existing database rejected | Generate a fresh directory as in step 3. Preserve old evidence. |
| Manual v5 returns duplicate | It was already sent. Start a fresh demo to repeat the walkthrough. |
| Manual v5 returns event_id_conflict | That event ID was reused with changed content. Start fresh and reuse the exact original payload for retries. |
| Dashboard shows packed after startup | Expected: the script already applied v4 after the v3 race. |
| Redis health is degraded | Redis is optional. The demo points to port 6399; database ingestion, dashboard and SSE still work without Redis. |
| curl keeps printing | Expected: SSE is continuous. Stop it with Ctrl+C. |

The remaining sections provide delivery guidance, design detail, presentation timing and historical verification.

## What to deliver

1. Supply the project patch, including application, dashboard, configuration, tests and demo scripts. Review it with `git diff` and `git status --short`; remember that untracked files do not appear in `git diff`. Local startup commands are in step 3 and [README.md](README.md).
2. Supply the tests and actual results, including unresolved failures. Historical verification is recorded below; rerun before submission.
3. Include [ROUND2-NOTES.md](ROUND2-NOTES.md): design choices, transaction boundary, limitations, setup/run/test commands and remaining production work.
4. Present the five-minute walkthrough below: normal update, concurrency, retries and recovery. Keep the generated `evidence.json` and server logs for review.

## Understand the design

An event describes an order at a source version. The greatest accepted version controls current state; arrival time and `occurred_at` do not control ordering. Higher versions can skip numbers or correct a delivered order back to packed. The first accepted store for an order is immutable.

Event identity is checked first. An identical event ID and complete normalized payload returns `duplicate`; reusing the ID with changed content returns `event_id_conflict`. For a new event ID, a different store returns `store_conflict`, then an equal/lower version returns `stale`, otherwise it returns `applied`. First-seen rejected decisions are also persisted. `stored_decision` reports the original decision when a request is retried.

The transaction starts with SQLite `BEGIN IMMEDIATE` **before** reading identity or current state. The entire batch is processed in request order and commits event decisions and order changes together, before acknowledgement. A precommit failure rolls back the whole batch. Recognized BUSY/LOCKED errors retry the rolled-back transaction up to four attempts; exhaustion returns HTTP 503. A commit followed by a lost acknowledgement survives: retry the same payload and receive duplicates.

Three independent HTTP processes share one absolute local SQLite file. Dashboard counts come directly from current order rows; Redis and snapshots are supplementary. SSE refreshes shared reads every second. Counts represent retained orders, so ten updates to one order still count as one order.

## Five-minute presentation

| Time | Show and say |
|---|---|
| 0:00-0:45 | Show the launcher command and database path in `evidence.json`. Explain two writers on 8011/8012 and an independent reader on 8013, all sharing the same file. The initial `e1` open update applies; an identical retry is duplicate; changed content under `e1` conflicts. These assertions are in `scripts/demo.py`. |
| 0:45-1:45 | Show `identical_race`: exactly one applied and one duplicate. Show `version_race`: final v3 is delivered whether v2 applies first or arrives too late and is stale. Both events may apply when v2 commits first; the invariant is final v3 and one retained order. The script then applies v4 to correct the order to packed. |
| 1:45-2:30 | Follow steps 5–7: open the browser/SSE reader, send v5 through 8012 and retry through 8011. Show measured dashboard/SSE latency from evidence; the under-five-second checks describe a controlled fixture, not a throughput SLA. |
| 2:30-3:45 | Show the recovery test results from step 8. Explain that a precommit exception or synchronized process kill leaves no partial batch, whereas a kill after commit preserves the whole batch. A lost acknowledgement requires a safe identical retry. |
| 3:45-4:30 | Show `api_restart_retry: duplicate`: the script kills and restarts writer 8011, then retries committed e4. Persisted identity survives process restart. The kill-at-transaction-boundary tests establish the separate precommit/postcommit cases. |
| 4:30-5:00 | Show the full test result and `ROUND2-NOTES.md`. State the local one-host SQLite limit, fresh-schema requirement, remaining production work and any unresolved verification gaps. |

## Historical verification from the previous guide

Previously recorded on 2026-10-07 in this Windows checkout. These are historical results, not new results from this rewrite; rerun the commands for your presentation:

| Command | Actual result |
|---|---|
| `.\.venv\Scripts\python -m pytest -q` | **PASS: 36 passed, 1 warning in 24.36s** |
| `.\.venv\Scripts\python scripts/demo.py` | **PASS: exit 0**; three HTTP processes, update/identity assertions, races, live SSE and API kill/restart assertions completed |

The demo measured dashboard visibility **0.375s** and SSE visibility **0.610s**. The identical race produced applied/duplicate; this version race applied v2 then v3. Final counts were s1 packed1 and s2 returned1; restart retry returned duplicate. Evidence was written to `C:\Users\albin_personal\AppData\Local\Temp\round2-demo-wzhb23sp\evidence.json`. The non-held run stopped its processes on completion.

No automated failures were reported in those runs. The warning concerned Starlette's deprecation of its httpx TestClient integration. Manual browser visual confirmation was not performed in that recorded verification; complete the browser step during rehearsal. Docker, production load and power-loss behavior were not reverified there. Earlier checks in `ROUND2-NOTES.md` are historical results.

## Questions to prepare for

- **Why `BEGIN IMMEDIATE`?** It obtains write ownership before decision reads, preventing concurrent writers from making decisions against the same obsolete state.
- **What if the client times out?** The client may not know whether commit happened. Retry exactly the same event IDs and payloads; committed decisions return duplicate, rolled-back work can apply.
- **Does returned remove an order?** No. It remains in total and returned counts; delivered/returned are inactive for summaries.
- **Can this run across hosts?** This implementation requires one shared local file on one host. Production needs a database and deployment design for multiple hosts.
- **What remains?** Authentication/authorization, migrations/backfill, source ID/version ownership and reconciliation, retention, backups/restore and power-loss checks, observability, dependency locking and load testing. Source timestamps can misrepresent delay age; process-kill tests do not establish hardware durability.
