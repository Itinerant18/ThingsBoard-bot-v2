"""device_telemetry hypertable + convert device_event to one.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-27

Why: nothing pulled from ThingsBoard was ever persisted. Live sync fetched all 128
devices every 60s and wrote to Redis with a 15-minute TTL; device_event held only
the 19 devices the rule-chain webhook pushes for. There was no telemetry history at
all, and device_event was a plain Postgres table because create_hypertable() rejects
a table whose unique indexes omit the partition column.

Ordering matters here:
  1. delete the epoch-dated row FIRST — a 1970 timestamp would create a chunk 56
     years adrift from every other one,
  2. swap device_event's unique constraint to include `time`,
  3. only then convert, with migrate_data since the table is not empty.

DEDUPE SEMANTICS CHANGE: device_event's idempotency key becomes
(tenant_id, event_id, time). EventParse stamps time=now() when a payload carries no
timestamp — which the production rule chain does not — so a redelivered message now
lands as a second row instead of being rejected. The consumer's Redis SETNX (24h)
remains the real guard; the direct-write webhook fallback loses its backstop. This is
the unavoidable cost of partitioning on time and is recorded here deliberately.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_telemetry_hypertables"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RETENTION_DAYS = 365
COMPRESS_AFTER_DAYS = 7


def _timescale_available() -> bool:
    """Timescale-specific DDL is skipped on plain Postgres (local dev, CI)."""
    return bool(
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"))
        .scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    timescale = _timescale_available()

    # 1. A single row carries a 1970 timestamp from a payload with no parseable ts.
    #    Left in place it becomes its own chunk, decades from the rest of the data.
    conn.execute(sa.text("DELETE FROM device_event WHERE time < '2000-01-01'"))

    # 2. Fold `time` into EVERY unique index. TimescaleDB rejects the conversion
    #    unless the partition column appears in the primary key as well — the PK was
    #    PRIMARY KEY (id), which alone would abort create_hypertable.
    op.drop_constraint("uq_event_tenant_event", "device_event", type_="unique")
    op.create_unique_constraint(
        "uq_event_tenant_event_time", "device_event", ["tenant_id", "event_id", "time"]
    )
    op.drop_constraint("device_event_pkey", "device_event", type_="primary")
    op.create_primary_key("device_event_pkey", "device_event", ["id", "time"])

    op.create_table(
        "device_telemetry",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=True),
        sa.Column("customer_id", sa.String(64), nullable=True),
        sa.Column("value_num", sa.Float, nullable=True),
        sa.Column("value_text", sa.Text, nullable=True),
        sa.UniqueConstraint("device_id", "key", "time", name="uq_telemetry_device_key_time"),
    )
    op.create_index(
        "ix_telemetry_device_key_time", "device_telemetry", ["device_id", "key", "time"]
    )
    op.create_index("ix_telemetry_customer_time", "device_telemetry", ["customer_id", "time"])

    if not timescale:
        return

    # 3. Convert. migrate_data moves the existing device_event rows into chunks.
    conn.execute(
        sa.text(
            "SELECT create_hypertable('device_telemetry', 'time', "
            "chunk_time_interval => INTERVAL '1 day', if_not_exists => TRUE)"
        )
    )
    conn.execute(
        sa.text(
            "SELECT create_hypertable('device_event', 'time', "
            "migrate_data => TRUE, if_not_exists => TRUE)"
        )
    )

    for table, segment_by in (("device_telemetry", "device_id, key"), ("device_event", "device_id")):
        conn.execute(
            sa.text(
                f"ALTER TABLE {table} SET (timescaledb.compress, "
                f"timescaledb.compress_segmentby = '{segment_by}')"
            )
        )
        conn.execute(
            sa.text(
                f"SELECT add_compression_policy('{table}', "
                f"INTERVAL '{COMPRESS_AFTER_DAYS} days', if_not_exists => TRUE)"
            )
        )
        conn.execute(
            sa.text(
                f"SELECT add_retention_policy('{table}', "
                f"INTERVAL '{RETENTION_DAYS} days', if_not_exists => TRUE)"
            )
        )


def downgrade() -> None:
    conn = op.get_bind()
    if _timescale_available():
        for table in ("device_telemetry", "device_event"):
            conn.execute(sa.text(f"SELECT remove_retention_policy('{table}', if_exists => TRUE)"))
            conn.execute(sa.text(f"SELECT remove_compression_policy('{table}', if_exists => TRUE)"))

    op.drop_index("ix_telemetry_customer_time", table_name="device_telemetry")
    op.drop_index("ix_telemetry_device_key_time", table_name="device_telemetry")
    op.drop_table("device_telemetry")

    # device_event stays a hypertable: reverting that would need a full table rewrite,
    # and the constraint swap below is what application code actually depends on.
    op.drop_constraint("uq_event_tenant_event_time", "device_event", type_="unique")
    op.create_unique_constraint(
        "uq_event_tenant_event", "device_event", ["tenant_id", "event_id"]
    )
    op.drop_constraint("device_event_pkey", "device_event", type_="primary")
    op.create_primary_key("device_event_pkey", "device_event", ["id"])
