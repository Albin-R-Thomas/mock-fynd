"""Allowlisted, isolated verification jobs. No user-supplied code or shell commands."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import signal
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/verification")
ROOT = Path(__file__).resolve().parents[1]
LOCK = threading.Lock()


def worker_environment():
    # Only runtime essentials, never the hosting account's credentials or API keys.
    names = {'PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP',
             'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'LANG', 'LC_ALL'}
    return {key: value for key, value in os.environ.items() if key.upper() in names}

CASES = [
    ("retry", "Identical retry", "Identity", "An identical event is applied once; retry is duplicate. Store total remains one.", "tests/test_showcase.py::test_contract[retry]"),
    ("identity", "Conflicting identity", "Identity", "A changed payload with the same event ID is rejected; the original is preserved.", "tests/test_showcase.py::test_contract[identity]"),
    ("late", "Late update", "Version authority", "Version 3 delivered wins over a late version 2 packed update.", "tests/test_showcase.py::test_contract[late]"),
    ("equal", "Equal version", "Version authority", "A new event ID with the same version is stale, even with a different status.", "tests/test_showcase.py::test_contract[equal]"),
    ("correction", "Authoritative correction", "Version authority", "Version 4 can correct delivered back to packed without changing the total.", "tests/test_showcase.py::test_contract[correction]"),
    ("store", "Immutable store boundary", "Store integrity", "An order cannot move to another store; the store check precedes version checks.", "tests/test_showcase.py::test_contract[store]"),
    ("returned", "Separate store & returned", "Store integrity", "A second store retains one returned order; all five status counts sum to total.", "tests/test_showcase.py::test_contract[returned]"),
    ("concurrent-retry", "Concurrent identical events", "Concurrency", "Independent connections race: one applied, one duplicate, one retained order.", "tests/test_round2.py::test_independent_connection_identical_race"),
    ("concurrent-versions", "Concurrent version race", "Concurrency", "Version 2 and version 3 race in both launch orders; final state is version 3 delivered.", "tests/test_round2.py::test_independent_connection_version_race"),
    ("rollback", "Failure before commit", "Recovery", "Injected exception rolls back the entire batch, including event decisions; retry succeeds.", "tests/test_round2.py::test_batch_order_and_whole_batch_exception_rollback"),
    ("lost-ack", "Lost acknowledgement", "Recovery", "Commit succeeds before an injected exception; retry is duplicate and counts stay unchanged.", "tests/test_round2.py::test_after_commit_lost_ack_no_rollback_or_retry"),
    ("restart", "Recreate service & connections", "Recovery", "A new service and new connection read the acknowledged state from the same database.", "tests/test_round2.py::test_connection_recreation_and_service_restart"),
    ("process-kill", "Real process kill", "Recovery", "Terminate a dedicated worker before and after commit, then verify rollback and durable recovery.", "tests/test_round2.py::test_actual_process_kill"),
    ("http-instances", "Three HTTP instances & freshness", "Cross-instance", "Two writer processes and an independent reader verify dashboard and SSE visibility within five seconds, plus restart.", "tests/test_http_instances.py::test_three_http_process_dashboard_sse_and_restart"),
    ("validation", "Strict input validation", "API contract", "Reject invalid statuses, IDs, versions and timestamps, including missing required fields.", "tests/test_round2.py::test_strict_validation", "tests/test_round2.py::test_required_fields"),
    ("unavailable", "Explicit unavailable responses", "API contract", "Database failures return HTTP 503 instead of successful cached data.", "tests/test_round2.py::test_database_failure_explicit_http"),
]


@router.get("/cases")
def cases():
    return {"enabled": os.getenv("VERIFICATION_ENABLED", "false").lower() == "true",
            "isolation": "Temporary SQLite databases; real backend code and dedicated worker processes",
            "cases": [dict(id=c[0], title=c[1], group=c[2], description=c[3]) for c in CASES]}


def execute(case):
    if not LOCK.acquire(blocking=False):
        raise HTTPException(429, "Another verification is running. Try again shortly.")
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="fynd-verification-") as directory:
            trace_path = Path(directory) / 'trace.jsonl'
            env = {**worker_environment(), "MOCK_AUTOSTART": "false", "VERIFICATION_ENABLED": "false",
                   "VERIFICATION_TRACE_PATH": str(trace_path),
                   "PYTEST_ADDOPTS": "", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONIOENCODING": "utf-8",
                   "AUTH_COOKIE_SECURE": "false",
                   "REDIS_URL": "redis://127.0.0.1:6399/0",
                   "DEMO_MUTATIONS_PER_MINUTE": "0", "MOCK_MAX_EVENTS_PER_SECOND": "2000",
                   "MOCK_MAX_RUNTIME_SECONDS": "0", "SNAPSHOT_RETENTION": "0",
                   "DATABASE_URL": "sqlite:///" + (Path(directory) / "isolated.db").as_posix()}
            process = subprocess.Popen([sys.executable, "-m", "pytest", "-v", "-s", "--tb=short",
                                     "-p", "no:cacheprovider", "-p", "app.verification_trace", "--basetemp", str(Path(directory) / "tests"), *case[4:]],
                                    cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                    start_new_session=os.name != "nt")
            try:
                stdout, stderr = process.communicate(timeout=100)
            except subprocess.TimeoutExpired:
                # Terminate the entire dedicated job tree, including crash-test workers.
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=10)
                raise
            output = (stdout + stderr).replace(str(ROOT), "<project>").replace(directory, "<isolated-test>")
            trace = [json.loads(line) for line in trace_path.read_text(encoding='utf-8').splitlines()] if trace_path.exists() else []
            return {"case_id": case[0], "status": "passed" if process.returncode == 0 else "failed",
                    "steps": trace,
                    "exit_code": process.returncode, "duration_ms": round((time.monotonic() - started) * 1000),
                    "completed_at": datetime.now(timezone.utc).isoformat(), "output": output[-30000:],
                    "command": "python -m pytest -v -s " + " ".join(case[4:])}
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Verification exceeded 100 seconds; no passing result was recorded.")
    finally:
        LOCK.release()


@router.post("/run/{case_id}")
async def run(case_id: str):
    if os.getenv("VERIFICATION_ENABLED", "false").lower() != "true":
        raise HTTPException(403, "Verification is disabled on this backend.")
    case = next((c for c in CASES if c[0] == case_id), None)
    if not case:
        raise HTTPException(404, "Unknown verification case")
    return await asyncio.to_thread(execute, case)
