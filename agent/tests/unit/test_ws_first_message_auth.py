"""Tests for WebSocket first-message authentication."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from models import AuthMessage


class TestAuthMessageModel(unittest.TestCase):
    def test_auth_message_model_exists(self):
        """AuthMessage model must exist with type='auth' and token field."""
        msg = AuthMessage(token="test-token")
        self.assertEqual(msg.type, "auth")
        self.assertEqual(msg.token, "test-token")

    def test_auth_message_type_is_literal(self):
        """AuthMessage.type must be 'auth' by default."""
        msg = AuthMessage(token="abc")
        parsed = json.loads(msg.model_dump_json())
        self.assertEqual(parsed["type"], "auth")

    def test_auth_message_token_required(self):
        """AuthMessage must require a token field."""
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            AuthMessage()
