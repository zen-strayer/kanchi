"""Tests for server-side WebSocket environment filtering."""

import asyncio
import json
import os
import sys
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_app_state_with_real_cm(env_patterns=None):
    """Build an app_state with a real ConnectionManager so _matches_environment fires."""
    ws = MagicMock()
    cm = ConnectionManager()
    cm.active_connections.append(ws)
    cm.client_filters[ws] = {}
    cm.client_modes[ws] = "live"
    cm.client_environments[ws] = env_patterns
    cm.send_personal_message = AsyncMock()

    app_state = MagicMock()
    app_state.connection_manager = cm

    session_ctx = MagicMock()
    session_ctx.__enter__ = MagicMock(return_value=MagicMock())
    session_ctx.__exit__ = MagicMock(return_value=False)
    app_state.db_manager.get_session.return_value = session_ctx

    return app_state, ws


class TestReplayPathsRespectEnvironmentFilter(unittest.TestCase):
    """get_stored and set_mode static replay must apply env filtering."""

    def test_get_stored_impl_filters_out_non_matching_env_events(self):
        """_handle_get_stored_impl must not send events that don't match client env."""
        import api.websocket_routes as ws_module

        env = {"queue_patterns": ["prod-*"], "worker_patterns": []}
        app_state, websocket = _make_app_state_with_real_cm(env_patterns=env)

        staging_event = _make_event(queue="staging-orders")
        prod_event = _make_event(queue="prod-orders")

        mock_ts = MagicMock()
        mock_ts.get_recent_events.return_value = {"data": [staging_event, prod_event]}

        with patch("services.TaskService", return_value=mock_ts):
            asyncio.run(ws_module._handle_get_stored_impl(app_state, websocket, {"type": "get_stored", "limit": 100}))

        sent_calls = app_state.connection_manager.send_personal_message.call_args_list
        sent_bodies = [json.loads(c[0][0]) for c in sent_calls]
        event_bodies = [b for b in sent_bodies if b.get("type") != "stored_events"]
        queues_sent = [b.get("queue") for b in event_bodies]
        self.assertNotIn("staging-orders", queues_sent)
        self.assertIn("prod-orders", queues_sent)

    def test_set_mode_impl_filters_out_non_matching_env_events(self):
        """_handle_set_mode_impl must not send events that don't match client env."""
        import api.websocket_routes as ws_module

        env = {"queue_patterns": ["prod-*"], "worker_patterns": []}
        app_state, websocket = _make_app_state_with_real_cm(env_patterns=env)

        staging_event = _make_event(queue="staging-orders")
        prod_event = _make_event(queue="prod-orders")

        mock_ts = MagicMock()
        mock_ts.get_recent_events.return_value = {"data": [staging_event, prod_event]}

        with patch("services.TaskService", return_value=mock_ts):
            asyncio.run(ws_module._handle_set_mode_impl(app_state, websocket, {"type": "set_mode", "mode": "static"}))

        sent_calls = app_state.connection_manager.send_personal_message.call_args_list
        sent_bodies = [json.loads(c[0][0]) for c in sent_calls]
        event_bodies = [b for b in sent_bodies if b.get("type") != "mode_changed"]
        queues_sent = [b.get("queue") for b in event_bodies]
        self.assertNotIn("staging-orders", queues_sent)
        self.assertIn("prod-orders", queues_sent)
