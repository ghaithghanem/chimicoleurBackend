"""PLC readers. READ-ONLY: the S7 is a production machine.

Snap7Reader talks to the PLC only through ReadOnlyClient, which whitelists the snap7 methods
that cannot change PLC state. Any write/stop/download method raises PermissionError.
"""

import logging
import random
import struct
import time
from typing import Protocol

from .config import TYPE_FORMATS, Tag

log = logging.getLogger("collector.reader")


def decode(tag: Tag, data: bytes | bytearray) -> int:
    if len(data) != tag.size:
        raise ValueError(f"{tag.source}: expected {tag.size} bytes, got {len(data)}")
    return struct.unpack(TYPE_FORMATS[tag.type], bytes(data))[0]


class Reader(Protocol):
    def connect(self) -> None: ...
    def read(self, tag: Tag) -> int: ...
    def close(self) -> None: ...


class ReadOnlyClient:
    ALLOWED = frozenset({"connect", "disconnect", "destroy", "get_connected", "read_area"})

    def __init__(self, client):
        object.__setattr__(self, "_client", client)

    def __getattr__(self, name):
        if name not in self.ALLOWED:
            raise PermissionError(f"snap7 method {name!r} is not allowed (read-only collector)")
        return getattr(self._client, name)

    def __setattr__(self, name, value):
        raise PermissionError("ReadOnlyClient is immutable")


def _snap7_areas():
    try:
        from snap7.type import Areas  # python-snap7 >= 2.0
    except ImportError:  # pragma: no cover
        from snap7.types import Areas  # python-snap7 1.x
    return {"MK": Areas.MK, "DB": Areas.DB, "PE": Areas.PE, "PA": Areas.PA}


class Snap7Reader:
    def __init__(self, ip: str, rack: int, slot: int):
        self.ip, self.rack, self.slot = ip, rack, slot
        self._client: ReadOnlyClient | None = None
        self._areas = None

    def connect(self) -> None:
        import snap7

        self.close()
        self._areas = self._areas or _snap7_areas()
        client = ReadOnlyClient(snap7.client.Client())
        client.connect(self.ip, self.rack, self.slot)
        if not client.get_connected():
            raise ConnectionError(f"could not connect to PLC {self.ip}")
        self._client = client
        log.info("connected to PLC %s rack=%s slot=%s", self.ip, self.rack, self.slot)

    def read(self, tag: Tag) -> int:
        if self._client is None:
            raise ConnectionError("not connected")
        data = self._client.read_area(self._areas[tag.area], tag.db, tag.offset, tag.size)
        return decode(tag, data)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.disconnect()
                self._client.destroy()
            except Exception:  # noqa: BLE001
                log.debug("error while closing PLC client", exc_info=True)
            self._client = None


class FakePLCReader:
    """Simulated counter: runs at ~rate_per_min, with random stops, occasional resets and link drops."""

    def __init__(self, rate_per_min: float = 24.0, disconnect_prob: float = 0.0, seed: int | None = None,
                 clock=time.monotonic):
        self.rate_per_s = rate_per_min / 60
        self.disconnect_prob = disconnect_prob
        self.rng = random.Random(seed)
        self.clock = clock
        self.connected = False
        self._state: dict[str, dict] = {}

    def connect(self) -> None:
        self.connected = True
        log.info("connected to FAKE PLC")

    def close(self) -> None:
        self.connected = False

    def read(self, tag: Tag) -> int:
        if not self.connected:
            raise ConnectionError("fake PLC not connected")
        if self.disconnect_prob and self.rng.random() < self.disconnect_prob:
            self.connected = False
            raise ConnectionError("fake PLC link dropped")
        now = self.clock()
        st = self._state.setdefault(tag.source, {"value": self.rng.randint(0, 5000), "acc": 0.0, "t": now,
                                                 "running": True, "until": now + self.rng.uniform(60, 600)})
        if now >= st["until"]:
            st["running"] = not st["running"]
            st["until"] = now + (self.rng.uniform(60, 600) if st["running"] else self.rng.uniform(30, 180))
            if not st["running"] and self.rng.random() < 0.05:
                st["value"] = 0  # operator reset
        if st["running"]:
            st["acc"] += (now - st["t"]) * self.rate_per_s
            step = int(st["acc"])
            st["acc"] -= step
            st["value"] = (st["value"] + step) % 2**31
        st["t"] = now
        return st["value"]
