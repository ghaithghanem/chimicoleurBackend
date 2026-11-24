import os
from datetime import datetime, timezone

import pytest

# Must be set before the app reads settings. TEST_DATABASE_URL=postgresql+psycopg://... runs against Postgres.
DB_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ["DATABASE_URL"] = DB_URL
os.environ["APP_API_KEY"] = "test-app-key-0123456789"
os.environ["INGEST_API_KEY"] = "test-ingest-key-0123456789"
os.environ["HEARTBEAT_INTERVAL_S"] = "60"
os.environ["DISPLAY_TZ"] = "Africa/Tunis"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, delete  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, CounterReading  # noqa: E402

APP_H = {"X-API-Key": "test-app-key-0123456789"}
INGEST_H = {"X-API-Key": "test-ingest-key-0123456789"}

get_settings.cache_clear()
_engine = (
    create_engine(DB_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    if DB_URL.startswith("sqlite")
    else create_engine(DB_URL)
)
Base.metadata.create_all(_engine)


def utc(*a) -> datetime:
    return datetime(*a, tzinfo=timezone.utc)


@pytest.fixture
def session():
    with Session(_engine) as s:
        s.execute(delete(CounterReading))
        s.commit()
        yield s


@pytest.fixture
def client(session):
    def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def add(session):
    def _add(rows, source="plc_line_2"):
        for row in rows:
            ts, value, *hb = row
            session.add(CounterReading(ts=ts, value=value, source=source, is_heartbeat=bool(hb and hb[0])))
        session.commit()

    return _add
