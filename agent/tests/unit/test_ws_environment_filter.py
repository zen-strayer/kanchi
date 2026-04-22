"""Tests for server-side WebSocket environment filtering."""

import os
import sys
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from connection_manager import ConnectionManager
from models import TaskEvent


def _make_event(queue: str = "default", hostname: str = "worker1@host") -> TaskEvent:
    return TaskEvent(
        task_id="t1",
        task_name="tasks.test",
        event_type="task-succeeded",
        timestamp=datetime.now(UTC),
        queue=queue,
        hostname=hostname,
    )


class TestConnectionManagerEnvironmentFilter(unittest.TestCase):
    def setUp(self):
        self.cm = ConnectionManager()
        self.ws = MagicMock()
        self.cm.active_connections.append(self.ws)
        self.cm.client_filters[self.ws] = {}
        self.cm.client_modes[self.ws] = "live"
        self.cm.client_environments[self.ws] = None

    def test_client_environments_dict_exists(self):
        """ConnectionManager must expose a client_environments dict."""
        cm = ConnectionManager()
        self.assertIsInstance(cm.client_environments, dict)

    def test_set_client_environment_stores_patterns(self):
        """set_client_environment must store queue and worker patterns for the client."""
        self.cm.set_client_environment(self.ws, ["prod-*"], ["worker-*.prod"])
        env = self.cm.client_environments[self.ws]
        self.assertEqual(env["queue_patterns"], ["prod-*"])
        self.assertEqual(env["worker_patterns"], ["worker-*.prod"])

    def test_no_environment_allows_all_events(self):
        """A client with no registered environment must receive all events."""
        self.cm.client_environments[self.ws] = None
        event = _make_event(queue="any-queue", hostname="any-worker")
        result = self.cm._matches_environment(event, None)
        self.assertTrue(result)

    def test_queue_pattern_matching_allows_matching_event(self):
        """Events whose queue matches the environment's queue_patterns must pass."""
        env = {"queue_patterns": ["prod-*"], "worker_patterns": []}
        event = _make_event(queue="prod-orders", hostname="worker1")
        self.assertTrue(self.cm._matches_environment(event, env))

    def test_queue_pattern_matching_blocks_non_matching_event(self):
        """Events whose queue does NOT match queue_patterns must be blocked."""
        env = {"queue_patterns": ["prod-*"], "worker_patterns": []}
        event = _make_event(queue="staging-orders", hostname="worker1")
        self.assertFalse(self.cm._matches_environment(event, env))

    def test_worker_pattern_matching_blocks_non_matching_hostname(self):
        """Events whose hostname does NOT match worker_patterns must be blocked."""
        env = {"queue_patterns": [], "worker_patterns": ["worker-*.prod"]}
        event = _make_event(queue="default", hostname="worker-1.staging")
        self.assertFalse(self.cm._matches_environment(event, env))
