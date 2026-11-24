"""Collector configuration from environment variables.

TAGS: semicolon-separated list of  source:AREA:DB:OFFSET:TYPE
  AREA  MK (Merker/M), DB (data block), PE (inputs/I), PA (outputs/Q)
  DB    data block number (0 for non-DB areas)
  TYPE  DINT | INT | DWORD | WORD | BYTE   (big-endian, as stored by the S7)
Example: TAGS=plc_line_2:MK:0:10:DINT;plc_line_2.waste:DB:5:0:DINT
A DB must have "Optimized block access" unchecked in TIA Portal, or snap7 cannot read it.
"""

import os
from dataclasses import dataclass

TYPE_FORMATS = {"DINT": ">i", "INT": ">h", "DWORD": ">I", "WORD": ">H", "BYTE": ">B"}
TYPE_SIZES = {"DINT": 4, "INT": 2, "DWORD": 4, "WORD": 2, "BYTE": 1}
AREAS = {"MK", "DB", "PE", "PA"}


@dataclass(frozen=True)
class Tag:
    source: str
    area: str
    db: int
    offset: int
    type: str

    @property
    def size(self) -> int:
        return TYPE_SIZES[self.type]


def parse_tags(spec: str) -> list[Tag]:
    tags = []
    for item in filter(None, (s.strip() for s in spec.split(";"))):
        parts = [p.strip() for p in item.split(":")]
        if len(parts) != 5:
            raise ValueError(f"bad tag spec {item!r}, expected source:AREA:DB:OFFSET:TYPE")
        source, area, db, offset, typ = parts
        area, typ = area.upper(), typ.upper()
        if area not in AREAS:
            raise ValueError(f"unknown area {area!r} in {item!r}")
        if typ not in TYPE_FORMATS:
            raise ValueError(f"unsupported type {typ!r} in {item!r}")
        tags.append(Tag(source, area, int(db), int(offset), typ))
    if not tags:
        raise ValueError("TAGS is empty")
    if len({t.source for t in tags}) != len(tags):
        raise ValueError("duplicate source names in TAGS")
    return tags


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None or val == "":
        raise ValueError(f"missing required env var {name}")
    return val


@dataclass(frozen=True)
class Config:
    reader: str
    plc_ip: str
    plc_rack: int
    plc_slot: int
    tags: list[Tag]
    poll_interval_s: float
    heartbeat_interval_s: float
    api_url: str
    ingest_api_key: str
    api_verify_tls: bool
    buffer_path: str
    flush_batch: int
    flush_interval_s: float
    max_backoff_s: float
    fake_rate_per_min: float
    fake_disconnect_prob: float

    @classmethod
    def from_env(cls) -> "Config":
        reader = _env("PLC_READER", "snap7").lower()
        if reader not in ("snap7", "fake"):
            raise ValueError("PLC_READER must be 'snap7' or 'fake'")
        return cls(
            reader=reader,
            plc_ip=_env("PLC_IP") if reader == "snap7" else os.environ.get("PLC_IP", ""),
            plc_rack=int(_env("PLC_RACK", "0")),
            plc_slot=int(_env("PLC_SLOT", "1")),
            tags=parse_tags(_env("TAGS", "plc_line_2:MK:0:10:DINT")),
            poll_interval_s=int(_env("POLL_INTERVAL_MS", "200")) / 1000,
            heartbeat_interval_s=float(_env("HEARTBEAT_INTERVAL_S", "60")),
            api_url=_env("API_URL").rstrip("/"),
            ingest_api_key=_env("INGEST_API_KEY"),
            api_verify_tls=_env("API_VERIFY_TLS", "true").lower() != "false",
            buffer_path=_env("BUFFER_PATH", "buffer.sqlite3"),
            flush_batch=int(_env("FLUSH_BATCH", "500")),
            flush_interval_s=float(_env("FLUSH_INTERVAL_S", "2")),
            max_backoff_s=float(_env("MAX_BACKOFF_S", "60")),
            fake_rate_per_min=float(_env("FAKE_RATE_PER_MIN", "24")),
            fake_disconnect_prob=float(_env("FAKE_DISCONNECT_PROB", "0")),
        )
