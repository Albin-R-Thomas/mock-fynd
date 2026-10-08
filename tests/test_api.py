# Compute relative event times for recent and delayed orders.
from datetime import timedelta

# Use pytest fixtures, parameterized cases, and expected-exception checks.
import pytest
# Send HTTP requests to FastAPI in-process while running its startup and shutdown hooks.
from fastapi.testclient import TestClient
from tests.auth_helpers import sign_in

# Access the event model, service, API, and UTC clock used by the application.
from app.main import Event, Operations, app, now


# Register this helper as a pytest fixture with automatic teardown.
@pytest.fixture
# Run the API with an isolated database and clean up its lifespan after the test.
def client(tmp_path, monkeypatch):
    # Use a temporary SQLite database so this test cannot affect application data.
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    # Point Redis at the test endpoint, which is expected to be unavailable.
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6399/0")
    # Disable automatic mock traffic so only test submissions change state.
    monkeypatch.setenv("MOCK_AUTOSTART", "false")
    # Start the application lifespan and close the HTTP client afterward.
    with TestClient(app) as client:
        sign_in(client)
        # Give the running client to the test, then resume context-manager cleanup.
        yield client


# Build an API request with a recent timestamp and optional field overrides.
def event(status="open", **kwargs):
    # Build the event identity, order ownership, status, and version fields.
    return {"event_id": "e1", "order_id": "o1", "store_id": "s1", "status": status, "version": 1,
            # Use a one-minute-old timestamp, then apply any caller overrides.
            "occurred_at": (now() - timedelta(minutes=1)).isoformat(), **kwargs}


# Check event identity, version ordering, store ownership, and dashboard counts through HTTP.
def test_idempotency_ordering_and_counts(client):
    # Prepare the first valid event for this order.
    initial = event()
    # Check how many submitted events actually changed order state.
    assert client.post('/events', json=initial).json()['applied'] == 1
    # Verify that an identical replay does not apply the event again.
    assert client.post('/events', json=initial).json()['results'][0]['outcome'] == 'duplicate'
    # Reject reuse of an event ID with a different payload.
    assert client.post('/events', json={**initial, 'status': 'packed'}).json()['results'][0]['outcome'] == 'event_id_conflict'
    # Prepare a newer update that moves the existing order to packed.
    packed = event('packed', event_id='e2', version=3, occurred_at=now().isoformat())
    # Check how many submitted events actually changed order state.
    assert client.post('/events', json=packed).json()['applied'] == 1
    # Reject an equal or older version without changing the current order.
    assert client.post('/events', json=event(event_id='e3')).json()['results'][0]['outcome'] == 'stale'
    # Verify that this valid newer event updates the current order.
    assert client.post('/events', json=event(event_id='e4', version=4, occurred_at=now().isoformat())).json()['results'][0]['outcome'] == 'applied'
    # Reject an attempt to move an existing order to a different store.
    assert client.post('/events', json=event(event_id='e5', store_id='other')).json()['results'][0]['outcome'] == 'store_conflict'
    # Fetch the store aggregates after applying and rejecting the preceding updates.
    counts = client.get('/dashboard').json()['stores'][0]
    # Verify rejected events did not inflate totals and the latest accepted status is open.
    assert counts['total'] == 1 and counts['packed'] == 0 and counts['open'] == 1


# Apply a valid batch and reject malformed events and mock configuration.
def test_batch_and_validation(client):
    # Choose an older timestamp for the initial event in the batch.
    earlier = now() - timedelta(minutes=2)
    # Prepare an initial event followed by a delivered update for the same order.
    batch = [event(occurred_at=earlier.isoformat()), event('delivered', event_id='e2', version=2)]
    # Check how many submitted events actually changed order state.
    assert client.post('/events/batch', json=batch).json()['applied'] == 2
    # Verify the batch leaves one order in the delivered bucket.
    assert client.get('/dashboard').json()['stores'][0]['delivered'] == 1
    # Return HTTP 422 for this invalid request instead of accepting it.
    assert client.post('/events', json=event(status='unknown')).status_code == 422
    # Return HTTP 422 for this invalid request instead of accepting it.
    assert client.post('/events', json=event(occurred_at='2026-01-01T00:00:00')).status_code == 422
    # Return HTTP 422 for this invalid request instead of accepting it.
    assert client.post('/events/batch', json=[]).status_code == 422
    # Return HTTP 422 for this invalid request instead of accepting it.
    assert client.post('/mock/start', json={'events_per_second': 0}).status_code == 422
    # Build an invalid request missing both required version and timestamp fields.
    no_time = {'event_id': 'no-time', 'order_id': 'o2', 'store_id': 's1', 'status': 'open'}
    # Return HTTP 422 for this invalid request instead of accepting it.
    assert client.post('/events', json=no_time).status_code == 422


# Verify seeded anomalies, snapshot history, durable events, and basic route health.
def test_seed_summary_history_and_storage(client):
    # Check how many submitted events actually changed order state.
    assert client.post('/mock/seed').json()['applied'] == 131
    # Read the anomalies computed from the seeded orders.
    summary = client.get('/summary').json()
    # Check that the seeded data produces all three expected anomaly types.
    assert {a['type'] for a in summary['anomalies']} == {'backlog', 'delivery_delay', 'status_imbalance'}
    # Verify saved snapshot history includes the current anomaly summary.
    assert any(snapshot['summary']['anomalies'] == summary['anomalies'] for snapshot in client.get('/snapshots').json())
    assert len(client.get('/events?limit=1000').json()) == 131
    # Verify the API remains usable when the configured Redis endpoint is unavailable.
    assert client.get('/health').json()['redis'] is False
    # Verify the dashboard HTML route is served successfully.
    assert client.get('/').status_code == 200


# Restore a fresh service from the database and compare it with the running API.
def test_restart_restores_state(client):
    # Persist an order through the running API before restoring another service.
    client.post('/events', json=event())
    # Create a fresh service using the same database configuration.
    restored = Operations()
    # Initialize the new instance from persisted database state.
    restored.restore()
    # Verify independently initialized instances read identical persisted orders.
    assert restored.read_orders() == app.state.ops.read_orders()
    # Check the retained event history has the expected contents or length.
    assert len(restored.read_events()) == 1
    # Release the additional service connections created by this test.
    restored.engine.dispose()


# Ensure a same-status update does not reset an already-delayed delivery clock.
def test_same_status_does_not_hide_delay(client):
    # Start delivery 45 minutes ago.
    client.post('/events', json=event('out_for_delivery', occurred_at=(now()-timedelta(minutes=45)).isoformat()))
    # Submit a newer delivery event with the same status; its original age must survive.
    client.post('/events', json=event('out_for_delivery', event_id='e2', version=3, occurred_at=now().isoformat()))
    # Read store metrics after both delivery-status events have been ingested.
    snapshot = app.state.ops.snapshot()
    # Check the delayed-delivery count after the preceding status updates.
    assert snapshot['stores'][0]['delayed_deliveries'] == 1
