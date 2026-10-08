import pytest
import secrets


@pytest.fixture(autouse=True)
def isolated_admin_configuration(monkeypatch):
    # Test fixtures sign in normally; they never disable backend authorization.
    monkeypatch.setenv('ADMIN_USERNAME', 'fynd')
    monkeypatch.setenv('ADMIN_PASSWORD', secrets.token_urlsafe(24))
    monkeypatch.delenv('ADMIN_PASSWORD_HASH', raising=False)
    monkeypatch.setenv('AUTH_COOKIE_SECURE', 'false')
