import time
from fastapi.testclient import TestClient
from tests.auth_helpers import sign_in
from app.main import app, mutation_times


def test_public_demo_limits_and_snapshot_retention(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "limits.db"}')
    monkeypatch.setenv('MOCK_AUTOSTART', 'false')
    monkeypatch.setenv('API_SECRET', '')
    monkeypatch.setenv('MOCK_MAX_EVENTS_PER_SECOND', '5')
    monkeypatch.setenv('MOCK_MAX_RUNTIME_SECONDS', '1')
    monkeypatch.setenv('SNAPSHOT_RETENTION', '2')
    monkeypatch.setenv('DEMO_MUTATIONS_PER_MINUTE', '3')
    mutation_times.clear()
    try:
        with TestClient(app) as client:
            sign_in(client)
            assert client.post('/mock/start',json={'events_per_second':50}).status_code == 422
            assert client.post('/mock/start',json={'events_per_second':5}).status_code == 200
            app.state.ops.mock_started_at = time.monotonic() - 5
            deadline = time.monotonic() + 5
            while client.get('/health').json()['mock_enabled']:
                assert time.monotonic() < deadline
                time.sleep(.1)
            assert client.post('/mock/stop').status_code == 200
            assert client.post('/mock/stop').status_code == 429
            time.sleep(2.1)
            assert len(client.get('/snapshots').json()) <= 2
    finally:
        mutation_times.clear()
