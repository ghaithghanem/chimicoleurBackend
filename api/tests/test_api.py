from datetime import datetime, timedelta, timezone

from conftest import APP_H, INGEST_H, utc


def test_health_no_auth(client):
    assert client.get("/health").json() == {"status": "ok", "db": "ok"}


def test_auth_keys_are_separate(client):
    assert client.get("/readings/latest").status_code == 401
    assert client.get("/readings/latest", headers=INGEST_H).status_code == 401
    body = {"readings": [{"ts": "2026-01-10T08:00:00Z", "value": 1}]}
    assert client.post("/ingest", json=body, headers=APP_H).status_code == 401
    assert client.post("/ingest", json=body, headers=INGEST_H).status_code == 200


def test_latest_no_data(client):
    body = client.get("/readings/latest", headers=APP_H).json()
    assert body["status"] == "no_data" and body["value"] is None


def test_latest_ok_and_stale(client, add):
    now = datetime.now(timezone.utc)
    add([(now - timedelta(seconds=90), 5), (now - timedelta(seconds=70), 7), (now - timedelta(seconds=10), 7, True)])
    body = client.get("/readings/latest", headers=APP_H).json()
    assert body["status"] == "ok" and body["value"] == 7
    assert body["last_change_ts"].startswith((now - timedelta(seconds=70)).strftime("%Y-%m-%dT%H:%M:%S"))
    assert body["label"] == "Line 2"


def test_latest_stale(client, add):
    add([(datetime.now(timezone.utc) - timedelta(seconds=300), 5)])
    assert client.get("/readings/latest", headers=APP_H).json()["status"] == "stale"


def test_ingest_dedup_and_validation(client):
    batch = {"readings": [
        {"ts": "2026-01-10T08:00:00Z", "value": 1},
        {"ts": "2026-01-10T09:00:00+01:00", "value": 1, "is_heartbeat": True},  # = 08:00Z heartbeat
        {"ts": "2026-01-10T08:00:05Z", "value": 2},
    ]}
    assert client.post("/ingest", json=batch, headers=INGEST_H).json() == {"received": 3, "inserted": 3}
    # Re-sent batch (e.g. response lost) is idempotent.
    assert client.post("/ingest", json=batch, headers=INGEST_H).json()["inserted"] == 0
    naive = {"readings": [{"ts": "2026-01-10T08:00:00", "value": 1}]}
    assert client.post("/ingest", json=naive, headers=INGEST_H).status_code == 422


def test_readings_pagination(client, add):
    t = utc(2026, 1, 10, 8)
    add([(t + timedelta(seconds=i), i) for i in range(5)] + [(t + timedelta(seconds=10), 4, True)])
    params = {"from": t.isoformat(), "to": (t + timedelta(minutes=1)).isoformat(), "limit": 2}
    p1 = client.get("/readings", params=params, headers=APP_H).json()
    assert [r["value"] for r in p1["items"]] == [4, 3] and p1["next_offset"] == 2
    p3 = client.get("/readings", params={**params, "offset": 4}, headers=APP_H).json()
    assert [r["value"] for r in p3["items"]] == [0] and p3["next_offset"] is None
    allr = client.get("/readings", params={**params, "limit": 10, "include_heartbeats": True}, headers=APP_H).json()
    assert len(allr["items"]) == 6 and allr["items"][0]["is_heartbeat"] is True
