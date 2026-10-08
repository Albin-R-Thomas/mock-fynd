"""Pytest-only observer: capture actual store snapshots for the website replay."""
import json
import os
import threading
from pathlib import Path

_lock = threading.RLock()
_last = None
_test = ""


def emit(kind, message, *, stores=None, orders=None, decisions=None, available=True):
    global _last
    path = os.getenv("VERIFICATION_TRACE_PATH")
    if not path:
        return
    with _lock:
        state = json.dumps([stores, orders, available], sort_keys=True)
        if kind == "snapshot" and state == _last:
            return
        if stores is not None:
            _last = state
        record = dict(kind=kind, message=message, test=_test, available=available)
        if stores is not None:
            record.update(stores=stores, orders=orders or [])
        if decisions is not None:
            record['decisions'] = decisions
        with Path(path).open('a', encoding='utf-8') as output:
            output.write(json.dumps(record) + '\n')


def pytest_runtest_setup(item):
    global _test, _last
    _test = item.name
    _last = None


def pytest_runtest_logreport(report):
    if report.when == "call" or report.failed:
        emit("assertion", f"{report.nodeid.split('::')[-1]}: {report.outcome}")


def pytest_configure(config):
    from app.main import Operations
    import httpx
    snapshot = Operations.snapshot
    persist = Operations.persist_events
    restore = Operations.restore
    send = httpx.Client.send

    def observe(service, kind, message, decisions=None):
        try:
            state = snapshot(service)
            emit(kind, message, stores=state['stores'], orders=service.read_orders(), decisions=decisions)
        except Exception:
            emit(kind, message + " · store read unavailable", available=False, decisions=decisions)

    def traced_restore(service):
        result = restore(service)
        observe(service, "snapshot", "Service connected · read persisted store state")
        return result

    def traced_snapshot(service):
        try:
            result = snapshot(service)
        except Exception:
            emit("unavailable", "Authoritative store read unavailable", available=False)
            raise
        emit("snapshot", "Independent reader · current store state", stores=result['stores'], orders=service.read_orders())
        return result

    def traced_persist(service, batch):
        try:
            result = persist(service, batch)
        except Exception as exc:
            observe(service, "failure", f"Write interrupted ({type(exc).__name__}) · inspect persisted state")
            raise
        decisions = [{**entry, "order_id": event.order_id, "status": event.status, "version": event.version}
                     for entry, event in zip(result['results'], batch)]
        message = "; ".join(f"{d['event_id']} · {d['status']} v{d['version']} → {d['outcome']}" for d in decisions)
        observe(service, "event", message, decisions)
        return result

    def traced_send(client, request, **kwargs):
        response = send(client, request, **kwargs)
        # Do not consume SSE streams; record only completed local API responses.
        if not kwargs.get('stream') and request.url.host in ('127.0.0.1', 'localhost', 'testserver'):
            try:
                value = response.json()
                if request.url.path == '/dashboard' and response.status_code == 200:
                    emit('snapshot', 'HTTP reader · authoritative store dashboard', stores=value['stores'])
                elif request.url.path == '/events' and request.method == 'POST' and response.status_code == 200:
                    emit('event', 'HTTP processor · ' + ', '.join(d['outcome'] for d in value['results']), decisions=value['results'])
                elif response.status_code >= 400:
                    emit('rejected', f'{request.method} {request.url.path} → HTTP {response.status_code}', available=response.status_code != 503)
            except (ValueError, KeyError, TypeError):
                pass
        return response

    Operations.restore = traced_restore
    Operations.snapshot = traced_snapshot
    Operations.persist_events = traced_persist
    httpx.Client.send = traced_send
