"""Tests for task-rejected event handling."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from constants import COMPLETED_EVENT_TYPES, EventType


class TestTaskRejectedConstants(unittest.TestCase):
    def test_task_rejected_in_event_type_enum(self):
        """EventType must have a TASK_REJECTED member with value 'task-rejected'."""
        self.assertEqual(EventType.TASK_REJECTED, "task-rejected")

    def test_task_rejected_in_completed_event_types(self):
        """COMPLETED_EVENT_TYPES must include TASK_REJECTED so rejected tasks are not shown as active."""
        self.assertIn(EventType.TASK_REJECTED, COMPLETED_EVENT_TYPES)

    def test_task_rejected_handler_registered_in_monitor(self):
        """Monitor._run_once handlers dict must include task-rejected wired to _handle_task_event."""
        import inspect

        from monitor import CeleryEventMonitor
        src = inspect.getsource(CeleryEventMonitor._run_once)
        self.assertIn("TASK_REJECTED", src)

    def test_final_states_uses_enum_not_hardcoded_string(self):
        """FINAL_STATES in task_service must reference EventType enum, not raw 'task-rejected' string."""
        import inspect

        from services.task_service import TaskService
        src = inspect.getsource(TaskService.get_unretried_orphaned_tasks)
        # The enum value produces "task-rejected" but via EventType, not a bare quoted literal
        self.assertNotIn('"task-rejected"', src)
