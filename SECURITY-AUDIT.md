# Security review — 2026-10-08

Scope: local application source, authentication, API/proxy boundaries, verification subprocesses, dependency lockfiles, container configuration and local secret handling. This is a targeted review, not a penetration-test certification or a guarantee against compromise.

## Fixed findings

- Removed the built-in administrator password hash and actual password copies in fixtures. Credentials are required from backend configuration; absent or malformed configuration denies sign-in. Tests generate separate credentials.
- Added the requested administrator credentials to the ignored local `.env`. No actual password was found in nonignored source or frontend build assets. `.env` is not tracked and has no history in the available Git checkout. Environment files and private key extensions are excluded from Git and Docker context.
- Restricted this workstation's `.env` ACL to the current user, SYSTEM and administrators; other local users previously had read/modify access. This local permission change does not transfer through Git.
- Verification workers receive an allowlisted runtime environment instead of inheriting administrator credentials and unrelated cloud secrets.
- The proxy forwards only the application session cookie. Backend destinations require HTTPS except for loopback HTTP development, and URL credentials are rejected.
- Added bounded request bodies (8 KiB for authentication, 4 MiB otherwise), including chunked requests, and a ten-second body-read timeout. Added no-store and defensive response headers.
- Patched pytest from 8.4.2 to 9.0.3 for CVE-2025-71176. See the [official pytest changelog](https://docs.pytest.org/en/9.0.x/changelog.html).
- The container runs as UID/GID 10001; Compose drops Linux capabilities and prevents gaining new privileges.

## Validation

- 58 backend tests pass on Windows and in the hardened Linux container, including missing credentials, hashed credentials, credential rotation, session expiry/revocation, CSRF, login throttling, unauthorized requests, body limits and subprocess environment isolation.
- 11 frontend/proxy tests pass; the production frontend build succeeds.
- `pip-audit` against the updated requirements lockfile and `npm audit` report no known vulnerabilities at review time. This does not cover all operating-system packages or unknown vulnerabilities.
- Local HTTP checks through the frontend proxy pass: anonymous dashboard access denied, configured sign-in succeeds, HttpOnly session cookie issued, authenticated dashboard succeeds, sign-out revokes access.
- Container checks confirm non-root UID, no `/app/.env`, and writable `/data`. Compose configuration validation and Git whitespace checks pass.
- One existing Starlette/httpx test-client deprecation warning remains; it does not fail tests.

## Remaining risks and deployment requirements

- **The supplied password is weak.** It is retained as requested. Replace it with a long, unique password before exposing the site publicly. Login throttling reduces guessing but cannot make a predictable password safe. There is no MFA or password recovery.
- `.env` is plaintext configuration. Keep it private and use the backend host's secret settings in production. Never put credentials in `VITE_` variables or the frontend project. Avoid printing full Compose configuration because it resolves secrets.
- Set `AUTH_COOKIE_SECURE=true` and serve production traffic over HTTPS. Configure required administrator secrets on the backend; local `.env` is intentionally excluded from deployment artifacts.
- Existing persistent volumes must be writable by UID/GID 10001 before upgrading to the non-root image. Back up data and arrange ownership with the hosting provider. Run one API worker with this SQLite deployment.
- The public Vercel/backend deployment, TLS configuration, hosting account permissions, operating-system patch state, load resistance, backup recovery and browser visual behavior were not audited here. No public deployment was performed.
- Request limits and throttling are application safeguards, not comprehensive denial-of-service protection. Disk growth and infrastructure-level traffic limits still need monitoring.

Reference guidance: [OWASP secrets management](https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html) and [OWASP authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html).
