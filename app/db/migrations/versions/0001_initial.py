"""initial tenant-safe ThingsBoard schema

Revision ID: 0001_initial
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "customer",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tb_customer_id", sa.String(64), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("prefix", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "hierarchy_node",
        sa.Column("node_id", sa.String(128), primary_key=True),
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column("parent_id", sa.String(128), sa.ForeignKey("hierarchy_node.node_id")),
        sa.Column("node_type", sa.String(32), nullable=False),
        sa.Column("node_level", sa.Integer, nullable=False),
        sa.Column("display_name", sa.String(256), nullable=False),
        sa.Column("is_leaf", sa.Boolean, nullable=False),
        sa.Column("tb_device_id", uuid),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "branch_ancestor_path",
        sa.Column(
            "node_id", sa.String(128), sa.ForeignKey("hierarchy_node.node_id"), primary_key=True
        ),
        sa.Column(
            "ancestor_id", sa.String(128), sa.ForeignKey("hierarchy_node.node_id"), primary_key=True
        ),
        sa.Column("depth", sa.Integer, nullable=False),
    )
    op.create_index("ix_hierarchy_customer_leaf", "hierarchy_node", ["customer_id", "is_leaf"])
    op.create_table(
        "branch_identity",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("customer_id", sa.String(64), nullable=False),
        sa.Column(
            "node_id", sa.String(128), sa.ForeignKey("hierarchy_node.node_id"), nullable=False
        ),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("normalized_alias", sa.String(255), nullable=False),
        sa.UniqueConstraint("customer_id", "normalized_alias", name="uq_identity_customer_alias"),
    )
    op.create_table(
        "device_event",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("customer_id", sa.String(64)),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.UniqueConstraint("tenant_id", "event_id", name="uq_event_tenant_event"),
    )
    op.create_index("ix_event_tenant_time", "device_event", ["tenant_id", "time"])
    op.create_index("ix_device_event_device_id", "device_event", ["device_id"])


def downgrade() -> None:
    op.drop_table("device_event")
    op.drop_table("branch_identity")
    op.drop_table("branch_ancestor_path")
    op.drop_table("hierarchy_node")
    op.drop_table("customer")
