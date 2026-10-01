from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import Event, Operations, app, now


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6399/0")
    monkeypatch.setenv("MOCK_AUTOSTART", "false")
    with TestClient(app) as client:
        yield client


def event(status="open", **kwargs):
    return {"event_id": "e1", "order_id": "o1", "store_id": "s1", "status": status,
            "occurred_at": (now() - timedelta(minutes=1)).isoformat(), **kwargs}


def test_idempotency_ordering_and_counts(client):
    initial = event()
    assert client.post('/events', json=initial).json()['applied'] == 1
    assert client.post('/events', json=initial).json()['results'][0]['outcome'] == 'duplicate'
    assert client.post('/events', json={**initial, 'status': 'packed'}).json()['results'][0]['outcome'] == 'event_id_conflict'
    packed = event('packed', event_id='e2', occurred_at=now().isoformat())
    assert client.post('/events', json=packed).json()['applied'] == 1
    assert client.post('/events', json=event(event_id='e3')).json()['results'][0]['outcome'] == 'stale'
    assert client.post('/events', json=event(event_id='e4', occurred_at=now().isoformat())).json()['results'][0]['outcome'] == 'status_regression'
    assert client.post('/events', json=event(event_id='e5', store_id='other')).json()['results'][0]['outcome'] == 'store_conflict'
    counts = app.state.ops.counts['s1']
    assert sum(counts.values()) == 1 and counts['packed'] == 1 and counts['open'] == 0


def test_batch_and_validation(client):
    earlier = now() - timedelta(minutes=2)
    batch = [event(occurred_at=earlier.isoformat()), event('delivered', event_id='e2')]
    assert client.post('/events/batch', json=batch).json()['applied'] == 2
    assert app.state.ops.counts['s1']['delivered'] == 1
    assert client.post('/events', json=event(status='unknown')).status_code == 422
    assert client.post('/events', json=event(occurred_at='2026-01-01T00:00:00')).status_code == 422
    assert client.post('/events/batch', json=[]).status_code == 422
    assert client.post('/mock/start', json={'events_per_second': 0}).status_code == 422
    no_time = {'event_id': 'no-time', 'order_id': 'o2', 'store_id': 's1', 'status': 'open'}
    assert client.post('/events', json=no_time).json()['applied'] == 1
    assert client.post('/events', json=no_time).json()['results'][0]['outcome'] == 'duplicate'


def test_seed_summary_history_and_storage(client):
    assert client.post('/mock/seed').json()['applied'] == 131
    summary = client.get('/summary').json()
    assert {a['type'] for a in summary['anomalies']} == {'backlog', 'delivery_delay', 'status_imbalance'}
    assert client.get('/snapshots').json()[0]['summary'] == summary
    assert len(client.get('/events?limit=1000').json()) == 131
    assert client.get('/health').json()['redis'] is False
    assert client.get('/').status_code == 200


def test_restart_restores_state(client):
    client.post('/events', json=event())
    restored = Operations()
    restored.restore()
    assert restored.counts == app.state.ops.counts
    assert len(restored.recent) == 1
    restored.engine.dispose()


def test_same_status_does_not_hide_delay(client):
    client.post('/events', json=event('out_for_delivery', occurred_at=(now()-timedelta(minutes=45)).isoformat()))
    client.post('/events', json=event('out_for_delivery', event_id='e2', occurred_at=now().isoformat()))
    snapshot = app.state.ops.snapshot()
    assert snapshot['stores'][0]['delayed_deliveries'] == 1
