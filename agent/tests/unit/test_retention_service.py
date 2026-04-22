"""Tests for the data retention pruning service."""

import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from database import TaskEventDB, TaskProgressDB, WorkflowExecutionDB
from services.retention_service import RetentionService
from tests.base import DatabaseTestCase


class TestRetentionService(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        self.service = RetentionService(self.session)

    _task_counter = 0

    def _create_old_task_event(self, days_old: int) -> TaskEventDB:
        TestRetentionService._task_counter += 1
        return self.create_task_event_db(
            task_id=f"retention-task-{TestRetentionService._task_counter}",
            timestamp=datetime.now(UTC) - timedelta(days=days_old),
            event_type="task-succeeded",
        )

    def _create_old_progress_event(self, task_id: str, days_old: int) -> TaskProgressDB:
        event = TaskProgressDB(
            task_id=task_id,
            task_name="tasks.test",
            progress=100.0,
            timestamp=datetime.now(UTC) - timedelta(days=days_old),
        )
        self.session.add(event)
        self.session.commit()
        return event

    def _create_old_workflow_execution(self, days_old: int) -> WorkflowExecutionDB:
        from database import WorkflowDB

        wf = WorkflowDB(
            id="wf-retention-test",
            name="test-workflow",
            enabled=True,
            trigger_type="task.failed",
            trigger_config={},
            actions=[],
            priority=100,
        )
        if not self.session.query(WorkflowDB).filter_by(id="wf-retention-test").first():
            self.session.add(wf)
            self.session.commit()
        execution = WorkflowExecutionDB(
            workflow_id="wf-retention-test",
            trigger_type="task.failed",
            trigger_event={},
            status="completed",
            triggered_at=datetime.now(UTC) - timedelta(days=days_old),
        )
        self.session.add(execution)
        self.session.commit()
        return execution

    def test_prune_deletes_old_task_events(self):
        """task_events older than retention_days must be deleted."""
        old = self._create_old_task_event(days_old=40)
        recent = self._create_old_task_event(days_old=5)
        # Capture IDs before pruning — the deleted object will be expired after commit.
        old_id = old.task_id
        recent_id = recent.task_id

        counts = self.service.prune(retention_days=30)

        self.assertEqual(counts["task_events"], 1)
        remaining = self.session.query(TaskEventDB).all()
        ids = [e.task_id for e in remaining]
        self.assertNotIn(old_id, ids)
        self.assertIn(recent_id, ids)

    def test_prune_deletes_old_progress_events(self):
        """task_progress_events older than retention_days must be deleted."""
        old_task = self._create_old_task_event(days_old=5)
        self._create_old_progress_event(old_task.task_id, days_old=40)
        self._create_old_progress_event(old_task.task_id, days_old=5)

        counts = self.service.prune(retention_days=30)

        self.assertEqual(counts["task_progress_events"], 1)
        remaining = self.session.query(TaskProgressDB).all()
        self.assertEqual(len(remaining), 1)

    def test_prune_deletes_old_workflow_executions(self):
        """workflow_executions older than retention_days must be deleted."""
        self._create_old_workflow_execution(days_old=40)
        self._create_old_workflow_execution(days_old=5)

        counts = self.service.prune(retention_days=30)

        self.assertEqual(counts["workflow_executions"], 1)
        remaining = self.session.query(WorkflowExecutionDB).all()
        self.assertEqual(len(remaining), 1)

    def test_prune_returns_deletion_counts(self):
        """prune() must return a dict with counts for each table."""
        counts = self.service.prune(retention_days=30)

        self.assertIn("task_events", counts)
        self.assertIn("task_progress_events", counts)
        self.assertIn("workflow_executions", counts)

    def test_prune_with_no_old_data_returns_zero_counts(self):
        """prune() on a database with only recent data must return all zeros."""
        self._create_old_task_event(days_old=5)

        counts = self.service.prune(retention_days=30)

        self.assertEqual(counts["task_events"], 0)
        self.assertEqual(counts["task_progress_events"], 0)
        self.assertEqual(counts["workflow_executions"], 0)

    def test_recent_data_survives_pruning(self):
        """Events within the retention window must not be deleted."""
        recent = self._create_old_task_event(days_old=1)

        self.service.prune(retention_days=30)

        surviving = self.session.query(TaskEventDB).filter_by(task_id=recent.task_id).first()
        self.assertIsNotNone(surviving)
