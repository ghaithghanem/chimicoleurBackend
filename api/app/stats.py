"""Production statistics from a monotonically increasing (but resettable) PLC counter.

Per consecutive pair of rows (prev -> cur), ordered by ts:
  production += cur - prev            if cur >= prev
             += max(cur, 0)           if cur < prev  (counter reset: restarted from 0; never negative)
  gap = cur.ts - prev.ts
  if gap <= stale_after (collector was alive):
      value unchanged -> stop seconds, value changed -> run seconds
  else -> no data (collector/PLC link down)
Everything is attributed to the bucket containing cur.ts. Since alive gaps are at most
2x the heartbeat interval, the error at bucket edges is at most ~2 minutes.
Buckets are aligned in local time (fixed UTC offset, see Settings.tz_offset_s).
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import and_, case, func, literal_column, select, union_all
from sqlalchemy.orm import Session

from .config import Settings
from .models import CounterReading
from .sqlfuncs import epoch, ifloor

BucketKind = Literal["hour", "day", "shift"]


@dataclass(frozen=True)
class BucketGrid:
    length_s: int
    anchor_s: int  # local seconds-of-day where buckets start
    offset_s: int  # local = utc + offset

    def key_of(self, ts: datetime) -> int:
        return math.floor((ts.timestamp() + self.offset_s - self.anchor_s) / self.length_s)

    def start_of(self, key: int) -> datetime:
        return datetime.fromtimestamp(key * self.length_s + self.anchor_s - self.offset_s, tz=timezone.utc)


def grid_for(kind: BucketKind, settings: Settings) -> BucketGrid:
    off = settings.tz_offset_s
    if kind == "hour":
        return BucketGrid(3600, 0, off)
    if kind == "day":
        return BucketGrid(86400, 0, off)
    n = len(settings.shifts)
    if n == 0 or 86400 % n:
        raise ValueError("SHIFT_NAMES must split 24 h evenly")
    return BucketGrid(86400 // n, settings.shift_anchor_s % (86400 // n), off)


def shift_name(grid: BucketGrid, key: int, settings: Settings) -> str:
    local_tod = (key * grid.length_s + grid.anchor_s) % 86400
    idx = ((local_tod - settings.shift_anchor_s) % 86400) // grid.length_s
    return settings.shifts[idx]


def _lit(n: int | float):
    # Inline numeric literals so SELECT and GROUP BY expressions are textually identical (Postgres).
    return literal_column(repr(n) if isinstance(n, float) else str(int(n)))


def aggregate(session: Session, source: str, start: datetime, end: datetime, grid: BucketGrid,
              stale_after_s: int) -> dict[int, tuple[int, float, float]]:
    """Return {bucket_key: (production, stop_s, run_s)} computed in SQL."""
    cr = CounterReading
    base_sq = (
        select(cr.ts, cr.value)
        .where(cr.source == source, cr.ts < start)
        .order_by(cr.ts.desc())
        .limit(1)
        .subquery("b")
    )
    win = select(cr.ts, cr.value).where(cr.source == source, cr.ts >= start, cr.ts < end)
    u = union_all(select(base_sq.c.ts, base_sq.c.value), win).subquery("u")
    p = select(
        u.c.ts,
        u.c.value,
        func.lag(u.c.value).over(order_by=u.c.ts).label("pv"),
        func.lag(u.c.ts).over(order_by=u.c.ts).label("pts"),
    ).subquery("p")

    gap = epoch(p.c.ts) - epoch(p.c.pts)
    alive = and_(p.c.pv.is_not(None), gap <= _lit(stale_after_s))
    delta = case(
        (p.c.pv.is_(None), 0),
        (p.c.value >= p.c.pv, p.c.value - p.c.pv),
        (p.c.value > 0, p.c.value),
        else_=0,
    )
    stop = case((and_(alive, p.c.value == p.c.pv), gap), else_=0)
    run = case((and_(alive, p.c.value != p.c.pv), gap), else_=0)
    key = ifloor((epoch(p.c.ts) + _lit(grid.offset_s - grid.anchor_s)) / _lit(float(grid.length_s)))

    q = (
        select(key.label("k"), func.sum(delta), func.sum(stop), func.sum(run))
        .where(p.c.ts >= start)
        .group_by(key)
    )
    return {int(k): (int(d or 0), float(s or 0), float(r or 0)) for k, d, s, r in session.execute(q)}


def _overlap_s(a0: datetime, a1: datetime, b0: datetime, b1: datetime) -> float:
    return max(0.0, (min(a1, b1) - max(a0, b0)).total_seconds())


def _metrics(production: int, run_s: float, stop_s: float, observed_s: float) -> dict:
    covered = run_s + stop_s
    if covered > observed_s > 0:  # edge attribution can overshoot slightly
        run_s, stop_s = run_s * observed_s / covered, stop_s * observed_s / covered
        covered = observed_s
    return {
        "production": production,
        "run_seconds": round(run_s),
        "stop_seconds": round(stop_s),
        "no_data_seconds": round(max(0.0, observed_s - covered)),
        "uptime": round(run_s / covered, 4) if covered > 0 else None,
        "cadence_per_min": round(production / (run_s / 60), 2) if run_s >= 60 else None,
        # Not available from a single output counter yet (add PLC tags later).
        "input": None,
        "waste": None,
        "defects": None,
        "efficiency": None,
        "quality": None,
    }


def compute_stats(session: Session, settings: Settings, source: str, start: datetime, end: datetime,
                  kind: BucketKind, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    grid = grid_for(kind, settings)
    k0 = grid.key_of(start)
    k1 = grid.key_of(end - timedelta(microseconds=1))
    if k1 - k0 + 1 > settings.max_buckets:
        raise ValueError(f"too many buckets ({k1 - k0 + 1} > {settings.max_buckets})")

    agg = aggregate(session, source, start, end, grid, settings.stale_after_s)
    observed_end = min(end, now)

    items = []
    tot_p = tot_run = tot_stop = tot_obs = 0.0
    for k in range(k0, k1 + 1):
        b0, b1 = grid.start_of(k), grid.start_of(k + 1)
        prod, stop_s, run_s = agg.get(k, (0, 0.0, 0.0))
        obs = _overlap_s(b0, b1, start, observed_end)
        m = _metrics(prod, run_s, stop_s, obs)
        items.append({"start": b0, "end": b1, "shift": shift_name(grid, k, settings) if kind == "shift" else None, **m})
        tot_p += prod
        tot_run += m["run_seconds"]
        tot_stop += m["stop_seconds"]
        tot_obs += obs

    return {
        "source": source,
        "label": settings.label_for(source),
        "bucket": kind,
        "tz": settings.display_tz,
        "from": start,
        "to": end,
        "items": items,
        "totals": _metrics(int(tot_p), tot_run, tot_stop, tot_obs),
    }
