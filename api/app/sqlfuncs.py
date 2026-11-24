"""Dialect-specific SQL helpers (PostgreSQL in prod, SQLite in tests)."""

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import BigInteger, Float


class epoch(FunctionElement):
    """Seconds since 1970-01-01 UTC for a timestamp column."""

    type = Float()
    inherit_cache = True
    name = "epoch"


@compiles(epoch, "postgresql")
def _epoch_pg(el, compiler, **kw):
    return "EXTRACT(EPOCH FROM %s)" % compiler.process(list(el.clauses)[0], **kw)


@compiles(epoch, "sqlite")
def _epoch_sqlite(el, compiler, **kw):
    return "ROUND((julianday(%s) - 2440587.5) * 86400.0, 3)" % compiler.process(list(el.clauses)[0], **kw)


class ifloor(FunctionElement):
    """floor(x) as an integer."""

    type = BigInteger()
    inherit_cache = True
    name = "ifloor"


@compiles(ifloor, "postgresql")
def _ifloor_pg(el, compiler, **kw):
    return "CAST(FLOOR(%s) AS BIGINT)" % compiler.process(list(el.clauses)[0], **kw)


@compiles(ifloor, "sqlite")
def _ifloor_sqlite(el, compiler, **kw):
    x = compiler.process(list(el.clauses)[0], **kw)
    return f"(CAST({x} AS INTEGER) - (({x}) < CAST({x} AS INTEGER)))"
