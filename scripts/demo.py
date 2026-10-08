"""Start three local HTTP processes and prove shared state, races, SSE and restart."""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import secrets
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

STATUSES = ('open', 'packed', 'out_for_delivery', 'delivered', 'returned')


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def run_demo(directory, *, hold=False, ports=None):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / 'shared.db'
    if database.exists():
        raise ValueError('Use a fresh demo directory; existing database is never deleted')
    env = {**os.environ, 'DATABASE_URL': 'sqlite:///' + database.as_posix(),
           'MOCK_AUTOSTART': 'false', 'REDIS_URL': 'redis://127.0.0.1:6399/0', 'AUTH_COOKIE_SECURE': 'false'}
    env['ADMIN_USERNAME'] = 'fixture-admin'
    env['ADMIN_PASSWORD'] = os.getenv('DEMO_ADMIN_PASSWORD') or secrets.token_urlsafe(24)
    env.pop('ADMIN_PASSWORD_HASH', None)
    ports = ports or [free_port() for _ in range(3)]
    urls = [f'http://127.0.0.1:{p}' for p in ports]
    processes, logs = [], []
    clients = {}

    def start(index):
        log = (directory / f'server-{index}.log').open('a')
        logs.append(log)
        process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(ports[index])], env=env, stdout=log, stderr=log)
        processes.append(process)
        deadline = time.monotonic() + 20
        while True:
            if process.poll() is not None:
                raise RuntimeError((directory / f'server-{index}.log').read_text())
            try:
                response = httpx.get(urls[index] + '/health/live', timeout=1)
                if response.status_code == 200:
                    if index not in clients:
                        clients[index] = httpx.Client(base_url=urls[index], timeout=5)
                    login = clients[index].post('/auth/login', json={'username':env['ADMIN_USERNAME'],'password':env['ADMIN_PASSWORD']}, headers={'x-requested-with':'fynd-console'})
                    login.raise_for_status()
                    clients[index].headers['x-csrf-token'] = login.json()['csrf_token']
                    return process
            except httpx.HTTPError:
                pass
            if time.monotonic() > deadline:
                raise RuntimeError('HTTP startup timed out')
            time.sleep(0.05)

    def event(event_id='e1', version=1, status='open', **kwargs):
        return dict(event_id=event_id, order_id='o1', store_id='s1', version=version, status=status,
                    occurred_at='2026-01-01T00:00:00Z') | kwargs

    def post(index, value):
        response = clients[index].post('/events', json=value, timeout=5)
        response.raise_for_status()
        return response.json()

    def read():
        response = clients[2].get('/dashboard', timeout=3)
        response.raise_for_status()
        stores = response.json()['stores']
        assert all(s['total'] == sum(s[k] for k in STATUSES) for s in stores)
        return stores

    def race(values):
        barrier = threading.Barrier(2)
        def work(pair):
            barrier.wait(timeout=10)
            return post(*pair)
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(work, enumerate(values)))

    try:
        for i in range(3):
            start(i)
        assert post(0, event())['applied'] == 1
        committed = time.monotonic()
        assert read()[0]['open'] == 1
        dashboard_latency = time.monotonic() - committed
        assert dashboard_latency < 5
        assert post(1, event())['results'][0]['outcome'] == 'duplicate'
        assert post(1, event(status='packed'))['results'][0]['outcome'] == 'event_id_conflict'
        version_race = race([event('e2', 2, 'packed'), event('e3', 3, 'delivered')])
        assert read()[0]['delivered'] == 1
        same = event('same', 1, 'returned', order_id='o2', store_id='s2')
        same_race = race([same, same])
        assert sorted(r['results'][0]['outcome'] for r in same_race) == ['applied', 'duplicate']
        # Keep a real stream open BEFORE another process commits a correction.
        with clients[2].stream('GET', '/stream', timeout=5) as response:
            response.raise_for_status()
            lines = response.iter_lines()
            while not next(lines).startswith('data: '):
                pass
            assert post(0, event('e4', 4, 'packed'))['applied'] == 1
            committed = time.monotonic()
            for line in lines:
                if line.startswith('data: '):
                    stores = json.loads(line[6:])['stores']
                    if stores[0]['packed'] == 1:
                        sse_latency = time.monotonic() - committed
                        break
            assert sse_latency < 5
        assert post(1, event('e5', 5, 'open', store_id='s2'))['results'][0]['outcome'] == 'store_conflict'
        # Actual API process restart, separate from transactional crash hooks.
        processes[0].kill()
        processes[0].wait(timeout=10)
        start(0)
        assert post(0, event('e4', 4, 'packed'))['results'][0]['outcome'] == 'duplicate'
        stores = read()
        assert stores[0]['total'] == stores[0]['packed'] == 1
        assert stores[1]['total'] == stores[1]['returned'] == 1
        report = {'dashboard_visibility_seconds': round(dashboard_latency, 3),
                  'sse_visibility_seconds': round(sse_latency, 3), 'version_race': version_race,
                  'identical_race': same_race, 'stores': stores, 'reader_url': urls[2],
                  'database': str(database), 'api_restart_retry': 'duplicate'}
        (directory / 'evidence.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2), flush=True)
        if hold:
            print('Three instances remain running for browser confirmation. Ctrl+C stops only these processes.', flush=True)
            while True:
                time.sleep(0.2)
        return report
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for client in clients.values():
            client.close()
        for log in logs:
            log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory')
    parser.add_argument('--hold', action='store_true')
    parser.add_argument('--ports', type=int, nargs=3)
    args = parser.parse_args()
    directory = args.directory or tempfile.mkdtemp(prefix='round2-demo-')
    run_demo(directory, hold=args.hold, ports=args.ports)
