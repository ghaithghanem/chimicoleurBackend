from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Text, UniqueConstraint, false, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# BIGSERIAL on Postgres; SQLite (tests) only autoincrements INTEGER primary keys.
BigId = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


class CounterReading(Base):
    __tablename__ = "counter_readings"
    __table_args__ = (
        # Makes collector re-sends idempotent (ON CONFLICT DO NOTHING).
        UniqueConstraint("source", "ts", "is_heartbeat", name="uq_counter_readings_source_ts_hb"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'plc_line_2'"))
    is_heartbeat: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=false())
