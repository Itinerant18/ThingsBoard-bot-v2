from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
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
        UniqueConstraint("tenant_id", "event_id", name="uq_event_tenant_event"),
        Index("ix_event_tenant_time", "tenant_id", "time"),
    )
    id: Mapped[UUID] = uuid_column()
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    customer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event_id: Mapped[str] = mapped_column(String(128))
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(64))
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    payload: Mapped[dict[str, object]] = mapped_column(JSON)


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
