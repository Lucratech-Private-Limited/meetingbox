"""mem0_sqlite_ingest_log — audit trail for Mem0 payloads sourced from SQLite.

Revision ID: 0004_mem0_ingest_log
Revises: 0003_user_commitments
Create Date: 2026-05-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_mem0_ingest_log"
down_revision: Union[str, None] = "0003_user_commitments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "mem0_sqlite_ingest_log" in insp.get_table_names():
        return
    op.create_table(
        "mem0_sqlite_ingest_log",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("ref_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_mem0_sqlite_ingest_user_created",
        "mem0_sqlite_ingest_log",
        ["user_id", "created_at"],
    )
    op.create_index(
        "idx_mem0_sqlite_ingest_kind_ref",
        "mem0_sqlite_ingest_log",
        ["kind", "ref_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_mem0_sqlite_ingest_kind_ref", table_name="mem0_sqlite_ingest_log")
    op.drop_index("idx_mem0_sqlite_ingest_user_created", table_name="mem0_sqlite_ingest_log")
    op.drop_table("mem0_sqlite_ingest_log")
