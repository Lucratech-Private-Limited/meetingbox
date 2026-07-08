"""known_contacts — auto-learned email address book from Gmail interactions.

Revision ID: 0005_known_contacts
Revises: 0004_mem0_ingest_log
Create Date: 2026-05-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_known_contacts"
down_revision: Union[str, None] = "0004_mem0_ingest_log"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "known_contacts" in insp.get_table_names():
        return
    op.create_table(
        "known_contacts",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("last_seen", sa.Text(), nullable=False),
        sa.Column("interaction_count", sa.Integer(), nullable=False, server_default="1"),
        sa.PrimaryKeyConstraint("user_id", "email"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
    )
    op.create_index(
        "idx_known_contacts_user_name",
        "known_contacts",
        ["user_id", "name"],
    )


def downgrade() -> None:
    op.drop_index("idx_known_contacts_user_name", table_name="known_contacts")
    op.drop_table("known_contacts")
