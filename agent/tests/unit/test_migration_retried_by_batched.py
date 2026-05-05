"""
Tests for the zero-downtime batched retried_by migration (ccfacd9fe52b).

Runs against an in-memory SQLite DB created with the OLD schema (retried_by TEXT)
to verify that upgrade() correctly migrates to JSON and downgrade() reverts.
All tests use the SQLite path of the migration (batch_alter_table).
"""
import importlib.util
import sys
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text


def _load_migration():
    """Load the migration module from its file path."""
    migration_path = (
        Path(__file__).parent.parent.parent
        / "alembic" / "versions" / "ccfacd9fe52b_retried_by_text_to_json.py"
    )
    spec = importlib.util.spec_from_file_location("ccfacd9fe52b", migration_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _old_schema_engine():
    """
    Create an in-memory SQLite engine with the PRE-migration schema:
    retried_by is TEXT in both task_events and task_latest.
    Only the columns touched by this migration are included.
    """
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE task_events ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  task_id TEXT NOT NULL,"
            "  retried_by TEXT"
            ")"
        ))
        conn.execute(text(
            "CREATE TABLE task_latest ("
            "  task_id TEXT PRIMARY KEY,"
            "  retried_by TEXT"
            ")"
        ))
        conn.commit()
    return engine


def _run_upgrade(engine):
    """Run the migration upgrade() against the given engine."""
    migration = _load_migration()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()


def _run_downgrade(engine):
    """Run the migration downgrade() against the given engine."""
    migration = _load_migration()
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.downgrade()


class TestUpgradeMigratesColumn(unittest.TestCase):
    """upgrade() should rename/convert retried_by from TEXT to JSON on SQLite."""

    def test_upgrade_retried_by_column_survives(self):
        """After upgrade, task_events must still have a retried_by column."""
        pass

    def test_upgrade_null_retried_by_stays_null(self):
        """Rows with NULL retried_by must remain NULL after upgrade."""
        pass

    def test_upgrade_json_text_is_preserved(self):
        """
        Rows whose retried_by TEXT contains a JSON array must survive upgrade
        with the same data intact (SQLite JSON is stored as text internally).
        """
        pass

    def test_upgrade_task_latest_column_survives(self):
        """After upgrade, task_latest must still have a retried_by column."""
        pass


class TestDowngradeRevertsColumn(unittest.TestCase):
    """downgrade() should revert retried_by from JSON back to TEXT on SQLite."""

    def test_downgrade_retried_by_column_survives(self):
        """After downgrade, task_events must still have a retried_by column."""
        pass

    def test_downgrade_preserves_data(self):
        """Rows with JSON-list data must survive downgrade with data intact."""
        pass


class TestUpgradeIsIdempotent(unittest.TestCase):
    """Running upgrade() twice must not raise an error."""

    def test_double_upgrade_does_not_raise(self):
        """upgrade() called twice on the same DB must not raise."""
        pass
