"""Counter-reset logic and /stats. Tunis = UTC+1 (no DST)."""
from datetime import timedelta

from conftest import APP_H, utc


def stats(client, frm, to, bucket="hour", **kw):
    r = client.get("/stats", params={"from": frm.isoformat(), "to": to.isoformat(), "bucket": bucket, **kw},
                   headers=APP_H)
    assert r.status_code == 200, r.text
    return r.json()


def test_simple_increase(client, add):
    t = utc(2026, 1, 10, 8, 0)
    add([(t + timedelta(seconds=10 * i), 100 + i) for i in range(6)])  # +5
    body = stats(client, t, t + timedelta(hours=1))
    assert body["totals"]["production"] == 5
    assert body["label"] == "Line 2"
    assert body["from"].endswith("Z")


def test_reset_is_never_negative(client, add):
    t = utc(2026, 1, 10, 8, 0)
    # 100 -> 150 (+50), reset -> 10 (+10), -> 30 (+20)
    add([(t, 100), (t + timedelta(seconds=10), 150), (t + timedelta(seconds=20), 10),
         (t + timedelta(seconds=30), 30)])
    body = stats(client, t, t + timedelta(hours=1))
    assert body["totals"]["production"] == 80


def test_reset_to_zero_and_signed_wrap(client, add):
    t = utc(2026, 1, 10, 8, 0)
    add([(t, 500), (t + timedelta(seconds=10), 0), (t + timedelta(seconds=20), 7),
         (t + timedelta(seconds=30), 2147483647), (t + timedelta(seconds=40), -2147483648),
         (t + timedelta(seconds=50), -2147483600)])
    body = stats(client, t, t + timedelta(hours=1))
    # 0 + 7 + (2147483647-7) + 0 (negative after drop) + 48
    assert body["totals"]["production"] == 2147483647 + 48
    assert all(i["production"] >= 0 for i in body["items"])


def test_baseline_before_window_is_used(client, add):
    add([(utc(2026, 1, 10, 7, 59, 50), 100), (utc(2026, 1, 10, 8, 0, 10), 110)])
    body = stats(client, utc(2026, 1, 10, 8), utc(2026, 1, 10, 9))
    assert body["totals"]["production"] == 10


def test_hour_buckets_include_empty_hours(client, add):
    t = utc(2026, 1, 10, 8, 0)
    add([(t, 0), (t + timedelta(seconds=30), 5), (t + timedelta(hours=2, seconds=30), 9)])
    body = stats(client, t, t + timedelta(hours=3))
    assert [i["production"] for i in body["items"]] == [5, 0, 4]
    assert body["items"][0]["start"] == "2026-01-10T08:00:00Z"


def test_day_buckets_use_tunis_midnight(client, add):
    # 22:30 UTC = 23:30 local (day 10); 23:30 UTC = 00:30 local (day 11)
    add([(utc(2026, 1, 10, 22, 0), 0), (utc(2026, 1, 10, 22, 30), 3), (utc(2026, 1, 10, 23, 30), 10)])
    body = stats(client, utc(2026, 1, 9, 23), utc(2026, 1, 11, 23), bucket="day")
    assert [i["start"] for i in body["items"]] == ["2026-01-09T23:00:00Z", "2026-01-10T23:00:00Z"]
    assert [i["production"] for i in body["items"]] == [3, 7]


def test_shift_buckets(client, add):
    # Shifts local A 06-14, B 14-22, C 22-06  => UTC A 05-13, B 13-21, C 21-05
    add([(utc(2026, 1, 10, 5, 0), 0), (utc(2026, 1, 10, 5, 30), 10),   # A +10
         (utc(2026, 1, 10, 13, 30), 15),                               # B +5
         (utc(2026, 1, 10, 21, 30), 17)])                              # C +2
    body = stats(client, utc(2026, 1, 10, 5), utc(2026, 1, 11, 5), bucket="shift")
    assert [(i["shift"], i["production"]) for i in body["items"]] == [("A", 10), ("B", 5), ("C", 2)]
    assert body["items"][2]["start"] == "2026-01-10T21:00:00Z"


def test_run_stop_and_no_data(client, add):
    t = utc(2026, 1, 10, 8, 0)
    rows = [(t + timedelta(seconds=s), v) for s, v in [(0, 0), (30, 10), (60, 20)]]   # run 60 s
    rows += [(t + timedelta(seconds=120), 20, True), (t + timedelta(seconds=180), 20, True)]  # stop 120 s
    rows += [(t + timedelta(seconds=180 + 600), 25)]                                  # 10 min gap -> no data
    add(rows)
    it = stats(client, t, t + timedelta(hours=1))["items"][0]
    assert it["run_seconds"] == 60
    assert it["stop_seconds"] == 120
    assert it["no_data_seconds"] == 3600 - 180
    assert it["uptime"] == round(60 / 180, 4)
    assert it["production"] == 25
    assert it["waste"] is None and it["efficiency"] is None


def test_other_source_isolated(client, add):
    t = utc(2026, 1, 10, 8, 0)
    add([(t, 0), (t + timedelta(seconds=5), 100)], source="other")
    assert stats(client, t, t + timedelta(hours=1))["totals"]["production"] == 0


def test_validation(client):
    t = utc(2026, 1, 10, 8, 0)
    r = client.get("/stats", params={"from": t.isoformat(), "to": (t - timedelta(hours=1)).isoformat()},
                   headers=APP_H)
    assert r.status_code == 422
    r = client.get("/stats", params={"from": t.isoformat(), "to": (t + timedelta(days=200)).isoformat(),
                                     "bucket": "hour"}, headers=APP_H)
    assert r.status_code == 422  # too many buckets
