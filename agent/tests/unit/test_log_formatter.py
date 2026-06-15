"""Tests for the Cloud Logging JSON formatter.

These pin down the structured-logging behavior that fixes kanchi's INFO-logs-as-ERROR
problem in GKE: each record must serialize to a single JSON line carrying an explicit
``severity`` matching the record's true Python level, so Cloud Logging stops inferring
severity from the (stderr) stream.
"""

import json
import logging
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from log_formatter import CloudLoggingFormatter, configure_logging


def _config(**overrides):
    """A minimal duck-typed stand-in for Config carrying only the logging attributes."""
    base = {
        "development_mode": False,
        "log_level": "INFO",
        "log_format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        "log_file": "kanchi.log",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_record(level: int, msg: str, args=(), *, name: str = "services.orphan_detection_service"):
    """Build a LogRecord the way logging.Logger._log would, without a configured logger."""
    return logging.LogRecord(name=name, level=level, pathname=__file__, lineno=1, msg=msg, args=args, exc_info=None)


class TestCloudLoggingFormatter(unittest.TestCase):
    def setUp(self):
        self.formatter = CloudLoggingFormatter()

    def test_output_is_valid_single_line_json(self):
        """A formatted record must be exactly one line of parseable JSON."""
        out = self.formatter.format(_make_record(logging.INFO, "hello"))
        self.assertNotIn("\n", out)
        json.loads(out)  # raises if not valid JSON

    def test_severity_matches_info_level(self):
        """An INFO record must carry severity=INFO (not ERROR)."""
        payload = json.loads(self.formatter.format(_make_record(logging.INFO, "hello")))
        self.assertEqual(payload["severity"], "INFO")

    def test_severity_matches_warning_level(self):
        """WARNING must stay WARNING — the level distinction stream-split loses."""
        payload = json.loads(self.formatter.format(_make_record(logging.WARNING, "careful")))
        self.assertEqual(payload["severity"], "WARNING")

    def test_severity_matches_error_level(self):
        """A genuine ERROR record must carry severity=ERROR."""
        payload = json.loads(self.formatter.format(_make_record(logging.ERROR, "boom")))
        self.assertEqual(payload["severity"], "ERROR")

    def test_message_is_rendered_with_args(self):
        """%-style args must be interpolated into the message field."""
        payload = json.loads(
            self.formatter.format(_make_record(logging.INFO, "Broadcasting orphan event for task %s", ("abc123",)))
        )
        self.assertEqual(payload["message"], "Broadcasting orphan event for task abc123")

    def test_logger_name_included(self):
        """The originating logger name must be preserved for filtering."""
        payload = json.loads(self.formatter.format(_make_record(logging.INFO, "hi")))
        self.assertEqual(payload["logger"], "services.orphan_detection_service")

    def test_time_field_present(self):
        """Each entry must carry a timestamp field."""
        payload = json.loads(self.formatter.format(_make_record(logging.INFO, "hi")))
        self.assertIn("time", payload)
        self.assertTrue(payload["time"])

    def test_severity_matches_debug_level(self):
        """DEBUG records must map to severity=DEBUG."""
        payload = json.loads(self.formatter.format(_make_record(logging.DEBUG, "trace")))
        self.assertEqual(payload["severity"], "DEBUG")

    def test_severity_matches_critical_level(self):
        """CRITICAL records must map to severity=CRITICAL."""
        payload = json.loads(self.formatter.format(_make_record(logging.CRITICAL, "fatal")))
        self.assertEqual(payload["severity"], "CRITICAL")

    def test_unicode_message_round_trips(self):
        """Non-ASCII content must survive serialization intact (json escapes, json decodes back)."""
        payload = json.loads(self.formatter.format(_make_record(logging.INFO, "café 日本語 \U0001f600")))
        self.assertEqual(payload["message"], "café 日本語 \U0001f600")

    def test_exception_traceback_included_in_message(self):
        """When a record has exc_info, the traceback must be folded into the message."""
        try:
            raise ValueError("kaboom")
        except ValueError:
            record = logging.LogRecord(
                name="worker_health_monitor",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="Error marking tasks as orphaned",
                args=(),
                exc_info=sys.exc_info(),
            )
        payload = json.loads(self.formatter.format(record))
        self.assertIn("Error marking tasks as orphaned", payload["message"])
        self.assertIn("ValueError: kaboom", payload["message"])
        self.assertIn("Traceback", payload["message"])


class TestConfigureLogging(unittest.TestCase):
    """The production branch must route JSON-with-severity to stdout; dev stays plain text."""

    def setUp(self):
        root = logging.getLogger()
        self._saved_handlers = root.handlers[:]
        self._saved_level = root.level
        frontend = logging.getLogger("kanchi.frontend")
        self._saved_frontend_handlers = frontend.handlers[:]
        self._saved_frontend_level = frontend.level
        self._saved_frontend_propagate = frontend.propagate

    def tearDown(self):
        root = logging.getLogger()
        root.handlers[:] = self._saved_handlers
        root.setLevel(self._saved_level)
        # Restore the kanchi.frontend logger too, closing only handlers this test created,
        # so configure_logging's dev-mode setup cannot leak into the rest of the suite.
        frontend = logging.getLogger("kanchi.frontend")
        for h in list(frontend.handlers):
            if h not in self._saved_frontend_handlers:
                h.close()
        frontend.handlers[:] = self._saved_frontend_handlers
        frontend.setLevel(self._saved_frontend_level)
        frontend.propagate = self._saved_frontend_propagate

    def test_production_emits_cloud_logging_json_to_stdout(self):
        """In production, the root logger must write Cloud Logging JSON to stdout (not stderr)."""
        configure_logging(_config(development_mode=False))
        json_handlers = [
            h
            for h in logging.getLogger().handlers
            if isinstance(h, logging.StreamHandler) and isinstance(h.formatter, CloudLoggingFormatter)
        ]
        self.assertTrue(json_handlers, "expected a JSON-formatted stream handler")
        self.assertTrue(all(h.stream is sys.stdout for h in json_handlers))

    def test_production_has_no_stderr_handler(self):
        """No handler may target stderr in production — that is what GKE mis-tags as ERROR."""
        configure_logging(_config(development_mode=False))
        stream_handlers = [h for h in logging.getLogger().handlers if isinstance(h, logging.StreamHandler)]
        self.assertTrue(stream_handlers)
        self.assertFalse(any(h.stream is sys.stderr for h in stream_handlers))

    def test_production_sets_configured_level(self):
        """The root logger level must come from config.log_level."""
        configure_logging(_config(development_mode=False, log_level="WARNING"))
        self.assertEqual(logging.getLogger().level, logging.WARNING)

    def test_development_does_not_use_json_formatter(self):
        """Local dev keeps human-readable logs; JSON formatting is a production-only concern."""
        with tempfile.NamedTemporaryFile(suffix=".log") as tmp:
            configure_logging(_config(development_mode=True, log_file=tmp.name))
        self.assertFalse(any(isinstance(h.formatter, CloudLoggingFormatter) for h in logging.getLogger().handlers))

    def test_lowercase_log_level_does_not_crash(self):
        """A lowercase LOG_LEVEL (e.g. 'info') must resolve to the level, not raise."""
        configure_logging(_config(development_mode=False, log_level="info"))
        self.assertEqual(logging.getLogger().level, logging.INFO)

    def test_invalid_log_level_falls_back_to_info(self):
        """An unknown LOG_LEVEL must degrade to INFO instead of crashing the agent at startup."""
        configure_logging(_config(development_mode=False, log_level="BOGUS"))
        self.assertEqual(logging.getLogger().level, logging.INFO)

    def test_repeated_dev_configure_does_not_duplicate_frontend_handlers(self):
        """configure_logging must be idempotent for the kanchi.frontend logger in dev mode."""
        with tempfile.NamedTemporaryFile(suffix=".log") as tmp:
            cfg = _config(development_mode=True, log_file=tmp.name)
            configure_logging(cfg)
            configure_logging(cfg)
            self.assertEqual(len(logging.getLogger("kanchi.frontend").handlers), 1)


if __name__ == "__main__":
    unittest.main()
