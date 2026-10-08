"""Single-admin sign-in with revocable, durable, opaque sessions."""
import asyncio
import hashlib
import os
import secrets
import time
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, delete, insert, select, func
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(prefix='/auth')
COOKIE = 'fynd_session'
TTL = 8 * 60 * 60
metadata = MetaData()
sessions = Table('admin_sessions', metadata,
    Column('token_hash', String(64), primary_key=True),
    Column('csrf', String(64), nullable=False),
    Column('expires_at', Float, nullable=False),
    Column('credential_revision', String(64), nullable=False))
attempts = Table('admin_login_attempts', metadata,
    Column('id', Integer, primary_key=True), Column('attempted_at', Float, nullable=False))


def configured_credentials():
    username = os.getenv('ADMIN_USERNAME', '')
    encoded = os.getenv('ADMIN_PASSWORD_HASH', '')
    password = os.getenv('ADMIN_PASSWORD', '')
    if not username.strip() or len(username) > 100 or not (encoded or password):
        raise HTTPException(503, 'Administrator credentials are not configured')
    if not encoded and len(password) > 256:
        raise HTTPException(503, 'Administrator credentials are not configured correctly')
    return username, 'hash' if encoded else 'password', encoded or password


@lru_cache(maxsize=1)
def password_hash(password):
    # Plaintext .env credentials are converted to a randomly salted verifier in memory.
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 600000)
    return f'pbkdf2_sha256$600000${salt.hex()}${digest.hex()}'


def credentials():
    username, kind, secret = configured_credentials()
    return username, secret if kind == 'hash' else password_hash(secret)


@lru_cache(maxsize=1)
def credential_revision(username, kind, secret):
    # Stable across service instances; slow derivation avoids a fast password oracle
    # in the session table when the operator chooses plaintext environment input.
    material = (username + '\0' + kind + '\0' + secret).encode()
    return hashlib.pbkdf2_hmac('sha256', material, b'fynd-session-credential-revision-v1', 600000).hex()


def revision():
    return credential_revision(*configured_credentials())


def secure_cookie(request):
    # Production deployment sets this explicitly; local HTTP remains usable.
    return os.getenv('AUTH_COOKIE_SECURE', 'false').lower() == 'true' or request.url.scheme == 'https'


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def session_for(request):
    token = request.cookies.get(COOKIE, '')
    if not token or len(token) > 128:
        return None
    with request.app.state.ops.engine.connect() as connection:
        session = connection.execute(select(sessions).where(
            sessions.c.token_hash == token_hash(token), sessions.c.expires_at > time.time(),
            sessions.c.credential_revision == revision())).mappings().first()
        return dict(session) if session else None


async def authorize(request):
    path = request.url.path
    if (request.method == 'POST' and path == '/auth/login') or (request.method == 'GET' and path == '/health/live'):
        return
    try:
        session = await asyncio.to_thread(session_for, request)
    except SQLAlchemyError:
        raise HTTPException(503, 'Authentication storage unavailable')
    if not session:
        raise HTTPException(401, 'Sign in to continue')
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        supplied = request.headers.get('x-csrf-token', '')
        if not secrets.compare_digest(supplied.encode(), session['csrf'].encode()):
            raise HTTPException(403, 'Invalid request token. Refresh and try again.')
    request.state.admin_session = session


class Login(BaseModel):
    model_config = ConfigDict(extra='forbid')
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


def login_sync(request, payload):
    engine = request.app.state.ops.engine
    now = time.time()
    with engine.connect() as connection:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        connection.execute(delete(attempts).where(attempts.c.attempted_at < now - 60))
        count = connection.execute(select(func.count()).select_from(attempts)).scalar_one()
        if count >= 10:
            raise HTTPException(429, 'Too many sign-in attempts. Try again in one minute.', headers={'Retry-After':'60'})
        connection.execute(insert(attempts).values(attempted_at=now))
        connection.commit()
    username, encoded = credentials()
    try:
        algorithm, iterations, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not 100000 <= int(iterations) <= 2000000:
            raise ValueError('Invalid hash')
        if len(bytes.fromhex(salt)) < 16 or len(bytes.fromhex(expected)) != 32:
            raise ValueError('Invalid hash')
        actual = hashlib.pbkdf2_hmac('sha256', payload.password.encode(), bytes.fromhex(salt), int(iterations)).hex()
    except (ValueError, TypeError):
        raise HTTPException(503, 'Administrator credentials are not configured correctly')
    valid_password = secrets.compare_digest(actual, expected)
    valid_username = secrets.compare_digest(payload.username.encode(), username.encode())
    if not (valid_password and valid_username):
        raise HTTPException(401, 'Invalid username or password')
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with engine.begin() as connection:
        connection.execute(delete(sessions).where(sessions.c.expires_at <= now))
        old_token = request.cookies.get(COOKIE, '')
        if old_token:
            connection.execute(delete(sessions).where(sessions.c.token_hash == token_hash(old_token)))
        connection.execute(insert(sessions).values(token_hash=token_hash(token), csrf=csrf,
            expires_at=now+TTL, credential_revision=revision()))
    return token, csrf, username


@router.post('/login')
def login(payload: Login, request: Request):
    if request.headers.get('x-requested-with') != 'fynd-console':
        raise HTTPException(403, 'Sign in through the application')
    try:
        token, csrf, username = login_sync(request, payload)
    except SQLAlchemyError:
        raise HTTPException(503, 'Authentication storage unavailable')
    response = JSONResponse({'username':username, 'role':'admin', 'csrf_token':csrf})
    response.set_cookie(COOKIE, token, max_age=TTL, httponly=True, secure=secure_cookie(request), samesite='strict', path='/')
    return response


@router.get('/session')
def session(request: Request):
    return {'username':credentials()[0], 'role':'admin', 'csrf_token':request.state.admin_session['csrf']}


@router.post('/logout')
def logout(request: Request):
    try:
        with request.app.state.ops.engine.begin() as connection:
            connection.execute(delete(sessions).where(sessions.c.token_hash == request.state.admin_session['token_hash']))
    except SQLAlchemyError:
        raise HTTPException(503, 'Could not sign out. Please try again.')
    response = JSONResponse({'signed_out':True})
    response.delete_cookie(COOKIE, httponly=True, secure=secure_cookie(request), samesite='strict', path='/')
    return response
