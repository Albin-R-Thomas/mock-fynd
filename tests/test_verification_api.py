from fastapi.testclient import TestClient
from tests.auth_helpers import sign_in
from app.main import app
from app import verification


def test_catalog_disabled_and_unknown_case(monkeypatch, tmp_path):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "catalog.db"}')
    monkeypatch.setenv('MOCK_AUTOSTART', 'false')
    monkeypatch.setenv('VERIFICATION_ENABLED', 'false')
    monkeypatch.setenv('API_SECRET', '')
    with TestClient(app, raise_server_exceptions=False) as client:
        sign_in(client)
        catalog = client.get('/verification/cases').json()
        assert len(catalog['cases']) == 16
        assert catalog['enabled'] is False
        assert client.post('/verification/run/retry').status_code == 403
        monkeypatch.setenv('VERIFICATION_ENABLED', 'true')
        assert client.post('/verification/run/arbitrary-command').status_code == 404


def test_mutation_auth_and_real_runner(monkeypatch, tmp_path):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "api.db"}')
    monkeypatch.setenv('MOCK_AUTOSTART', 'false')
    monkeypatch.setenv('VERIFICATION_ENABLED', 'true')
    monkeypatch.setenv('API_SECRET', 'test-secret')
    with TestClient(app) as client:
        assert client.post('/verification/run/retry').status_code == 401
        assert client.get('/dashboard').status_code == 401
        sign_in(client)
        response = client.post('/verification/run/retry', headers={'x-api-key':'test-secret'})
        assert response.status_code == 200
        result = response.json()
        assert result['status'] == 'passed' and result['exit_code'] == 0
        assert 'READER SNAPSHOT' in result['output']
        event_steps = [s for s in result['steps'] if s['kind'] == 'event']
        assert [s['decisions'][0]['outcome'] for s in event_steps] == ['applied', 'duplicate']
        assert [s['stores'][0]['total'] for s in event_steps] == [1, 1]
        assert [s['stores'][0]['open'] for s in event_steps] == [1, 1]
        assert client.get('/dashboard').json()['stores'] == []
        assert verification.LOCK.acquire(blocking=False)
        try:
            assert client.post('/verification/run/retry', headers={'x-api-key':'test-secret'}).status_code == 429
        finally:
            verification.LOCK.release()
