# Serialize the crash worker input batch as JSON.
import json
# Construct SQLite failures and identify retryable lock error codes.
import sqlite3
# Launch and kill a real writer process for crash-recovery tests.
import subprocess
# Launch the worker with the same Python interpreter as the tests.
import sys
# Synchronize competing writers with a thread barrier.
import threading
# Measure readiness deadlines and pause between worker checks.
import time
# Run concurrent submissions through independent service instances.
from concurrent.futures import ThreadPoolExecutor
# Import filesystem path support; this import is currently unused.
from pathlib import Path

# Use pytest fixtures, parameterized cases, and expected-exception checks.
import pytest
# Send HTTP requests to FastAPI in-process while running its startup and shutdown hooks.
from fastapi.testclient import TestClient
from tests.auth_helpers import sign_in
# Check that invalid event input raises the Pydantic validation exception.
from pydantic import ValidationError
# Import SQLAlchemy query construction; this import is currently unused.
from sqlalchemy import select
# Simulate database operation failures in retry and HTTP error tests.
from sqlalchemy.exc import OperationalError

# Access service errors, event validation, status order, and the API; events_table is currently unused.
from app.main import DatabaseUnavailable, Event, Operations, STATUSES, app, events_table


# Build a valid event dictionary; callers override fields to exercise specific cases.
def payload(event_id='e1', order_id='o1', store_id='s1', version=1, status='open', occurred_at='2026-01-01T00:00:00Z'):
    # Return all required event fields using the defaults or caller-supplied values.
    return dict(event_id=event_id, order_id=order_id, store_id=store_id, version=version, status=status, occurred_at=occurred_at)


# Register this helper as a pytest fixture with automatic teardown.
@pytest.fixture
# Create independent service instances backed by the same isolated test database.
def factory(tmp_path, monkeypatch):
    # Use a temporary SQLite database so this test cannot affect application data.
    monkeypatch.setenv('DATABASE_URL', f"sqlite:///{tmp_path / 'shared.db'}")
    # Disable automatic mock traffic so only test submissions change state.
    monkeypatch.setenv('MOCK_AUTOSTART', 'false')
    # Point Redis at the test endpoint, which is expected to be unavailable.
    monkeypatch.setenv('REDIS_URL', 'redis://localhost:6399/0')
    # Track every service created by this fixture for teardown.
    services = []
    # Initialize a service and track its engine so the fixture can close it later.
    def make(**kwargs):
        # Construct a service, forwarding options such as injected transaction hooks.
        service = Operations(**kwargs)
        # Load persisted state and prepare database storage for this instance.
        service.restore()
        # Register this instance for connection cleanup after the test.
        services.append(service)
        # Give the caller the initialized service.
        return service
    # Expose the factory to the test; resume here afterward for cleanup.
    yield make
    # Close the engines of all instances created during this test.
    for service in services:
        # Release pooled database connections after the test finishes.
        service.engine.dispose()


# Validate and persist one event, then return its individual processing result.
def ingest(service, **kwargs):
    return service.persist_events([Event(**payload(**kwargs))])['results'][0]


# Check each store total and status count, including the total/status invariant.
def assert_counts(service, expected):
    # Read current store aggregates from the database-backed snapshot.
    stores = service.snapshot()['stores']
    # Compare each store with the expected total and ordered status counts.
    assert {s['store_id']: (s['total'], *(s[k] for k in STATUSES)) for s in stores} == expected
    # Ensure each store total equals the sum of its status buckets.
    assert all(s['total'] == sum(s[k] for k in STATUSES) for s in stores)


# Check deduplication, version ordering, fixed store ownership, and retained decisions.
def test_required_fixture_and_retained_identity(factory):
    # Use separate instances to verify that committed writes are visible to another reader.
    writer, reader = factory(), factory()
    # Verify that this valid newer event updates the current order.
    assert ingest(writer)['outcome'] == 'applied'
    # Verify that an identical replay does not apply the event again.
    assert ingest(writer)['outcome'] == 'duplicate'
    # Verify store totals and status buckets; tuples follow total, open, packed, out_for_delivery, delivered, returned.
    assert_counts(reader, {'s1': (1, 1, 0, 0, 0, 0)})
    # Reject reuse of an event ID with a different payload.
    assert ingest(writer, status='packed')['outcome'] == 'event_id_conflict'
    # Verify that this valid newer event updates the current order.
    assert ingest(writer, event_id='e3', version=3, status='delivered')['outcome'] == 'applied'
    # Reject an equal or older version without changing the current order.
    assert ingest(writer, event_id='e2', version=2, status='packed')['outcome'] == 'stale'
    # Reject an equal or older version without changing the current order.
    assert ingest(writer, event_id='equal', version=3)['outcome'] == 'stale'
    # Verify store totals and status buckets; tuples follow total, open, packed, out_for_delivery, delivered, returned.
    assert_counts(reader, {'s1': (1, 0, 0, 0, 1, 0)})
    # Verify that this valid newer event updates the current order.
    assert ingest(writer, event_id='e4', version=4, status='packed')['outcome'] == 'applied'
    # Reject an attempt to move an existing order to a different store.
    assert ingest(writer, event_id='e5', store_id='s2', version=5)['outcome'] == 'store_conflict'
    # Reject an attempt to move an existing order to a different store.
    assert ingest(writer, event_id='lower-other', store_id='s2', version=1)['outcome'] == 'store_conflict'
    # Verify that this valid newer event updates the current order.
    assert ingest(writer, event_id='e6', order_id='o2', store_id='s2', status='returned')['outcome'] == 'applied'
    # Verify store totals and status buckets; tuples follow total, open, packed, out_for_delivery, delivered, returned.
    assert_counts(reader, {'s1': (1, 0, 1, 0, 0, 0), 's2': (1, 0, 0, 0, 0, 1)})
    # Capture retained events before retrying rejected or duplicate submissions.
    before = reader.read_events(1000)
    # Verify that replaying an event preserves the decision recorded on its first submission.
    assert ingest(writer, event_id='e2', version=2, status='packed') == {'event_id': 'e2', 'outcome': 'duplicate', 'stored_decision': 'stale'}
    # Verify that replaying an event preserves the decision recorded on its first submission.
    assert ingest(writer, event_id='e5', store_id='s2', version=5)['stored_decision'] == 'store_conflict'
    # Reject reuse of an event ID with a different payload.
    assert ingest(writer, event_id='e5', version=100)['outcome'] == 'event_id_conflict'
    # Ensure retries and conflicts did not alter the retained event history.
    assert reader.read_events(1000) == before
    # Check the original event payload and its stored decision are retained exactly.
    assert next(e for e in before if e['event_id'] == 'e1') == {**Event(**payload()).model_dump(mode='json'), 'outcome': 'applied'}
    # Verify the durable order retains the expected latest version.
    assert reader.read_orders()[0]['version'] == 4


# Run the test once for each listed invalid field override.
@pytest.mark.parametrize('change', [dict(version=True), dict(version=1.0), dict(version='1'), dict(version=0), dict(version=-1), dict(status='unknown'), dict(event_id=' \t'), dict(order_id='\n'), dict(store_id=' '), dict(extra=1), dict(occurred_at='2026-01-01T00:00:00'), dict(occurred_at=123)])
# Reject each invalid field value instead of silently coercing it.
def test_strict_validation(change):
    # Require the enclosed operation to raise the specified error; fail if it succeeds.
    with pytest.raises(ValidationError):
        # Override one part of a valid event and require the model to reject it.
        Event(**{**payload(), **change})


# Run the test once for each listed field, writer assignment, or commit phase.
@pytest.mark.parametrize('field', ['event_id', 'version', 'occurred_at'])
# Reject an event when any of the selected required fields is absent.
def test_required_fields(field):
    # Start with a valid event so only the missing field causes rejection.
    value = payload()
    # Remove the required field selected by this parameterized test.
    del value[field]
    # Require the enclosed operation to raise the specified error; fail if it succeeds.
    with pytest.raises(ValidationError):
        # Construct the model and require validation to fail for the missing field.
        Event(**value)


# Normalize equivalent timestamps while preserving the exact event ID text.
def test_normalized_timestamp_identity_and_exact_ids(factory):
    # Create a service using the fixture database.
    service = factory()
    # Verify that this valid newer event updates the current order.
    assert ingest(service, event_id=' e1 ')['outcome'] == 'applied'
    # Verify that an identical replay does not apply the event again.
    assert ingest(service, event_id=' e1 ', occurred_at='2026-01-01T05:30:00+05:30')['outcome'] == 'duplicate'
    # Reject an equal or older version without changing the current order.
    assert ingest(service, event_id='e1')['outcome'] == 'stale'
    # Check the persisted current-order projection after the preceding operations.
    assert service.read_orders()[0]['event_id'] == ' e1 '


# Submit events concurrently through separate services sharing one database.
def race(services, events):
    # Hold workers until all have reached the same starting point.
    barrier = threading.Barrier(len(services))
    # Wait for all workers before submitting this service/event pair.
    def worker(pair):
        # Release submissions together; fail rather than hang if a worker never arrives.
        barrier.wait(timeout=10)
        # Validate this event and submit it through its assigned connection pool.
        return pair[0].persist_events([Event(**pair[1])])
    # Run one thread per service and join the threads on exit.
    with ThreadPoolExecutor(max_workers=len(services)) as pool:
        # Pair each service with its event and collect all concurrent results.
        return list(pool.map(worker, zip(services, events)))


# Ensure concurrent identical submissions apply once and retain one event.
def test_independent_connection_identical_race(factory):
    # Create two independent writers and a third instance for verifying persisted state.
    a, b, reader = factory(), factory(), factory()
    # Submit the exact same event from both writers simultaneously.
    results = race([a, b], [payload(), payload()])
    # Require exactly one successful writer and one duplicate result in the race.
    assert sorted(r['results'][0]['outcome'] for r in results) == ['applied', 'duplicate']
    # Check the retained event history has the expected contents or length.
    assert len(reader.read_events()) == 1
    # Verify store totals and status buckets; tuples follow total, open, packed, out_for_delivery, delivered, returned.
    assert_counts(reader, {'s1': (1, 1, 0, 0, 0, 0)})


# Run the test once for each listed field, writer assignment, or commit phase.
@pytest.mark.parametrize('reverse', [False, True])
# Ensure the highest version wins regardless of which connection submits it.
def test_independent_connection_version_race(factory, reverse):
    # Create two independent writers and a third instance for verifying persisted state.
    a, b, reader = factory(), factory(), factory()
    # Persist the initial version of the order.
    ingest(a)
    # Prepare competing updates for the same order at versions 2 and 3.
    inputs = [payload(event_id='e2', version=2, status='packed'), payload(event_id='e3', version=3, status='delivered')]
    # Race the updates, swapping their assigned writers in the second test case.
    race([a, b], inputs[::-1] if reverse else inputs)
    # Verify the durable order retains the expected latest version.
    assert reader.read_orders()[0]['version'] == 3
    # Verify store totals and status buckets; tuples follow total, open, packed, out_for_delivery, delivered, returned.
    assert_counts(reader, {'s1': (1, 0, 0, 0, 1, 0)})
    # Check the retained event history has the expected contents or length.
    assert len(reader.read_events()) == 3


# Roll back the entire batch on a pre-commit error, then verify a clean retry.
def test_batch_order_and_whole_batch_exception_rollback(factory):
    # Record hook calls to verify whether the transaction is retried.
    calls = []
    # Inject an unexpected error immediately before the transaction commits.
    def fail():
        # Record this hook invocation for the retry assertions.
        calls.append(1)
        # Abort before commit to exercise transaction rollback.
        raise RuntimeError('injected before commit')
    # Attach the failing pre-commit hook to one writer and create an independent reader.
    a, reader = factory(test_hooks={'before_commit': fail}), factory()
    # Build two ordered updates that must commit or roll back together.
    batch = [Event(**payload()), Event(**payload(event_id='e2', version=2, status='packed'))]
    # Require the enclosed operation to raise the specified error; fail if it succeeds.
    with pytest.raises(RuntimeError):
        # Attempt the batch while the injected hook forces an error.
        a.persist_events(batch)
    # Verify the hook invocation count matches the expected retry behavior.
    assert calls == [1]  # arbitrary exceptions are not retried
    # Verify rollback removed both event history and current-order changes.
    assert reader.read_events() == reader.read_orders() == []
    # Remove the injected failure so the next submission can succeed.
    a.test_hooks.clear()
    # Check how many submitted events actually changed order state.
    assert a.persist_events(batch)['applied'] == 2
    # Verify the durable order retains the expected latest version.
    assert reader.read_orders()[0]['version'] == 2


# Keep committed data when acknowledgement fails and deduplicate a caller retry.
def test_after_commit_lost_ack_no_rollback_or_retry(factory):
    # Record hook calls to verify whether the transaction is retried.
    calls = []
    # Simulate a lost acknowledgement after the database has committed.
    def lose():
        # Record this hook invocation for the retry assertions.
        calls.append(1)
        # Fail after commit to simulate the caller missing a successful acknowledgement.
        raise RuntimeError('ack lost')
    # Attach the failing post-commit hook and use another instance to inspect durable data.
    a, reader = factory(test_hooks={'after_commit': lose}), factory()
    # Require the enclosed operation to raise the specified error; fail if it succeeds.
    with pytest.raises(RuntimeError):
        # Persist the initial version of the order.
        ingest(a)
    # Verify the hook invocation count matches the expected retry behavior.
    assert calls == [1]
    # Check the retained event history has the expected contents or length.
    assert len(reader.read_events()) == 1
    # Remove the injected failure so the next submission can succeed.
    a.test_hooks.clear()
    # Verify that an identical replay does not apply the event again.
    assert ingest(a)['outcome'] == 'duplicate'
    # Verify store totals and status buckets; tuples follow total, open, packed, out_for_delivery, delivered, returned.
    assert_counts(reader, {'s1': (1, 1, 0, 0, 0, 0)})


# Verify that disposing connections and recreating services preserves durable state.
def test_connection_recreation_and_service_restart(factory):
    # Create a service for the test database.
    a = factory()
    # Persist the initial version of the order.
    ingest(a)
    # Discard existing pooled connections so the next operation must reconnect.
    a.engine.dispose()
    # Verify that this valid newer event updates the current order.
    assert ingest(a, event_id='e2', version=2, status='returned')['outcome'] == 'applied'
    # Create fresh instances against the same database to verify restart recovery.
    restarted, reader = factory(), factory()
    # Verify independently initialized instances read identical persisted orders.
    assert restarted.read_orders() == reader.read_orders()
    # Verify store totals and status buckets; tuples follow total, open, packed, out_for_delivery, delivered, returned.
    assert_counts(reader, {'s1': (1, 0, 0, 0, 0, 1)})


# Run the test once for each listed field, writer assignment, or commit phase.
@pytest.mark.parametrize('phase', ['before_commit', 'after_commit'])
# Kill a real writer before or after commit and verify recovery and safe resubmission.
def test_actual_process_kill(factory, tmp_path, phase):
    # Initialize the shared database and retain an independent reader.
    reader = factory()
    # Choose temporary paths for the worker readiness signal and input batch.
    ready, inputs = tmp_path / 'ready', tmp_path / 'events.json'
    # Write the two-event batch that the child process will attempt to commit.
    inputs.write_text(json.dumps([payload(), payload(event_id='e2', version=2, status='packed')]))
    # Run the crash worker with this database, batch, and selected commit-phase hook.
    command = [sys.executable, 'scripts/crash_worker.py', '--database', str(reader.database_path), '--phase', phase, '--ready', str(ready), '--events', str(inputs)]
    # Launch a real child process and capture diagnostics if it exits unexpectedly.
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # Ensure child-process cleanup runs even if waiting or an assertion fails.
    try:
        # Allow up to 15 seconds for the worker to reach the selected hook.
        deadline = time.monotonic() + 15
        # Wait for the file confirming the worker reached the commit boundary.
        while not ready.exists():
            # Detect a worker that exited before signaling readiness.
            if process.poll() is not None:
                # Fail with the worker output so startup errors are visible.
                pytest.fail(str(process.communicate()))
            # Fail if the worker does not signal readiness before the deadline.
            assert time.monotonic() < deadline, 'worker did not reach hook'
            # Pause briefly between readiness checks to avoid busy polling.
            time.sleep(0.02)
        # Terminate the worker abruptly to simulate a process crash.
        process.kill()
        # Reap the terminated process and drain its captured output.
        process.communicate(timeout=10)
        # Confirm the worker exited abnormally after being killed.
        assert process.returncode != 0
    # Always clean up a child process that survived the test body.
    finally:
        # Only kill again if the worker is still running.
        if process.poll() is None:
            # Terminate the worker abruptly to simulate a process crash.
            process.kill()
            # Reap the terminated process and drain its captured output.
            process.communicate(timeout=10)
    # Create a new service to read durable state after the crash.
    restarted = factory()
    # Expect no retained events before commit and both events after commit.
    expected = 0 if phase == 'before_commit' else 2
    # Check the retained event history has the expected contents or length.
    assert len(restarted.read_events()) == expected
    # Check the persisted current-order projection after the preceding operations.
    assert len(restarted.read_orders()) == (0 if phase == 'before_commit' else 1)
    # Resubmit the original batch to test recovery and duplicate detection.
    retry = restarted.persist_events([Event(**payload()), Event(**payload(event_id='e2', version=2, status='packed'))])
    # Check how many submitted events actually changed order state.
    assert retry['applied'] == (2 if phase == 'before_commit' else 0)
    # Verify store totals and status buckets; tuples follow total, open, packed, out_for_delivery, delivered, returned.
    assert_counts(reader, {'s1': (1, 0, 1, 0, 0, 0)})


# Report exhausted lock retries explicitly without retaining a partial write.
def test_lock_exhaustion_is_explicit_and_rolls_back(factory):
    # Use separate service instances for writing and observing or locking the database.
    a, reader = factory(), factory()
    # Keep an independent database connection open for the lock scenario.
    with reader.engine.connect() as lock:
        # Acquire the SQLite write lock before the other service attempts its write.
        lock.exec_driver_sql('BEGIN IMMEDIATE')
        # Require the enclosed operation to raise the specified error; fail if it succeeds.
        with pytest.raises(DatabaseUnavailable, match='contention'):
            # Persist the initial version of the order.
            ingest(a)
        # Check the retained event history has the expected contents or length.
        assert reader.read_events() == []
        # Release the held write lock so the next write can proceed.
        lock.rollback()
    # Verify that this valid newer event updates the current order.
    assert ingest(a)['outcome'] == 'applied'


# Retry a recognized SQLite busy error and commit exactly one event.
def test_recognized_lock_retries_complete_transaction(factory):
    # Record hook calls to verify whether the transaction is retried.
    calls = []
    # Inject a retryable SQLite lock error on the first commit attempt only.
    def fail_once():
        # Record this hook invocation for the retry assertions.
        calls.append(1)
        # Fail only the first attempt so a transaction retry can succeed.
        if len(calls) == 1:
            # Build the low-level SQLite error recognized as lock contention.
            error = sqlite3.OperationalError('database is locked')
            # Attach the busy error code used to classify the failure as retryable.
            error.sqlite_errorcode = sqlite3.SQLITE_BUSY
            # Wrap the SQLite error as SQLAlchemy would when a database operation fails.
            raise OperationalError('injected', {}, error)
    # Install the one-time lock failure at the pre-commit boundary.
    a = factory(test_hooks={'before_commit': fail_once})
    # Verify that this valid newer event updates the current order.
    assert ingest(a)['outcome'] == 'applied'
    # Verify the hook invocation count matches the expected retry behavior.
    assert len(calls) == 2
    # Check the retained event history has the expected contents or length.
    assert len(a.read_events()) == 1


# Preserve status age on same-status updates and exclude returned orders from delays.
def test_status_since_metadata_and_inactive_returned(factory):
    # Create a service for the test database.
    a = factory()
    # Start the order in delivery status so its status age can be tested.
    ingest(a, status='out_for_delivery')
    # Submit a newer version to exercise status changes and timestamp tracking.
    ingest(a, event_id='e2', version=2, status='out_for_delivery', occurred_at='2026-01-02T00:00:00Z')
    # Verify that status age is measured from the event that began the current status.
    assert a.read_orders()[0]['status_since'] == '2026-01-01T00:00:00Z'
    # Submit a newer version to exercise status changes and timestamp tracking.
    ingest(a, event_id='e3', version=3, status='returned')
    # Check the delayed-delivery count after the preceding status updates.
    assert a.snapshot()['stores'][0]['delayed_deliveries'] == 0
    # Submit a newer version to exercise status changes and timestamp tracking.
    ingest(a, event_id='e4', version=4, status='packed', occurred_at='2025-01-01T00:00:00Z')
    # Verify that status age is measured from the event that began the current status.
    assert a.read_orders()[0]['status_since'] == '2025-01-01T00:00:00Z'


# Expose database failures as HTTP 503 responses across the affected routes.
def test_database_failure_explicit_http(factory, monkeypatch):
    # Start the application lifespan and close the HTTP client afterward.
    with TestClient(app) as client:
        sign_in(client)
        # Simulate a database failure when the API tries to read its snapshot.
        def unavailable():
            # Raise a database error unrelated to ordinary lock contention.
            raise OperationalError('test', {}, sqlite3.OperationalError('disk unavailable'))
        # Temporarily replace snapshot reads with the simulated database failure.
        monkeypatch.setattr(app.state.ops, 'snapshot', unavailable)
        # Return HTTP 503 so the caller can distinguish database unavailability.
        assert client.get('/dashboard').status_code == 503
        # Return HTTP 503 so the caller can distinguish database unavailability.
        assert client.get('/summary').status_code == 503
        # Return HTTP 503 so the caller can distinguish database unavailability.
        assert client.get('/stream').status_code == 503
        # Replace ingestion with a callable that raises DatabaseUnavailable when invoked.
        monkeypatch.setattr(app.state.ops, 'persist_events', lambda batch: (_ for _ in ()).throw(DatabaseUnavailable('failed')))
        # Return HTTP 503 so the caller can distinguish database unavailability.
        assert client.post('/events', json=payload()).status_code == 503

# Preserve and compare positive versions larger than SQLite signed integers.
def test_positive_versions_beyond_sqlite_integer_range(factory):
    # Create a service using the fixture database.
    service = factory()
    # Verify that this valid newer event updates the current order.
    assert ingest(service, version=2**80)['outcome'] == 'applied'
    # Verify that this valid newer event updates the current order.
    assert ingest(service, event_id='next', version=2**80 + 1, status='returned')['outcome'] == 'applied'
    # Verify the durable order retains the expected latest version.
    assert service.read_orders()[0]['version'] == 2**80 + 1
