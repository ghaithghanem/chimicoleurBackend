import logging
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone

from .buffer import Buffer, Row
from .config import Tag
from .readers import Reader
from .sink import RejectedBatch

log = logging.getLogger("collector")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Collector:
    """Polls the PLC; buffers a row on every value change, plus a heartbeat row when nothing
    was written for a tag during `heartbeat_s`. Never raises out of run()."""

    def __init__(self, reader: Reader, tags: list[Tag], buffer: Buffer, poll_interval_s: float = 0.2,
                 heartbeat_s: float = 60.0, max_backoff_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] = utcnow):
        self.reader, self.tags, self.buffer = reader, tags, buffer
        self.poll_interval_s, self.heartbeat_s, self.max_backoff_s = poll_interval_s, heartbeat_s, max_backoff_s
        self.clock, self.now = clock, now
        self.connected = False
        self.backoff_s = 1.0
        # source -> (last value, monotonic time of last written row)
        self.last: dict[str, tuple[int, float]] = {}

    def poll_once(self) -> list[Row]:
        rows = []
        for tag in self.tags:
            value = self.reader.read(tag)
            t, ts = self.clock(), self.now()
            prev = self.last.get(tag.source)
            if prev is None or value != prev[0]:
                rows.append(Row(tag.source, ts, value, False))
                self.last[tag.source] = (value, t)
            elif t - prev[1] >= self.heartbeat_s:
                rows.append(Row(tag.source, ts, value, True))
                self.last[tag.source] = (value, t)
        return rows

    def _disconnect(self) -> None:
        self.connected = False
        try:
            self.reader.close()
        except Exception:  # noqa: BLE001
            log.debug("reader.close failed", exc_info=True)

    def step(self) -> float:
        """One loop iteration. Returns how long to wait before the next one."""
        if not self.connected:
            try:
                self.reader.connect()
                self.connected = True
                self.backoff_s = 1.0
            except Exception as e:  # noqa: BLE001
                wait = self.backoff_s * random.uniform(0.8, 1.2)
                log.warning("PLC connect failed (%s); retrying in %.1fs", e, wait)
                self.backoff_s = min(self.backoff_s * 2, self.max_backoff_s)
                return wait
        t0 = self.clock()
        try:
            rows = self.poll_once()
        except Exception as e:  # noqa: BLE001
            log.error("PLC read failed (%s); reconnecting", e)
            self._disconnect()
            return 0.0
        try:
            self.buffer.add(rows)
        except Exception:  # noqa: BLE001
            log.exception("could not write to local buffer")
        return max(0.0, self.poll_interval_s - (self.clock() - t0))

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                wait = self.step()
            except Exception:  # noqa: BLE001  (belt and braces: the loop must never die)
                log.exception("unexpected error in collector loop")
                wait = 1.0
            if wait:
                stop.wait(wait)
        self._disconnect()


class Flusher:
    """Sends buffered rows to the API in order, deleting them only after a successful response."""

    def __init__(self, buffer: Buffer, sink, batch_size: int = 500, interval_s: float = 2.0,
                 max_backoff_s: float = 60.0):
        self.buffer, self.sink = buffer, sink
        self.batch_size, self.interval_s, self.max_backoff_s = batch_size, interval_s, max_backoff_s
        self.backoff_s = interval_s

    def flush_once(self) -> int:
        batch = self.buffer.peek(self.batch_size)
        if not batch:
            return 0
        ids = [i for i, _ in batch]
        try:
            self.sink.send([row for _, row in batch])
        except RejectedBatch as e:
            log.error("API rejected %d rows, moved to 'dead' table: %s", len(ids), e)
            self.buffer.move_to_dead(ids)
            return len(ids)
        self.buffer.delete(ids)
        return len(ids)

    def step(self) -> float:
        try:
            n = self.flush_once()
        except Exception as e:  # noqa: BLE001
            log.warning("flush failed (%s); %d rows buffered; retrying in %.0fs",
                        e, self.buffer.count(), self.backoff_s)
            wait = self.backoff_s
            self.backoff_s = min(self.backoff_s * 2, self.max_backoff_s)
            return wait
        self.backoff_s = self.interval_s
        return 0.0 if n == self.batch_size else self.interval_s  # drain backlog quickly

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            try:
                wait = self.step()
            except Exception:  # noqa: BLE001
                log.exception("unexpected error in flusher loop")
                wait = self.interval_s
            if wait:
                stop.wait(wait)
        try:  # best effort on shutdown; anything left stays in the buffer for next start
            self.flush_once()
        except Exception:  # noqa: BLE001
            pass
