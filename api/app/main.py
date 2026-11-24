import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from .auth import require_app_key, require_ingest_key
from .config import Settings, get_settings
from .db import get_session
from .models import CounterReading
from .schemas import (
    Health, IngestBatch, IngestResult, Latest, Reading, ReadingsPage, StatsResponse, to_utc,
)
from .stats import compute_stats

log = logging.getLogger("api")
app = FastAPI(title="PLC Counter API", version="1.0.0")

SourceQ = Query(default=None, pattern=r"^[A-Za-z0-9_.\-]{1,64}$")


def _range(frm: datetime | None, to: datetime | None) -> tuple[datetime, datetime]:
    end = to_utc(to) if to else datetime.now(timezone.utc)
    start = to_utc(frm) if frm else end - timedelta(hours=24)
    if start >= end:
        raise HTTPException(422, "'from' must be before 'to'")
    return start, end


@app.get("/health", response_model=Health)
def health(session: Session = Depends(get_session)):
    try:
        session.execute(text("SELECT 1"))
        return Health(status="ok", db="ok")
    except Exception:
        log.exception("health check failed")
        return JSONResponse(status_code=503, content={"status": "error", "db": "error"})


@app.get("/readings/latest", response_model=Latest, dependencies=[Depends(require_app_key)])
def latest(
    source: str | None = SourceQ,
    session: Session = Depends(get_session),
    s: Settings = Depends(get_settings),
):
    src = source or s.default_source
    cr = CounterReading
    row = session.execute(
        select(cr.ts, cr.value).where(cr.source == src).order_by(cr.ts.desc()).limit(1)
    ).first()
    if row is None:
        return Latest(source=src, label=s.label_for(src), value=None, ts=None, last_change_ts=None,
                      age_seconds=None, status="no_data", stale_after_seconds=s.stale_after_s)
    ts, value = to_utc(row.ts), row.value
    # Last time the value actually changed = earliest row of the current run of equal values.
    last_diff = session.execute(
        select(func.max(cr.ts)).where(cr.source == src, cr.value != value)
    ).scalar()
    q = select(func.min(cr.ts)).where(cr.source == src, cr.value == value)
    if last_diff is not None:
        q = q.where(cr.ts > last_diff)
    last_change = session.execute(q).scalar()
    age = int((datetime.now(timezone.utc) - ts).total_seconds())
    return Latest(
        source=src, label=s.label_for(src), value=value, ts=ts, last_change_ts=last_change,
        age_seconds=age, status="stale" if age > s.stale_after_s else "ok",
        stale_after_seconds=s.stale_after_s,
    )


@app.get("/readings", response_model=ReadingsPage, dependencies=[Depends(require_app_key)])
def readings(
    frm: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    include_heartbeats: bool = False,
    source: str | None = SourceQ,
    session: Session = Depends(get_session),
    s: Settings = Depends(get_settings),
):
    start, end = _range(frm, to)
    cr = CounterReading
    q = select(cr).where(cr.source == (source or s.default_source), cr.ts >= start, cr.ts < end)
    if not include_heartbeats:
        q = q.where(cr.is_heartbeat.is_(False))
    rows = session.scalars(q.order_by(cr.ts.desc(), cr.id.desc()).offset(offset).limit(limit + 1)).all()
    has_more = len(rows) > limit
    return ReadingsPage(
        items=[Reading.model_validate(r, from_attributes=True) for r in rows[:limit]],
        limit=limit, offset=offset, next_offset=offset + limit if has_more else None,
    )


@app.get("/stats", response_model=StatsResponse, response_model_by_alias=True,
         dependencies=[Depends(require_app_key)])
def stats(
    frm: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    bucket: Literal["hour", "day", "shift"] = "hour",
    source: str | None = SourceQ,
    session: Session = Depends(get_session),
    s: Settings = Depends(get_settings),
):
    start, end = _range(frm, to)
    try:
        data = compute_stats(session, s, source or s.default_source, start, end, bucket)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    data["from_"] = data.pop("from")
    return StatsResponse(**data)


@app.post("/ingest", response_model=IngestResult, dependencies=[Depends(require_ingest_key)])
def ingest(
    batch: IngestBatch,
    session: Session = Depends(get_session),
    s: Settings = Depends(get_settings),
):
    if len(batch.readings) > s.ingest_max_batch:
        raise HTTPException(413, f"batch too large (max {s.ingest_max_batch})")
    rows = [
        {"ts": r.ts, "value": r.value, "is_heartbeat": r.is_heartbeat, "source": r.source or s.default_source}
        for r in batch.readings
    ]
    dialect = session.get_bind().dialect.name
    ins = (postgresql.insert if dialect == "postgresql" else sqlite.insert)(CounterReading)
    stmt = ins.values(rows).on_conflict_do_nothing(index_elements=["source", "ts", "is_heartbeat"])
    result = session.execute(stmt)
    session.commit()
    inserted = result.rowcount if result.rowcount is not None and result.rowcount >= 0 else len(rows)
    return IngestResult(received=len(rows), inserted=inserted)
