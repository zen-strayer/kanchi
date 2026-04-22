"""Tests for orphan sleep session ordering in EventHandler."""

import os
import sys
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from event_handler import EventHandler


class TestOrphanSleepSessionOrdering(unittest.TestCase):
    """The grace-period sleep must complete BEFORE opening a DB session."""

    def _make_handler(self):
        db_manager = MagicMock()
        conn_manager = MagicMock()
        return EventHandler(db_manager, conn_manager)

    def test_mark_tasks_as_orphaned_does_not_accept_session_parameter(self):
        """Signature must be (self, hostname, orphaned_at, grace_period_seconds=2) — no session arg."""
        import inspect

        handler = self._make_handler()
        sig = inspect.signature(handler._mark_tasks_as_orphaned)
        params = list(sig.parameters.keys())
        self.assertNotIn("session", params)
        self.assertIn("hostname", params)
        self.assertIn("orphaned_at", params)

    def test_sleep_happens_before_get_session(self):
        """time.sleep must be called before db_manager.get_session is entered."""
        handler = self._make_handler()

        call_order = []

        with patch("time.sleep", side_effect=lambda _: call_order.append("sleep")):
            handler.db_manager.get_session.return_value.__enter__ = MagicMock(
                side_effect=lambda *_: call_order.append("session_open") or MagicMock()
            )
            handler.db_manager.get_session.return_value.__exit__ = MagicMock(return_value=False)

            with patch("event_handler.OrphanDetectionService") as mock_ods:
                mock_ods.return_value.find_and_mark_orphaned_tasks.return_value = []
                handler._mark_tasks_as_orphaned("worker1", datetime.now(UTC), grace_period_seconds=1)

        self.assertEqual(call_order[0], "sleep", "sleep must come before session open")
        self.assertIn("session_open", call_order)

    def test_zero_grace_period_skips_sleep(self):
        """grace_period_seconds=0 must not call time.sleep at all."""
        handler = self._make_handler()

        with patch("time.sleep") as mock_sleep, patch("event_handler.OrphanDetectionService") as mock_ods:
            mock_ods.return_value.find_and_mark_orphaned_tasks.return_value = []
            handler._mark_tasks_as_orphaned("worker1", datetime.now(UTC), grace_period_seconds=0)
            mock_sleep.assert_not_called()

    def test_handle_worker_event_worker_offline_does_not_hold_session_during_orphan_marking(self):
        """handle_worker_event must call _mark_tasks_as_orphaned OUTSIDE any session context."""
        from models import WorkerEvent

        handler = self._make_handler()

        worker_event = WorkerEvent(
            hostname="worker1",
            event_type="worker-offline",
            timestamp=datetime.now(UTC),
            status="offline",
        )

        orphan_call_order = []

        def fake_mark(hostname, orphaned_at, grace_period_seconds=2):
            orphan_call_order.append("orphan_mark")
            self.assertEqual(session_context_active, [], "Session must already be closed when orphan marking starts")

        handler._mark_tasks_as_orphaned = fake_mark

        session_context_active = []

        class FakeSession:
            def __enter__(self_inner):
                session_context_active.append(True)
                return MagicMock()

            def __exit__(self_inner, *args):
                session_context_active.pop()
                return False

        handler.db_manager.get_session.return_value = FakeSession()

        handler.handle_worker_event(worker_event)

        # Orphan marking must happen outside the session context
        self.assertEqual(orphan_call_order, ["orphan_mark"])
        self.assertEqual(session_context_active, [], "Session must be closed before orphan marking")
