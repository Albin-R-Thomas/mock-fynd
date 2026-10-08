# Frontend and deployment verification

## Administrator authorization update

Added a sign-in-only administrator flow on the frontend and session authorization on every operational backend endpoint. Anonymous access and legacy API-key attempts are rejected. Sessions use HttpOnly cookies, CSRF tokens, eight-hour expiry and server-side revocation. SQLite persists hashed session tokens and login throttling. The password is stored as a salted PBKDF2 hash on the backend and was confirmed absent from the production frontend assets.

Latest checks: **52 Python tests passed on Windows and 52 passed in Linux Docker (24.34 seconds)**, including unauthorized reads/writes, invalid credentials, CSRF rejection, logout/replayed-cookie rejection, expired/forged sessions, credential invalidation, secure-cookie flags, login throttling and connection recreation. **Eight component tests and three proxy tests passed**, covering sign-in, sign-out, session expiry and forwarded cookie/CSRF headers. All **16 verification cases passed** with normal authenticated clients. The frontend proxy HTTP check verified anonymous rejection, successful sign-in, authenticated execution and logout revocation. Production build, npm audit (zero findings), Docker image build and Compose validation passed.

## Store visualization update

Removed Add scenario and moved mock traffic to the top of Overview. The verification lab now replays structured observations from actual backend executions using store metrics, status counts and current orders, with a five-line event log and collapsed full report. Retry recording verifies two attempts with retained/open counts of one after both. Added replay/pause/previous/next controls; Run all waits for each recorded sequence.

Validation for this update: all 16 allowlisted backend cases passed with the observer enabled; two verification API tests passed; six component tests and two proxy tests passed; production build passed. The new component test verifies the first event changes the UI count from zero to one and the retry increases attempts to two while order counts stay one. The local frontend API returned the same applied/duplicate snapshots.

## Initial deployment package checks

Executed on 2026-10-08 using Python 3.11.9 and Node 24.16.0 on Windows, plus Python 3.11 in the Linux Docker image. Existing uncommitted project changes were preserved.

| Check | Actual result |
| --- | --- |
| `python -m pytest -q` | 46 passed in 35.77 seconds |
| `npm.cmd test` | 2 proxy tests and 4 component interaction tests passed |
| `npm.cmd run build` | Passed; JavaScript 262.41 kB / 79.74 kB gzip |
| `npm.cmd audit` | 0 vulnerabilities |
| `docker compose config --quiet` | Passed |
| `docker build -t mock-fynd-console:verification .` | Passed using pinned Python dependencies |
| `docker run --rm --name mock-fynd-console-verification mock-fynd-console:verification python -m pytest -q` | 46 passed in 16.05 seconds |
| All 16 verification cases called through `/api/verification/run/...` | All passed; real backend subprocess execution |
| Local seeded dashboard through frontend API proxy | Three stores, 131 retained orders |
| `git diff --check` | Passed |

Both Python runs reported the existing Starlette/httpx deprecation warning; there were no test failures. Early dependency auditing found advisories in the initial test-tool version; upgrading Vitest resolved them, and the final audit is clean.

The component checks verify store details and search, individual execution, sequential Run all, evidence availability, and honest display of backend failure. Proxy checks verify the route/method allowlist and explicit unavailable behavior when deployment configuration is missing. Backend additions verify authentication, runner isolation, concurrent-job rejection, traffic limits, automatic stop and snapshot retention.

The complete lab HTTP run covered identical retry, conflicting identity, late update, equal version, authoritative correction, immutable store, returned orders, concurrent identical events, concurrent versions, rollback, lost acknowledgement, recreated service, real process kills, three HTTP instances, validation and database unavailability. The HTTP case completed in 18.718 seconds; this is whole-test runtime, distinct from the asserted sub-five-second update visibility objective.

Detailed local HTTP output is retained in ignored `.round2-ui/verification-results.json`; the website exports the same result structure through **Export evidence**.

## Open verification and deployment items

- No browser surfaces were available to the browser tool. Visual inspection at desktop/mobile widths remains unverified.
- Vercel CLI reported signed out. No public deployment was created.
- No durable public backend was configured or provisioned. The supplied Render Blueprint requires a paid persistent service/disk and user selection of hosting.
- Hosted latency, production URL, TLS routing and Vercel-to-backend connectivity must be checked after deployment.

The current result is an implemented, locally tested deployment package and running local preview. It is not yet a production-verified live website.

## Security follow-up (2026-10-08)

58 backend tests pass on Windows and in the non-root Linux container. All 11 frontend/proxy tests and the production build pass. Updated Python and npm dependency audits report no known vulnerabilities. Local sign-in, protected dashboard access and logout pass through the frontend proxy. See SECURITY-AUDIT.md for scope, fixes and remaining risks.
