# Deploy the operations console

The React frontend lives in `frontend/` and deploys to Vercel. Its same-origin API proxy calls the existing Python backend. Keep SQLite on one host with a persistent local disk; Vercel's ephemeral filesystem cannot satisfy the brief's recovery requirement.

## Local preview

Create `.env` from `.env.example` if needed and set `ADMIN_USERNAME` and `ADMIN_PASSWORD` before starting. These are required backend secrets; missing credentials deny sign-in. From the repository root, start the backend in one PowerShell terminal:

```powershell
.\.venv\Scripts\python -m pip install -r requirements.lock
$env:VERIFICATION_ENABLED='true'
$env:MOCK_AUTOSTART='false'
.\.venv\Scripts\python -m uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8001
```

Start the frontend in a second terminal:

```powershell
cd frontend
npm.cmd ci
$env:BACKEND_URL='http://127.0.0.1:8001'
npm.cmd run dev
```

Open `http://127.0.0.1:5173`. Use **Start mock traffic** at the top of Overview to create new orders across the mock stores. **Verification lab** runs each case on demand, or all 16 sequentially. Export evidence downloads the actual results from that browser session. Reports are not retained after a page reload; export them before leaving.

Vite reads shell variables in this configuration. `frontend/.env.example` documents settings; copying it alone does not configure the local proxy. Vercel reads its project environment settings.

## Persistent backend

`render.yaml` is a Render Blueprint for a Docker service with one instance and a 1 GB persistent disk. This requires a paid service/disk; it has not been provisioned automatically. Use an existing Linux host with a durable local `/data` volume instead if preferred.

1. Push this checkout to the GitHub repository after reviewing the existing uncommitted work.
2. In Render, create a Blueprint from that repository and review the service/disk cost before provisioning.
3. Render builds the root Dockerfile. It copies `app`, `tests` and `scripts`, because the lab executes allowlisted tests on the deployed backend.
4. Set the required `ADMIN_USERNAME` and `ADMIN_PASSWORD` secrets in Render. Record the HTTPS service URL. The Blueprint enables `AUTH_COOKIE_SECURE=true` for production session cookies.
5. Confirm public `GET /health/live` responds successfully. After signing in, `GET /health` reports database/cache details. Redis is optional; degraded cache health does not prevent database access.

The Blueprint fixes `DATABASE_URL=sqlite:////data/operations-v2.db`, enables verification, limits mutations to 40/minute for the whole service, caps traffic at 5 events/second, pauses traffic after 300 seconds, and retains 300 supplementary snapshots. Retained order/event history is not deleted. Monitor disk usage and take backups; repeated demos grow that history.

Run exactly one API worker with this deployment: the verification semaphore and demo rate limit are process-local. The independent workers used by the tests are created inside isolated test jobs. Horizontal replicas cannot share this SQLite file. Moving to multiple backend hosts requires a separate database design.

## Vercel frontend

1. Import the GitHub repository into Vercel.
2. Set **Root Directory** to `frontend` and framework to **Vite**.
3. Add `BACKEND_URL`, the backend HTTPS origin without a path, to Production and Preview environment settings.
4. Deploy. Build is `npm run build`; output is `dist`. `vercel.json` sets a 120-second function duration so the multi-process case can finish.
5. Open the deployment, start mock traffic, run all verification cases and export the evidence. Confirm the dashboard continues to refresh during a lab job.

Or, with an authenticated CLI:

```powershell
cd frontend
npx.cmd vercel login
npx.cmd vercel link
npx.cmd vercel env add BACKEND_URL production
npx.cmd vercel --prod
```

The Vercel proxy forwards allowlisted routes, session cookies and CSRF headers, and passes backend Set-Cookie responses back to the browser. The backend validates every session. The old API key does not grant access and is no longer required.

## Administrator sign-in

There is one administrator, `fynd`, using the password supplied for this project. There is no sign-up endpoint or UI. The frontend shows the sign-in screen before mounting the dashboard or starting data requests. The password is never included in the frontend bundle; the backend verifies a salted PBKDF2-SHA256 hash.

Sessions use random opaque tokens in HttpOnly, SameSite=Strict cookies. Only token hashes are stored in SQLite; sessions last eight hours and survive service restarts. Sign-out deletes the database session so replaying its cookie is rejected. Changing ADMIN_USERNAME or the active ADMIN_PASSWORD / ADMIN_PASSWORD_HASH invalidates existing sessions. All operational routes, documentation, verification and detailed health require a session; only POST /auth/login and GET /health/live are public. Mutations require the session's CSRF token, and login requires the application's custom request header. Existing SSE streams recheck their session on each polling cycle.

`GET /auth/session` returns the admin identity and CSRF token. `POST /auth/logout` revokes the session. Login permits at most ten attempts per minute across the deployment, persisted in the shared SQLite database. There is no password recovery or account management UI.

Use `AUTH_COOKIE_SECURE=true` behind HTTPS (already set by render.yaml). Local HTTP uses false. Backend-only `ADMIN_USERNAME` and `ADMIN_PASSWORD` are required in the ignored local `.env` or deployment secret settings. There is no built-in password fallback. Alternatively, `ADMIN_PASSWORD_HASH` takes precedence over ADMIN_PASSWORD; the hash format is `pbkdf2_sha256$iterations$salt_hex$digest_hex`. Never put credentials in VITE_ variables. The local demo and verification tests now sign in normally and send their own session/CSRF credentials; they do not disable authentication.

Implementation references: [OWASP session management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html), [OWASP CSRF prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html).


## Verification and evidence

```powershell
# Repository root
.\.venv\Scripts\python -m pytest -q
# Frontend
cd frontend
npm.cmd test
npm.cmd run build
npm.cmd audit
```

The lab replays actual recorded backend snapshots as an overview-style store dashboard. A compact event log shows attempts, while store counts and order versions reflect persisted state. Replay, pause and previous/next controls let reviewers inspect each step; full pytest output stays in a collapsed report. Run all waits for each replay before starting the next case.

The UI's seven contract buttons invoke separate parameterized fixtures in `tests/test_showcase.py`; output includes requests, decisions and independent reader snapshots. Other buttons invoke the established concurrency/recovery/API tests. The HTTP case starts two writer processes and one reader, measures dashboard/SSE visibility and restarts an API process. Real process-kill tests cover both sides of commit. Temporary databases are removed after each run; failures are reported with actual output and never converted to success.

An unavailable backend produces a visible error; previously loaded counts are labeled stale with the last successful snapshot time. The frontend polls every 2.5 seconds. The five-second fixture objective is measured in the dedicated HTTP test and is not a hosted throughput promise.

## Five-minute demonstration

1. **0:00–0:45** — Open Overview, start mock traffic, inspect each store and explain the total/status invariant. Pause traffic.
2. **0:45–1:45** — Open Verification lab. Run Identical retry and Conflicting identity; inspect the requests, decisions and reader output.
3. **1:45–2:45** — Run Late update, Authoritative correction, Store boundary and Concurrent version race.
4. **2:45–3:45** — Run Failure before commit, Lost acknowledgement and Real process kill. Distinguish injected exceptions from actual worker termination.
5. **3:45–5:00** — Run Three HTTP instances & freshness, inspect timings and restart evidence, then export the result. Run all cases for complete coverage if time allows.

## Remaining release gates

Publishing needs an authenticated Vercel account and a provisioned durable backend. No production URL is claimed until both are configured and the deployed site passes the same HTTP checks. Browser screenshot/viewport inspection also requires an available browser connection; component tests do not replace visual review. The Docker image runs as UID/GID 10001; existing persistent volumes must be writable by that identity before upgrading. See SECURITY-AUDIT.md for current checks; the selected public host still needs its deployment smoke test. The checked-in lockfiles pin the dependencies tested locally. See [VERIFICATION-RESULTS.md](VERIFICATION-RESULTS.md) for actual local results.

References: [Vercel SQLite guidance](https://vercel.com/kb/guide/is-sqlite-supported-in-vercel), [Vercel function duration](https://vercel.com/docs/functions/configuring-functions/duration), [Render Blueprint configuration](https://render.com/docs/blueprint-spec).
