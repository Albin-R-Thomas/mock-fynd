"""Individually runnable brief fixtures, with input/decision/read evidence for the UI."""
import json
import pytest
from app.main import Event, Operations, STATUSES


@pytest.mark.parametrize("scenario", ["retry", "identity", "late", "equal", "correction", "store", "returned"])
def test_contract(tmp_path, monkeypatch, scenario):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'showcase.db'}")
    writer, reader = Operations(), Operations()
    writer.restore()
    reader.restore()
    initial = dict(event_id="e1", order_id="o1", store_id="s1", version=1,
                   status="open", occurred_at="2026-01-01T00:00:00Z")

    def send(expected, **changes):
        payload = {**initial, **changes}
        result = writer.persist_events([Event(**payload)])['results'][0]
        print("\nREQUEST " + json.dumps(payload))
        print("DECISION " + json.dumps(result))
        assert result['outcome'] == expected

    try:
        send("applied")
        status = "open"
        if scenario == "retry":
            send("duplicate")
        elif scenario == "identity":
            send("event_id_conflict", status="packed")
            assert reader.read_events()[0]['status'] == "open"
        else:
            send("applied", event_id="e3", version=3, status="delivered")
            status = "delivered"
            if scenario == "late":
                send("stale", event_id="e2", version=2, status="packed")
            elif scenario == "equal":
                send("stale", event_id="equal", version=3)
            else:
                send("applied", event_id="e4", version=4, status="packed")
                status = "packed"
                if scenario == "store":
                    send("store_conflict", event_id="e5", store_id="s2", version=5)
                    send("store_conflict", event_id="older-store", store_id="s2", version=1)
                elif scenario == "returned":
                    send("applied", event_id="e6", order_id="o2", store_id="s2", status="returned")
        stores = reader.snapshot()['stores']
        print("READER SNAPSHOT " + json.dumps(stores))
        assert stores[0]['total'] == 1 and stores[0][status] == 1
        assert len(stores) == (2 if scenario == "returned" else 1)
        if scenario == "returned":
            assert stores[1]['returned'] == 1 and stores[1]['total'] == 1
        assert all(s['total'] == sum(s[k] for k in STATUSES) for s in stores)
        print("ASSERTIONS verified: decision, independent reader, retained counts, status-sum invariant")
    finally:
        writer.engine.dispose()
        reader.engine.dispose()
