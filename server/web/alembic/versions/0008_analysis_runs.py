"""analysis_runs — idempotency table for APScheduler background analysis jobs.

Revision ID: 0008_analysis_runs
Revises: 0007_add_participants
Create Date: 2026-06-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_analysis_runs"
down_revision: Union[str, None] = "0007_add_participants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "analysis_runs" in insp.get_table_names():
        return
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("job_type", sa.Text(), nullable=False),
        sa.Column("run_date", sa.Text(), nullable=False),  # ISO date YYYY-MM-DD
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("completed_at", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_analysis_runs_user_job_date",
        "analysis_runs",
        ["user_id", "job_type", "run_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_analysis_runs_user_job_date", table_name="analysis_runs")
    op.drop_table("analysis_runs")
