import time
import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update, select
from app.main import app
from app.auth import COOKIE, sessions
from tests.auth_helpers import sign_in


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('DATABASE_URL', f'sqlite:///{tmp_path / "auth.db"}')
    monkeypatch.setenv('MOCK_AUTOSTART','false')
    monkeypatch.setenv('REDIS_URL','redis://127.0.0.1:6399/0')
    with TestClient(app) as client:
        yield client


def test_all_data_and_control_routes_require_session(client, monkeypatch):
    monkeypatch.setenv('API_SECRET','old-api-key')
    for path in ['/', '/dashboard','/events','/summary','/snapshots','/stream','/health','/docs','/openapi.json','/verification/cases','/auth/session']:
        assert client.get(path, headers={'x-api-key':'old-api-key'}).status_code == 401, path
    for path in ['/events','/events/batch','/mock/start','/mock/stop','/mock/seed','/verification/run/retry','/auth/logout']:
        assert client.post(path,headers={'x-api-key':'old-api-key'}).status_code == 401, path
    assert client.get('/health/live').json() == {'status':'ok'}
    assert client.post('/auth/signup').status_code == 401


def test_login_session_csrf_logout_and_revocation(client):
    assert client.post('/auth/login',json={'username':'fynd','password':'wrong'},headers={'x-requested-with':'fynd-console'}).status_code == 401
    assert client.post('/auth/login',json={'username':'fynd','password':os.environ['ADMIN_PASSWORD']}).status_code == 403
    response = sign_in(client)
    token=client.cookies.get(COOKIE)
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    assert token not in response.text
    assert client.get('/auth/session').json()['role'] == 'admin'
    assert client.get('/dashboard').status_code == 200
    assert client.post('/mock/stop',headers={'x-csrf-token':'wrong'}).status_code == 403
    assert client.post('/mock/stop').status_code == 200
    assert client.post('/auth/logout').status_code == 200
    assert client.get('/dashboard').status_code == 401
    client.cookies.set(COOKIE,token)
    assert client.get('/dashboard').status_code == 401


def test_expired_and_forged_sessions_are_rejected(client):
    client.cookies.set(COOKIE,'fabricated-token')
    assert client.get('/dashboard').status_code == 401
    client.cookies.clear()
    sign_in(client)
    with app.state.ops.engine.begin() as connection:
        connection.execute(update(sessions).values(expires_at=time.time()-1))
    assert client.get('/dashboard').status_code == 401


def test_password_change_invalidates_sessions_and_secure_cookie(client,monkeypatch):
    monkeypatch.setenv('AUTH_COOKIE_SECURE','true')
    response=sign_in(client)
    assert '; Secure' in response.headers['set-cookie']
    # Send cookie explicitly to exercise credential invalidation over test HTTP.
    token=client.cookies.get(COOKIE)
    monkeypatch.setenv('ADMIN_USERNAME','another-admin')
    assert client.get('/dashboard',headers={'cookie':f'{COOKIE}={token}'}).status_code == 401


def test_login_throttling(client):
    for _ in range(10):
        response=client.post('/auth/login',json={'username':'unknown','password':'wrong'},headers={'x-requested-with':'fynd-console'})
        assert response.status_code==401
    assert client.post('/auth/login',json={'username':'fynd','password':os.environ['ADMIN_PASSWORD']},headers={'x-requested-with':'fynd-console'}).status_code==429


def test_session_survives_connection_recreation_without_storing_raw_token(client):
    sign_in(client)
    token=client.cookies.get(COOKIE)
    with app.state.ops.engine.connect() as connection:
        stored=connection.execute(select(sessions)).mappings().one()
        assert stored['token_hash'] != token
    app.state.ops.engine.dispose()
    assert client.get('/auth/session').status_code == 200


def test_missing_credentials_have_no_fallback(client, monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD')
    monkeypatch.delenv('ADMIN_PASSWORD_HASH', raising=False)
    response=client.post('/auth/login',json={'username':'fynd','password':'anything'},headers={'x-requested-with':'fynd-console'})
    assert response.status_code==503
    assert not client.cookies.get(COOKIE)


def test_hash_configuration_and_invalid_hash_fail_closed(client, monkeypatch):
    from app.auth import password_hash
    configured=os.environ['ADMIN_PASSWORD']
    monkeypatch.setenv('ADMIN_PASSWORD_HASH',password_hash(configured))
    assert sign_in(client).status_code==200
    monkeypatch.setenv('ADMIN_PASSWORD_HASH','not-a-valid-hash')
    assert client.post('/auth/login',json={'username':'fynd','password':configured},headers={'x-requested-with':'fynd-console'}).status_code==503


def test_password_rotation_revokes_existing_session(client, monkeypatch):
    sign_in(client)
    monkeypatch.setenv('ADMIN_PASSWORD','new-test-only-password')
    assert client.get('/auth/session').status_code==401


def test_request_limits_and_headers_cover_unauthenticated_responses(client):
    response=client.post('/auth/login',content=b'x'*8193,headers={'content-type':'application/json'})
    assert response.status_code==413
    assert response.headers['cache-control']=='no-store'
    assert response.headers['x-frame-options']=='DENY'
    response=client.get('/dashboard')
    assert response.status_code==401
    assert response.headers['x-content-type-options']=='nosniff'
    assert client.post('/auth/login',content='{}',headers={'content-length':'-1'}).status_code==400


def test_chunked_login_request_cannot_bypass_size_limit(client):
    def chunks():
        yield b'x'*4096
        yield b'y'*4097
    assert client.post('/auth/login',content=chunks(),headers={'content-type':'application/json'}).status_code==413


def test_worker_environment_never_forwards_credentials(monkeypatch):
    from app.verification import worker_environment
    monkeypatch.setenv('ADMIN_PASSWORD','private-admin-value')
    monkeypatch.setenv('CLOUD_SECRET','private-cloud-value')
    monkeypatch.setenv('API_SECRET','private-api-value')
    child=worker_environment()
    assert 'ADMIN_PASSWORD' not in child
    assert 'CLOUD_SECRET' not in child
    assert 'API_SECRET' not in child
    assert 'PATH' in {key.upper() for key in child}
