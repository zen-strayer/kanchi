"""retried_by Text -> JSON in task_events and task_latest

Revision ID: ccfacd9fe52b
Revises: 930bf786783a
Create Date: 2026-04-29 11:24:12.807847

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ccfacd9fe52b"
down_revision: Union[str, None] = "930bf786783a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_BATCH_SIZE = 1_000


def upgrade() -> None:
    dialect = op.get_context().dialect.name

    if dialect == "sqlite":
        _upgrade_sqlite()
    else:
        _upgrade_postgresql()


def downgrade() -> None:
    dialect = op.get_context().dialect.name

    if dialect == "sqlite":
        _downgrade_sqlite()
    else:
        _downgrade_postgresql()


# ---------------------------------------------------------------------------
# SQLite paths (used by unit tests; recreates the table in-place)
# ---------------------------------------------------------------------------

def _upgrade_sqlite() -> None:
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.alter_column(
            "retried_by",
            type_=sa.JSON(),
            existing_type=sa.Text(),
        )
    with op.batch_alter_table("task_latest") as batch_op:
        batch_op.alter_column(
            "retried_by",
            type_=sa.JSON(),
            existing_type=sa.Text(),
        )


def _downgrade_sqlite() -> None:
    with op.batch_alter_table("task_latest") as batch_op:
        batch_op.alter_column(
            "retried_by",
            type_=sa.Text(),
            existing_type=sa.JSON(),
        )
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.alter_column(
            "retried_by",
            type_=sa.Text(),
            existing_type=sa.JSON(),
        )


# ---------------------------------------------------------------------------
# PostgreSQL paths (zero-downtime: add shadow column → batch backfill → swap)
# ---------------------------------------------------------------------------

def _upgrade_postgresql() -> None:
    pass  # Implemented in next task


def _downgrade_postgresql() -> None:
    pass  # Implemented in next task
