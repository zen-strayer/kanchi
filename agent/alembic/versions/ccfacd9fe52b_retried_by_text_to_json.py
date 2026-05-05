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
    conn = op.get_bind()
    # Break out of Alembic's wrapping transaction so each statement auto-commits,
    # keeping ACCESS EXCLUSIVE lock windows to milliseconds per DDL statement.
    conn.execute(sa.text("COMMIT"))

    # ── task_events ──────────────────────────────────────────────────────────
    conn.execute(sa.text(
        "ALTER TABLE task_events ADD COLUMN IF NOT EXISTS retried_by_new JSON"
    ))

    # Backfill in batches using integer PK cursor to avoid long-running row locks
    while True:
        result = conn.execute(sa.text(
            "UPDATE task_events"
            " SET retried_by_new = retried_by::json"
            " WHERE id IN ("
            "   SELECT id FROM task_events"
            "   WHERE retried_by_new IS NULL AND retried_by IS NOT NULL"
            "   LIMIT :batch_size"
            " )"
        ), {"batch_size": _BATCH_SIZE})
        if result.rowcount == 0:
            break

    _pg_swap_column(conn, "task_events")

    # ── task_latest ──────────────────────────────────────────────────────────
    conn.execute(sa.text(
        "ALTER TABLE task_latest ADD COLUMN IF NOT EXISTS retried_by_new JSON"
    ))

    # Backfill using ctid because task_latest has a varchar PK
    while True:
        result = conn.execute(sa.text(
            "UPDATE task_latest"
            " SET retried_by_new = retried_by::json"
            " WHERE ctid IN ("
            "   SELECT ctid FROM task_latest"
            "   WHERE retried_by_new IS NULL AND retried_by IS NOT NULL"
            "   LIMIT :batch_size"
            " )"
        ), {"batch_size": _BATCH_SIZE})
        if result.rowcount == 0:
            break

    _pg_swap_column(conn, "task_latest")


def _pg_swap_column(conn, table: str) -> None:
    """
    Rename retried_by → retried_by_old, retried_by_new → retried_by,
    sync any rows written during the backfill window, then drop retried_by_old.

    Idempotent: checks information_schema before renaming.
    """
    already_swapped = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns"
        " WHERE table_name = :t AND column_name = 'retried_by_old'"
    ), {"t": table}).fetchone()

    if not already_swapped:
        # Wrap both renames in one transaction so the swap is atomic — no window
        # where retried_by is absent by name and concurrent writes can error.
        conn.execute(sa.text("BEGIN"))
        conn.execute(sa.text(f"ALTER TABLE {table} RENAME COLUMN retried_by TO retried_by_old"))
        conn.execute(sa.text(f"ALTER TABLE {table} RENAME COLUMN retried_by_new TO retried_by"))
        conn.execute(sa.text("COMMIT"))

    # Sync rows written to retried_by_old after backfill started
    conn.execute(sa.text(
        f"UPDATE {table}"
        f" SET retried_by = retried_by_old::json"
        f" WHERE retried_by IS NULL AND retried_by_old IS NOT NULL"
    ))

    conn.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS retried_by_old"))


def _downgrade_postgresql() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("COMMIT"))

    for table in ["task_latest", "task_events"]:
        conn.execute(sa.text(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS retried_by_old TEXT"
        ))

        # Backfill using ctid (works for both tables regardless of PK type)
        while True:
            result = conn.execute(sa.text(
                f"UPDATE {table}"
                f" SET retried_by_old = retried_by::text"
                f" WHERE ctid IN ("
                f"   SELECT ctid FROM {table}"
                f"   WHERE retried_by_old IS NULL AND retried_by IS NOT NULL"
                f"   LIMIT :batch_size"
                f" )"
            ), {"batch_size": _BATCH_SIZE})
            if result.rowcount == 0:
                break

        already_swapped = conn.execute(sa.text(
            "SELECT 1 FROM information_schema.columns"
            " WHERE table_name = :t AND column_name = 'retried_by_new'"
        ), {"t": table}).fetchone()

        if not already_swapped:
            conn.execute(sa.text("BEGIN"))
            conn.execute(sa.text(f"ALTER TABLE {table} RENAME COLUMN retried_by TO retried_by_new"))
            conn.execute(sa.text(f"ALTER TABLE {table} RENAME COLUMN retried_by_old TO retried_by"))
            conn.execute(sa.text("COMMIT"))

        conn.execute(sa.text(
            f"UPDATE {table}"
            f" SET retried_by = retried_by_new::text"
            f" WHERE retried_by IS NULL AND retried_by_new IS NOT NULL"
        ))

        conn.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS retried_by_new"))
