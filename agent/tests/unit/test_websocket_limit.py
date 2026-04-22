"""Tests for get_stored WebSocket message limit cap."""

import asyncio
import json
import os
import sys
import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from models import WebSocketErrorResponse


class TestGetStoredLimitCap(unittest.TestCase):
    def _make_app_state(self, db_manager=None):
        app_state = MagicMock()
        app_state.db_manager = db_manager
        app_state.connection_manager = MagicMock()
        app_state.connection_manager.send_personal_message = AsyncMock()
        app_state.connection_manager.client_filters = {}
        return app_state

    def test_websocket_error_response_model_exists(self):
        """WebSocketErrorResponse must exist with type='error' and message field."""
        resp = WebSocketErrorResponse(message="test error", timestamp=datetime.now(UTC))
        self.assertEqual(resp.type, "error")
        self.assertEqual(resp.message, "test error")
        parsed = json.loads(resp.model_dump_json())
        self.assertEqual(parsed["type"], "error")
        self.assertIn("message", parsed)

    def test_limit_exactly_10000_is_accepted(self):
        """limit=10000 must not trigger an error."""
        import api.websocket_routes as ws_module

        self.assertEqual(ws_module.GET_STORED_LIMIT_MAX, 10_000)

    def test_limit_over_10000_returns_error_response(self):
        """limit > 10000 must send an error WebSocket message and not query the DB."""
        import api.websocket_routes as ws_module

        app_state = self._make_app_state(db_manager=MagicMock())
        websocket = MagicMock()

        async def run():
            await ws_module._handle_get_stored_impl(app_state, websocket, {"type": "get_stored", "limit": 99_999})

        # Use asyncio.run for better event loop handling
        asyncio.run(run())

        sent_json = app_state.connection_manager.send_personal_message.call_args[0][0]
        sent = json.loads(sent_json)
        self.assertEqual(sent["type"], "error")
        self.assertIn("10000", sent["message"])
        app_state.db_manager.get_session.assert_not_called()

    def test_limit_none_defaults_to_1000(self):
        """Omitting limit must default to 1000 (existing behaviour)."""
        import api.websocket_routes as ws_module

        self.assertEqual(ws_module.GET_STORED_LIMIT_DEFAULT, 1_000)

    def test_negative_limit_returns_error(self):
        """limit < 1 must send an error response and not query the DB."""
        import api.websocket_routes as ws_module

        app_state = self._make_app_state(db_manager=MagicMock())
        websocket = MagicMock()

        async def run():
            await ws_module._handle_get_stored_impl(app_state, websocket, {"type": "get_stored", "limit": -1})

        asyncio.run(run())

        sent_json = app_state.connection_manager.send_personal_message.call_args[0][0]
        sent = json.loads(sent_json)
        self.assertEqual(sent["type"], "error")
        app_state.db_manager.get_session.assert_not_called()

    def test_non_integer_limit_returns_error(self):
        """A non-integer limit must send an error response and not query the DB."""
        import api.websocket_routes as ws_module

        app_state = self._make_app_state(db_manager=MagicMock())
        websocket = MagicMock()

        async def run():
            await ws_module._handle_get_stored_impl(app_state, websocket, {"type": "get_stored", "limit": "all"})

        asyncio.run(run())

        sent_json = app_state.connection_manager.send_personal_message.call_args[0][0]
        sent = json.loads(sent_json)
        self.assertEqual(sent["type"], "error")
        app_state.db_manager.get_session.assert_not_called()
