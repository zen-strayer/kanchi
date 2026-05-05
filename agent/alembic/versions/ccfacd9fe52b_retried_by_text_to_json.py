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
    pass


def downgrade() -> None:
    pass
