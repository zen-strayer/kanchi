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


def upgrade() -> None:
    # batch_alter_table is required for SQLite structural changes (table recreation)
    # postgresql_using provides the USING clause for PostgreSQL type coercion
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.alter_column(
            "retried_by",
            type_=sa.JSON(),
            existing_type=sa.Text(),
            postgresql_using="retried_by::json",
        )
    with op.batch_alter_table("task_latest") as batch_op:
        batch_op.alter_column(
            "retried_by",
            type_=sa.JSON(),
            existing_type=sa.Text(),
            postgresql_using="retried_by::json",
        )


def downgrade() -> None:
    with op.batch_alter_table("task_latest") as batch_op:
        batch_op.alter_column(
            "retried_by",
            type_=sa.Text(),
            existing_type=sa.JSON(),
            postgresql_using="retried_by::text",
        )
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.alter_column(
            "retried_by",
            type_=sa.Text(),
            existing_type=sa.JSON(),
            postgresql_using="retried_by::text",
        )
