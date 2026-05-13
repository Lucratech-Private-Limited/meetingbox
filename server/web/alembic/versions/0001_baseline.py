"""Baseline — core schema created by init_database().

Revision ID: 0001_baseline
Revises:
Create Date: 2026-05-08

Subsequent revisions add columns/tables without re-running full DDL here.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
