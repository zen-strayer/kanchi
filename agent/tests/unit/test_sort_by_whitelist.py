"""Tests for sort_by column whitelist in TaskService."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.task_service import ALLOWED_SORT_COLUMNS, TaskService
from tests.base import DatabaseTestCase


class TestSortByWhitelist(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.task_service = TaskService(self.session)

    def test_allowed_sort_columns_constant_exists(self):
        """ALLOWED_SORT_COLUMNS must be a non-empty set of strings."""
        self.assertIsInstance(ALLOWED_SORT_COLUMNS, (set, frozenset))
        self.assertTrue(len(ALLOWED_SORT_COLUMNS) > 0)
        for col in ALLOWED_SORT_COLUMNS:
            self.assertIsInstance(col, str)

    def test_timestamp_is_allowed(self):
        """'timestamp' must be an allowed sort column."""
        self.assertIn("timestamp", ALLOWED_SORT_COLUMNS)

    def test_invalid_sort_column_raises_value_error(self):
        """_apply_sorting must raise ValueError for an unlisted column name."""
        from database import TaskEventDB

        query = self.session.query(TaskEventDB)
        with self.assertRaises(ValueError) as ctx:
            self.task_service._apply_sorting(query, "password_hash", "asc")
        self.assertIn("password_hash", str(ctx.exception))

    def test_none_sort_by_does_not_raise(self):
        """sort_by=None must fall through to default timestamp ordering."""
        from database import TaskEventDB

        query = self.session.query(TaskEventDB)
        result = self.task_service._apply_sorting(query, None, "desc")
        self.assertIsNotNone(result)

    def test_valid_sort_column_does_not_raise(self):
        """Every column in ALLOWED_SORT_COLUMNS must be accepted without error."""
        from database import TaskEventDB

        for col in ALLOWED_SORT_COLUMNS:
            query = self.session.query(TaskEventDB)
            result = self.task_service._apply_sorting(query, col, "asc")
            self.assertIsNotNone(result, f"Column '{col}' raised unexpectedly")
