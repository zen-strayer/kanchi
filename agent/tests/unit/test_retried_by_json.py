"""Tests that retried_by is stored/retrieved as a native list, not JSON text."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from database import TaskEventDB
from tests.base import DatabaseTestCase


class TestRetriedByJson(DatabaseTestCase):
    def test_retried_by_stored_as_list_not_string(self):
        """Writing a list to retried_by must persist as a list, not a JSON string."""
        event = self.create_task_event_db(task_id="task-1", event_type="task-started")
        event.retried_by = ["task-2", "task-3"]
        self.session.commit()
        self.session.expire(event)
        self.assertIsInstance(event.retried_by, list)
        self.assertEqual(event.retried_by, ["task-2", "task-3"])

    def test_retried_by_none_returns_empty_list_in_to_dict(self):
        """to_dict() must return [] when retried_by is NULL."""
        event = self.create_task_event_db(task_id="task-1", event_type="task-started")
        event.retried_by = None
        self.session.commit()
        d = event.to_dict()
        self.assertEqual(d["retried_by"], [])

    def test_retried_by_list_round_trips_through_to_dict(self):
        """to_dict() must return the same list that was stored."""
        event = self.create_task_event_db(task_id="task-1", event_type="task-started")
        event.retried_by = ["abc", "def"]
        self.session.commit()
        self.session.expire(event)
        d = event.to_dict()
        self.assertEqual(d["retried_by"], ["abc", "def"])

    def test_create_retry_relationship_stores_list(self):
        """create_retry_relationship must write a list to retried_by, not a JSON string."""
        from services.task_service import TaskService

        self.create_task_event_db(task_id="original", event_type="task-started")
        svc = TaskService(self.session)
        svc.create_retry_relationship("original", "retry-1")

        events = self.session.query(TaskEventDB).filter_by(task_id="original").all()
        for ev in events:
            self.assertIsInstance(ev.retried_by, list,
                msg=f"retried_by should be list, got {type(ev.retried_by)}: {ev.retried_by!r}")
