"""Add device_id and correlation_id to assistant_audits.

Revision ID: 0002_audit_device
Revises: 0001_baseline
Create Date: 2026-05-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_audit_device"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c["name"] for c in insp.get_columns("assistant_audits")}
    if "device_id" not in cols:
        op.add_column("assistant_audits", sa.Column("device_id", sa.Text(), nullable=True))
    if "correlation_id" not in cols:
        op.add_column("assistant_audits", sa.Column("correlation_id", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assistant_audits") as batch:
        try:
            batch.drop_column("correlation_id")
        except Exception:
            pass
        try:
            batch.drop_column("device_id")
        except Exception:
            pass
