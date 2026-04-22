"""Tests for brute-force rate limiting on /api/auth/basic/login."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from api.auth_routes import _is_rate_limited, _login_attempts


class TestLoginRateLimit(unittest.TestCase):
    def setUp(self):
        """Clear the global attempts dict before each test."""
        _login_attempts.clear()

    def test_first_attempt_is_allowed(self):
        """First login attempt from an IP must be allowed."""
        self.assertTrue(_is_rate_limited("1.2.3.4", max_attempts=5, window_seconds=60))

    def test_five_attempts_are_allowed(self):
        """Five attempts within the window must all be allowed."""
        for _ in range(5):
            result = _is_rate_limited("1.2.3.4", max_attempts=5, window_seconds=60)
        self.assertTrue(result)

    def test_sixth_attempt_is_blocked(self):
        """Sixth attempt within the window must be blocked (returns False)."""
        for _ in range(5):
            _is_rate_limited("1.2.3.4", max_attempts=5, window_seconds=60)
        self.assertFalse(_is_rate_limited("1.2.3.4", max_attempts=5, window_seconds=60))

    def test_different_ips_are_independent(self):
        """Rate limiting must be per-IP — one IP exhausted must not block another."""
        for _ in range(5):
            _is_rate_limited("1.2.3.4", max_attempts=5, window_seconds=60)
        _is_rate_limited("1.2.3.4", max_attempts=5, window_seconds=60)  # 6th — blocked
        self.assertTrue(_is_rate_limited("9.9.9.9", max_attempts=5, window_seconds=60))
