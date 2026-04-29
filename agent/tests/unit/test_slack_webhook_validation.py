"""Tests for Slack webhook URL domain validation in ActionConfigService."""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.action_config_service import ActionConfigService


class TestSlackWebhookValidation(unittest.TestCase):
    def setUp(self):
        self.service = ActionConfigService(MagicMock())

    def test_valid_slack_webhook_is_accepted(self):
        """A valid https://hooks.slack.com/... URL must be stored as-is."""
        result = self.service._sanitize_config(
            "slack.notify",
            {"webhook_url": "https://hooks.slack.com/services/T000/B000/xxxx"},
        )
        self.assertEqual(
            result["webhook_url"],
            "https://hooks.slack.com/services/T000/B000/xxxx",
        )

    def test_non_slack_domain_raises_value_error(self):
        """A webhook URL pointing to a non-Slack domain must raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.service._sanitize_config(
                "slack.notify",
                {"webhook_url": "https://evil.example.com/steal-data"},
            )
        self.assertIn("hooks.slack.com", str(ctx.exception))

    def test_http_url_raises_value_error(self):
        """A non-HTTPS Slack URL must raise ValueError."""
        with self.assertRaises(ValueError) as ctx:
            self.service._sanitize_config(
                "slack.notify",
                {"webhook_url": "http://hooks.slack.com/services/T000/B000/xxxx"},
            )
        self.assertIn("HTTPS", str(ctx.exception))

    def test_empty_webhook_url_returns_empty_dict(self):
        """An empty webhook_url must return {} (existing behaviour)."""
        result = self.service._sanitize_config("slack.notify", {"webhook_url": ""})
        self.assertEqual(result, {})

    def test_non_slack_config_type_is_unchanged(self):
        """Non-slack action types must pass through _sanitize_config unchanged."""
        config = {"some_key": "some_value"}
        result = self.service._sanitize_config("email.send", config)
        self.assertEqual(result, config)
