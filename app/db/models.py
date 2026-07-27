from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def uuid_column() -> Mapped[UUID]:
    return mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)


class Customer(Base):
    __tablename__ = "customer"
    id: Mapped[UUID] = uuid_column()
    tb_customer_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    prefix: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DeviceEvent(Base):
    __tablename__ = "device_event"
    __table_args__ = (
        UniqueConstraint("tenant_id", "event_id", "time", name="uq_event_tenant_event_time"),
        Index("ix_event_tenant_time", "tenant_id", "time"),
    )
    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(128))
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    # Part of the PK: TimescaleDB requires the partition column in every unique index,
    # the primary key included.
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)


class DeviceTelemetry(Base):
    """Long-term history for EVERY device key pulled from ThingsBoard.

    Before this table existed, nothing polled was persisted: live sync fetched all
    128 devices every 60s and wrote only to Redis with a 15-minute TTL, so the
    history evaporated. device_event covered just 19 devices because it is fed
    solely by the rule-chain webhook.

    Long/narrow (EAV) rather than a column per key, because devices carry ~1150
    distinct keys and the set differs per model and firmware — a wide table would
    need a migration for every new key. This is also what makes the webhook payload
    queryable without parsing JSON.

    `time` is part of the unique constraint because TimescaleDB refuses to create a
    hypertable whose unique indexes omit the partition column.
    """

    __tablename__ = "device_telemetry"
    __table_args__ = (
        UniqueConstraint("device_id", "key", "time", name="uq_telemetry_device_key_time"),
        # Serves the dominant query: latest / point-in-time value for one key.
        Index("ix_telemetry_device_key_time", "device_id", "key", "time"),
        Index("ix_telemetry_customer_time", "customer_id", "time"),
    )
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(64))
    customer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    # Numeric when the value parses as one, so time_bucket()/avg() work without a
    # cast; the original always stays in value_text, including JSON for containers
    # and lists (rock.HddINFO, gatewayStatus, ...).
    value_num: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(Text)


class HierarchyNode(Base):
    __tablename__ = "hierarchy_node"
    node_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("hierarchy_node.node_id"))
    node_type: Mapped[str] = mapped_column(String(32))
    node_level: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(256))
    is_leaf: Mapped[bool]
    tb_device_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (Index("ix_hierarchy_customer_leaf", "customer_id", "is_leaf"),)


class BranchAncestorPath(Base):
    __tablename__ = "branch_ancestor_path"
    node_id: Mapped[str] = mapped_column(ForeignKey("hierarchy_node.node_id"), primary_key=True)
    ancestor_id: Mapped[str] = mapped_column(ForeignKey("hierarchy_node.node_id"), primary_key=True)
    depth: Mapped[int] = mapped_column(Integer)


class BranchIdentity(Base):
    __tablename__ = "branch_identity"
    id: Mapped[UUID] = uuid_column()
    customer_id: Mapped[str] = mapped_column(String(64), index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("hierarchy_node.node_id"))
    alias: Mapped[str] = mapped_column(String(255), index=True)
    normalized_alias: Mapped[str] = mapped_column(String(255), index=True)
    __table_args__ = (
        UniqueConstraint("customer_id", "normalized_alias", name="uq_identity_customer_alias"),
    )
