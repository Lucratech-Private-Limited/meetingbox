"""meetings.participants — store participant names for timeline/person search.

Revision ID: 0007_add_participants
Revises: 0006_mem0_soft_deleted
Create Date: 2026-06-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_add_participants"
down_revision: Union[str, None] = "0006_mem0_soft_deleted"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    columns = [c["name"] for c in insp.get_columns("meetings")]
    if "participants" not in columns:
        op.add_column("meetings", sa.Column("participants", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("meetings", "participants")
