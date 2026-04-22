"""Tests for pickle serialization startup warnings."""

import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from config import Config


class TestPickleWarning(unittest.TestCase):
    def test_pickle_disabled_emits_no_critical_warning(self):
        """No CRITICAL warning logged when pickle serialization is disabled (default)."""
        import io

        handler = logging.StreamHandler(io.StringIO())
        handler.setLevel(logging.CRITICAL)
        log = logging.getLogger("config")
        log.addHandler(handler)
        try:
            Config(broker_url="redis://localhost:6379/0", enable_pickle_serialization=False)
            output = handler.stream.getvalue()
            self.assertNotIn("pickle", output.lower())
        finally:
            log.removeHandler(handler)

    def test_pickle_enabled_emits_critical_log_in_config(self):
        """Config.__post_init__ must emit a CRITICAL log when pickle is enabled."""
        with self.assertLogs("config", level="CRITICAL") as log_ctx:
            Config(broker_url="redis://localhost:6379/0", enable_pickle_serialization=True)
        self.assertTrue(any("pickle" in r.lower() for r in log_ctx.output))

    def test_monitor_emits_critical_log_when_pickle_enabled(self):
        """CeleryEventMonitor must emit a CRITICAL log when allow_pickle_serialization=True."""
        from monitor import CeleryEventMonitor

        with self.assertLogs("monitor", level="CRITICAL") as log_ctx:
            CeleryEventMonitor(
                broker_url="redis://localhost:6379/0",
                allow_pickle_serialization=True,
            )
        self.assertTrue(any("pickle" in r.lower() for r in log_ctx.output))
