import inspect
from datetime import datetime, timedelta, timezone

import pytest

from collector import readers
from collector.buffer import Buffer, Row
from collector.config import Tag, parse_tags
from collector.core import Collector, Flusher
from collector.readers import FakePLCReader, ReadOnlyClient, decode
from collector.sink import RejectedBatch

TAG = Tag("plc_line_2", "MK", 0, 10, "DINT")
T0 = datetime(2026, 1, 10, 8, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def now(self):
        return T0 + timedelta(seconds=self.t)


class ScriptedReader:
    """Returns values from a list; an Exception instance in the list is raised instead."""

    def __init__(self, values, connect_failures=0):
        self.values = list(values)
        self.connect_failures = connect_failures
        self.connects = self.closes = 0

    def connect(self):
        self.connects += 1
        if self.connect_failures > 0:
            self.connect_failures -= 1
            raise ConnectionError("unreachable")

    def read(self, tag):
        v = self.values.pop(0)
        if isinstance(v, Exception):
            raise v
        return v

    def close(self):
        self.closes += 1


class FlakySink:
    def __init__(self, fail_times=0):
        self.fail_times = fail_times
        self.received: list[Row] = []

    def send(self, rows):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("API down")
        self.received.extend(rows)


@pytest.fixture
def buf(tmp_path):
    b = Buffer(str(tmp_path / "buf.sqlite3"))
    yield b
    b.close()


# ---- config / decoding -------------------------------------------------------------------------

def test_parse_tags():
    tags = parse_tags("plc_line_2:MK:0:10:DINT; plc_line_2.waste:db:5:4:int")
    assert tags[0] == TAG and tags[1] == Tag("plc_line_2.waste", "DB", 5, 4, "INT")
    with pytest.raises(ValueError):
        parse_tags("x:MK:0:10:REAL")
    with pytest.raises(ValueError):
        parse_tags("a:MK:0:10:DINT;a:MK:0:14:DINT")


def test_decode_signed_big_endian():
    assert decode(TAG, bytes([0x00, 0x00, 0x30, 0x39])) == 12345
    assert decode(TAG, bytes([0xFF, 0xFF, 0xFF, 0xFE])) == -2


# ---- read-only guarantee -------------------------------------------------------------------------

@pytest.mark.parametrize("method", ["mb_write", "db_write", "write_area", "plc_stop", "plc_cold_start",
                                    "download", "delete", "set_plc_datetime", "eb_write", "ab_write"])
def test_read_only_client_blocks_writes(method):
    class Dummy:
        def __getattr__(self, name):
            return lambda *a, **k: "called"

    with pytest.raises(PermissionError):
        getattr(ReadOnlyClient(Dummy()), method)


def test_reader_module_never_calls_snap7_writes():
    src = inspect.getsource(readers)
    for forbidden in ("_write(", "write_area(", "plc_stop(", "plc_hot_start(", "plc_cold_start(", "download("):
        assert forbidden not in src


# ---- change + heartbeat --------------------------------------------------------------------------

def test_change_and_heartbeat(buf):
    clock = Clock()
    reader = ScriptedReader([5, 5, 5, 5, 6, 6])
    c = Collector(reader, [TAG], buf, poll_interval_s=0.2, heartbeat_s=60, clock=clock, now=clock.now)
    for t in (0, 30, 59.9, 60, 61, 62):
        clock.t = t
        c.step()
    rows = [r for _, r in buf.peek(100)]
    assert [(r.value, r.is_heartbeat, (r.ts - T0).total_seconds()) for r in rows] == [
        (5, False, 0), (5, True, 60), (6, False, 61)]


# ---- reconnect / never crash ---------------------------------------------------------------------

def test_reconnect_with_backoff_and_no_crash(buf):
    clock = Clock()
    reader = ScriptedReader([1, ConnectionError("link down"), 2], connect_failures=0)
    c = Collector(reader, [TAG], buf, clock=clock, now=clock.now, max_backoff_s=8)
    c.step()                       # connect + read 1
    assert c.step() == 0.0         # read fails -> disconnect, no exception
    assert not c.connected and reader.closes == 1
    reader.connect_failures = 5
    waits = [c.step() for _ in range(5)]  # connect keeps failing -> growing backoff, capped
    assert waits[0] < waits[2] and max(waits) <= 8 * 1.2
    c.step()                       # connect succeeds, read 2
    assert c.connected and c.backoff_s == 1.0
    assert [r.value for _, r in buf.peek(10)] == [1, 2]


def test_unexpected_error_in_step_does_not_kill_run(buf, monkeypatch):
    import threading

    c = Collector(ScriptedReader([]), [TAG], buf)
    calls = {"n": 0}
    stop = threading.Event()

    def boom():
        calls["n"] += 1
        if calls["n"] >= 2:
            stop.set()
        raise RuntimeError("bug")

    monkeypatch.setattr(c, "step", boom)
    monkeypatch.setattr(stop, "wait", lambda *_: None)
    c.run(stop)  # returns normally
    assert calls["n"] == 2


# ---- buffer / flush ------------------------------------------------------------------------------

def rows(n, start=0):
    return [Row("plc_line_2", T0 + timedelta(seconds=i), i) for i in range(start, start + n)]


def test_buffer_persists_across_restart(tmp_path):
    path = str(tmp_path / "b.sqlite3")
    b = Buffer(path)
    b.add(rows(3))
    b.close()
    b2 = Buffer(path)
    assert [r.value for _, r in b2.peek(10)] == [0, 1, 2]
    assert b2.peek(1)[0][1].ts == T0
    b2.close()


def test_flush_keeps_data_while_api_down_then_drains_in_order(buf):
    buf.add(rows(7))
    sink = FlakySink(fail_times=3)
    f = Flusher(buf, sink, batch_size=3, interval_s=1, max_backoff_s=4)
    waits = [f.step() for _ in range(3)]       # API down
    assert waits == [1, 2, 4] and buf.count() == 7 and sink.received == []
    buf.add(rows(2, start=7))                  # collector keeps buffering meanwhile
    assert f.step() == 0.0                     # full batch -> drain immediately
    f.step()
    f.step()
    assert [r.value for r in sink.received] == list(range(9))
    assert buf.count() == 0 and f.backoff_s == 1


def test_rejected_batch_goes_to_dead_letter(buf):
    buf.add(rows(2))

    class Rejecting:
        def send(self, rows):
            raise RejectedBatch("bad")

    f = Flusher(buf, Rejecting(), batch_size=10)
    f.step()
    assert buf.count() == 0 and buf.count("dead") == 2


# ---- fake reader ---------------------------------------------------------------------------------

def test_fake_reader_counts_up_and_drops_link():
    clock = Clock()
    r = FakePLCReader(rate_per_min=60, seed=1, clock=clock)
    r.connect()
    first = r.read(TAG)
    clock.t = 30
    assert r.read(TAG) == first + 30  # first run phase lasts >= 60 s
    flaky = FakePLCReader(disconnect_prob=1.0, seed=1, clock=clock)
    flaky.connect()
    with pytest.raises(ConnectionError):
        flaky.read(TAG)
