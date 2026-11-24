"""Durable local outbox (SQLite). Every reading is written here first and deleted only after
the API acknowledged it, so nothing is lost if the API/network/collector goes down."""

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Row:
    source: str
    ts: datetime  # timezone-aware UTC
    value: int
    is_heartbeat: bool = False

    def to_json(self) -> dict:
        return {"source": self.source, "ts": self.ts.isoformat(), "value": self.value,
                "is_heartbeat": self.is_heartbeat}


class Buffer:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        # Default isolation: `with self._conn` wraps each write in one transaction (atomic move_to_dead).
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        for table in ("pending", "dead"):
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {table} (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "source TEXT NOT NULL, ts TEXT NOT NULL, value INTEGER NOT NULL, is_heartbeat INTEGER NOT NULL)"
            )

    def add(self, rows: list[Row]) -> None:
        if not rows:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO pending (source, ts, value, is_heartbeat) VALUES (?, ?, ?, ?)",
                [(r.source, r.ts.isoformat(), r.value, int(r.is_heartbeat)) for r in rows],
            )

    def peek(self, n: int) -> list[tuple[int, Row]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT id, source, ts, value, is_heartbeat FROM pending ORDER BY id LIMIT ?", (n,)
            )
            return [(i, Row(s, datetime.fromisoformat(t), v, bool(h))) for i, s, t, v, h in cur.fetchall()]

    def delete(self, ids: list[int]) -> None:
        with self._lock, self._conn:
            self._conn.executemany("DELETE FROM pending WHERE id = ?", [(i,) for i in ids])

    def move_to_dead(self, ids: list[int]) -> None:
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO dead (source, ts, value, is_heartbeat) "
                "SELECT source, ts, value, is_heartbeat FROM pending WHERE id = ?", [(i,) for i in ids])
            self._conn.executemany("DELETE FROM pending WHERE id = ?", [(i,) for i in ids])

    def count(self, table: str = "pending") -> int:
        assert table in ("pending", "dead")
        with self._lock:
            return self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
