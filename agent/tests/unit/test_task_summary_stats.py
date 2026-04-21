import unittest
from datetime import datetime, timezone, timedelta

from services.task_service import TaskService
from tests.base import DatabaseTestCase


class TestGetTaskSummaryStats(DatabaseTestCase):

    def setUp(self):
        super().setUp()
        self.service = TaskService(self.session)
        self.now = datetime.now(timezone.utc)

    def test_recent_activity_counts_events_within_last_hour(self):
        # Event 30 minutes ago — should be counted
        self.create_task_event_db(
            task_id="recent-1",
            event_type="task-started",
            timestamp=self.now - timedelta(minutes=30),
        )
        # Event 2 hours ago — should NOT be counted
        self.create_task_event_db(
            task_id="old-1",
            event_type="task-started",
            timestamp=self.now - timedelta(hours=2),
        )

        result = self.service.get_task_summary_stats()

        self.assertEqual(result['recent_activity'], 1)

    def test_recent_activity_returns_zero_when_no_events(self):
        result = self.service.get_task_summary_stats()

        self.assertEqual(result['recent_activity'], 0)

    def test_recent_activity_counts_all_events_within_last_hour(self):
        for i in range(5):
            self.create_task_event_db(
                task_id=f"task-{i}",
                event_type="task-started",
                timestamp=self.now - timedelta(minutes=10),
            )

        result = self.service.get_task_summary_stats()

        self.assertEqual(result['recent_activity'], 5)
