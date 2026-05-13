"""Add user_commitments for tasks/reminders (SQLite assistant + Mem0 fallback).

Revision ID: 0003_user_commitments
Revises: 0002_audit_device
Create Date: 2026-05-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_user_commitments"
down_revision: Union[str, None] = "0002_audit_device"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "user_commitments" in insp.get_table_names():
        return
    op.create_table(
        "user_commitments",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("tags", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("remind_at", sa.Text(), nullable=True),
        sa.Column("due_at", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("calendar_event_id", sa.Text(), nullable=True),
        sa.Column("audit_id", sa.Text(), nullable=True),
        sa.Column("meeting_id", sa.Text(), nullable=True),
        sa.Column("mem0_synced", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
    )
    op.create_index("idx_user_commitments_user_status", "user_commitments", ["user_id", "status"])
    op.create_index("idx_user_commitments_remind", "user_commitments", ["user_id", "remind_at"])


def downgrade() -> None:
    op.drop_index("idx_user_commitments_remind", table_name="user_commitments")
    op.drop_index("idx_user_commitments_user_status", table_name="user_commitments")
    op.drop_table("user_commitments")
