"""create counter_readings

Revision ID: 0001
Revises:
Create Date: 2026-11-19
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "counter_readings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),  # BIGSERIAL
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("value", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'plc_line_2'")),
        sa.Column("is_heartbeat", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("source", "ts", "is_heartbeat", name="uq_counter_readings_source_ts_hb"),
    )
    op.create_index("ix_counter_readings_ts", "counter_readings", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_counter_readings_ts", table_name="counter_readings")
    op.drop_table("counter_readings")
