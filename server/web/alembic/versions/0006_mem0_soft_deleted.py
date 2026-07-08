"""mem0_soft_deleted — soft-delete tracking table for self-hosted Mem0 memories.

Revision ID: 0006_mem0_soft_deleted
Revises: 0005_known_contacts
Create Date: 2026-05-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_mem0_soft_deleted"
down_revision: Union[str, None] = "0005_known_contacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "mem0_soft_deleted" in insp.get_table_names():
        return
    op.create_table(
        "mem0_soft_deleted",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("memory_id", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("deleted_at", sa.Text(), nullable=False),
        sa.Column("deleted_by", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_mem0_soft_deleted_user",
        "mem0_soft_deleted",
        ["user_id"],
    )
    op.create_index(
        "idx_mem0_soft_deleted_mem_user",
        "mem0_soft_deleted",
        ["memory_id", "user_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_mem0_soft_deleted_mem_user", table_name="mem0_soft_deleted")
    op.drop_index("idx_mem0_soft_deleted_user", table_name="mem0_soft_deleted")
    op.drop_table("mem0_soft_deleted")
