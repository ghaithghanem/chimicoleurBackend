from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, field_validator


def to_utc(dt: datetime) -> datetime:
    """Naive datetimes (SQLite in tests) are treated as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


# Serialized by pydantic as ISO-8601 with "Z".
UtcDatetime = Annotated[datetime, AfterValidator(to_utc)]


class Health(BaseModel):
    status: Literal["ok", "error"]
    db: Literal["ok", "error"]


class Latest(BaseModel):
    source: str
    label: str
    value: int | None
    ts: UtcDatetime | None
    last_change_ts: UtcDatetime | None
    age_seconds: int | None
    status: Literal["ok", "stale", "no_data"]
    stale_after_seconds: int


class Reading(BaseModel):
    id: int
    ts: UtcDatetime
    value: int
    source: str
    is_heartbeat: bool


class ReadingsPage(BaseModel):
    items: list[Reading]
    limit: int
    offset: int
    next_offset: int | None


class StatsMetrics(BaseModel):
    production: int
    run_seconds: int
    stop_seconds: int
    no_data_seconds: int
    uptime: float | None
    cadence_per_min: float | None
    input: int | None
    waste: int | None
    defects: int | None
    efficiency: float | None
    quality: float | None


class StatsItem(StatsMetrics):
    start: UtcDatetime
    end: UtcDatetime
    shift: str | None


class StatsResponse(BaseModel):
    source: str
    label: str
    bucket: Literal["hour", "day", "shift"]
    tz: str
    from_: UtcDatetime = Field(serialization_alias="from")
    to: UtcDatetime
    items: list[StatsItem]
    totals: StatsMetrics


class IngestReading(BaseModel):
    ts: datetime
    value: int = Field(ge=-(2**63), le=2**63 - 1)
    is_heartbeat: bool = False
    source: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.\-]{1,64}$")

    @field_validator("ts")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("ts must include a timezone (UTC recommended)")
        return v.astimezone(timezone.utc)


class IngestBatch(BaseModel):
    readings: list[IngestReading] = Field(min_length=1)


class IngestResult(BaseModel):
    received: int
    inserted: int
