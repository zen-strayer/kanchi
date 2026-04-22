"""Tests for WorkflowEngine bounded ThreadPoolExecutor behavior."""

import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.workflow_engine import WorkflowEngine


class TestWorkflowEngineThreadPool(unittest.TestCase):

    def setUp(self):
        self.db_manager = MagicMock()

    def test_engine_has_executor_attribute(self):
        """WorkflowEngine must expose a ThreadPoolExecutor, not spawn bare threads."""
        engine = WorkflowEngine(self.db_manager)
        self.assertIsInstance(engine._executor, ThreadPoolExecutor)

    def test_default_max_workers_is_ten(self):
        """Default pool size must be 10."""
        engine = WorkflowEngine(self.db_manager)
        self.assertEqual(engine._executor._max_workers, 10)

    def test_custom_max_workers_is_respected(self):
        """Constructor kwarg max_workers must override the default."""
        engine = WorkflowEngine(self.db_manager, max_workers=3)
        self.assertEqual(engine._executor._max_workers, 3)

    def test_process_event_submits_to_executor_not_thread(self):
        """process_event must call executor.submit, never threading.Thread()."""
        engine = WorkflowEngine(self.db_manager)

        mock_event = MagicMock()
        mock_event.event_type = "task-succeeded"
        mock_event.model_dump.return_value = {}

        with patch.object(engine._executor, "submit") as mock_submit, \
             patch("services.workflow_engine.EVENT_TRIGGER_MAP", {"task-succeeded": "task.succeeded"}):
            engine.process_event(mock_event)
            mock_submit.assert_called_once()

    def test_process_event_noop_for_unknown_event_type(self):
        """process_event must not submit work for event types not in EVENT_TRIGGER_MAP."""
        engine = WorkflowEngine(self.db_manager)

        mock_event = MagicMock()
        mock_event.event_type = "unknown-event-xyz"

        with patch.object(engine._executor, "submit") as mock_submit, \
             patch("services.workflow_engine.EVENT_TRIGGER_MAP", {}):
            engine.process_event(mock_event)
            mock_submit.assert_not_called()

    def test_shutdown_is_callable(self):
        """shutdown() must exist and complete without error."""
        engine = WorkflowEngine(self.db_manager)
        engine.shutdown()  # must not raise

    def test_shutdown_prevents_new_submissions(self):
        """After shutdown(), process_event must not raise — RuntimeError is caught and logged."""
        engine = WorkflowEngine(self.db_manager)
        engine.shutdown()
        mock_event = MagicMock()
        mock_event.event_type = "task-succeeded"
        mock_event.model_dump.return_value = {}
        with patch("services.workflow_engine.EVENT_TRIGGER_MAP", {"task-succeeded": "task.succeeded"}):
            with self.assertLogs("services.workflow_engine", level="ERROR") as cm:
                engine.process_event(mock_event)
            self.assertTrue(any("Error processing" in line for line in cm.output))


class TestWorkflowEngineSingleExecutorInstance(unittest.TestCase):
    """The executor must be per-instance, not a global singleton."""

    def test_two_engines_have_independent_executors(self):
        db = MagicMock()
        engine_a = WorkflowEngine(db)
        engine_b = WorkflowEngine(db)
        self.assertIsNot(engine_a._executor, engine_b._executor)
        engine_a.shutdown()
        engine_b.shutdown()
