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
        engine = _old_schema_engine()
        _run_upgrade(engine)
        inspector = inspect(engine)
        col_names = [c["name"] for c in inspector.get_columns("task_events")]
        self.assertIn("retried_by", col_names)
        col_types = {c["name"]: str(c["type"]) for c in inspector.get_columns("task_events")}
        self.assertEqual(col_types["retried_by"], "JSON")

    def test_upgrade_null_retried_by_stays_null(self):
        """Rows with NULL retried_by must remain NULL after upgrade."""
        engine = _old_schema_engine()
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO task_events (task_id, retried_by) VALUES ('t1', NULL)"))
            conn.commit()
        _run_upgrade(engine)
        with engine.connect() as conn:
            row = conn.execute(text("SELECT retried_by FROM task_events WHERE task_id = 't1'")).fetchone()
        self.assertIsNone(row[0])

    def test_upgrade_json_text_is_preserved(self):
        """
        Rows whose retried_by TEXT contains a JSON array must survive upgrade
        with the same data intact (SQLite JSON is stored as text internally).
        """
        engine = _old_schema_engine()
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO task_events (task_id, retried_by) VALUES ('t2', '[\"retry-1\",\"retry-2\"]')"
            ))
            conn.commit()
        _run_upgrade(engine)
        with engine.connect() as conn:
            row = conn.execute(text("SELECT retried_by FROM task_events WHERE task_id = 't2'")).fetchone()
        import json
        value = row[0] if isinstance(row[0], list) else json.loads(row[0])
        self.assertEqual(value, ["retry-1", "retry-2"])

    def test_upgrade_task_latest_column_survives(self):
        """After upgrade, task_latest must still have a retried_by column."""
        engine = _old_schema_engine()
        _run_upgrade(engine)
        inspector = inspect(engine)
        col_names = [c["name"] for c in inspector.get_columns("task_latest")]
        self.assertIn("retried_by", col_names)
        col_types = {c["name"]: str(c["type"]) for c in inspector.get_columns("task_latest")}
        self.assertEqual(col_types["retried_by"], "JSON")


class TestDowngradeRevertsColumn(unittest.TestCase):
    """downgrade() should revert retried_by from JSON back to TEXT on SQLite."""

    def test_downgrade_retried_by_column_survives(self):
        """After downgrade, task_events must still have a retried_by column."""
        engine = _old_schema_engine()
        _run_upgrade(engine)
        _run_downgrade(engine)
        inspector = inspect(engine)
        col_names = [c["name"] for c in inspector.get_columns("task_events")]
        self.assertIn("retried_by", col_names)
        col_types = {c["name"]: str(c["type"]) for c in inspector.get_columns("task_events")}
        self.assertEqual(col_types["retried_by"], "TEXT")

    def test_downgrade_preserves_data(self):
        """Rows with JSON-list data must survive downgrade with data intact."""
        engine = _old_schema_engine()
        with engine.connect() as conn:
            conn.execute(text(
                "INSERT INTO task_events (task_id, retried_by) VALUES ('t3', '[\"a\",\"b\"]')"
            ))
            conn.commit()
        _run_upgrade(engine)
        _run_downgrade(engine)
        with engine.connect() as conn:
            row = conn.execute(text("SELECT retried_by FROM task_events WHERE task_id = 't3'")).fetchone()
        import json
        value = row[0] if isinstance(row[0], list) else json.loads(row[0])
        self.assertEqual(value, ["a", "b"])


class TestUpgradeIsIdempotent(unittest.TestCase):
    """Running upgrade() twice must not raise an error."""

    def test_double_upgrade_does_not_raise(self):
        """upgrade() called twice on the same DB must not raise."""
        engine = _old_schema_engine()
        _run_upgrade(engine)
        try:
            _run_upgrade(engine)
        except Exception as exc:
            self.fail(f"Second upgrade() raised unexpectedly: {exc}")
