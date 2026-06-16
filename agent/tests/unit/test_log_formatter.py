"""Tests for the structured JSON log formatter.

These pin down the structured-logging behavior that fixes kanchi's INFO-logs-as-ERROR
problem under any log collector that infers severity from the output stream: each record
must serialize to a single JSON line carrying an explicit ``level``/``severity`` matching
the record's true Python level, so the collector stops inferring severity from the
(stderr) stream. (The deployment that surfaced this was GKE's Cloud Logging agent, but
the fix is platform-agnostic.)
"""

import json
import logging
import logging.config
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from log_formatter import JSONLogFormatter, build_uvicorn_log_config, configure_logging


def _config(**overrides):
    """A minimal duck-typed stand-in for Config carrying only the logging attributes."""
    base = {
        "development_mode": False,
        "log_level": "INFO",
        "log_file": "kanchi.log",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _snapshot_loggers(names):
    """Capture handlers/level/propagate for the named loggers (plus root) for restoration."""
    snap = {}
    for name in names:
        lg = logging.getLogger(name)
        snap[name] = (lg.handlers[:], lg.level, lg.propagate)
    return snap


def _restore_loggers(snap):
    """Restore loggers captured by _snapshot_loggers, closing any handlers added since."""
    for name, (handlers, level, propagate) in snap.items():
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            if h not in handlers:
                h.close()
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate


def _make_record(level: int, msg: str, args=(), *, name: str = "services.orphan_detection_service"):
    """Build a LogRecord the way logging.Logger._log would, without a configured logger."""
    return logging.LogRecord(name=name, level=level, pathname=__file__, lineno=1, msg=msg, args=args, exc_info=None)


class TestJSONLogFormatter(unittest.TestCase):
    def setUp(self):
        self.formatter = JSONLogFormatter()

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

    def test_level_field_mirrors_severity(self):
        """A generic ``level`` field must accompany ``severity`` (same value) so collectors
        that key on ``level`` — CloudWatch, ELK, Datadog, etc. — read the true level too,
        not just GCP's ``severity``."""
        for level, name in ((logging.INFO, "INFO"), (logging.WARNING, "WARNING"), (logging.ERROR, "ERROR")):
            payload = json.loads(self.formatter.format(_make_record(level, "msg")))
            self.assertEqual(payload["level"], name)
            self.assertEqual(payload["level"], payload["severity"])

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
    """The production branch must route JSON-with-level to stdout; dev stays plain text."""

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

    def test_production_emits_json_to_stdout(self):
        """In production, the root logger must write structured JSON to stdout (not stderr)."""
        configure_logging(_config(development_mode=False))
        json_handlers = [
            h
            for h in logging.getLogger().handlers
            if isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JSONLogFormatter)
        ]
        self.assertTrue(json_handlers, "expected a JSON-formatted stream handler")
        self.assertTrue(all(h.stream is sys.stdout for h in json_handlers))

    def test_production_has_no_stderr_handler(self):
        """No handler may target stderr in production — that is the stream collectors mis-tag as ERROR."""
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
        self.assertFalse(any(isinstance(h.formatter, JSONLogFormatter) for h in logging.getLogger().handlers))

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


class TestUvicornLogConfig(unittest.TestCase):
    """build_uvicorn_log_config must hand uvicorn a config that routes ITS loggers through
    the JSON formatter in production, so uvicorn's own logs aren't mis-tagged ERROR by a
    stream-inferring collector."""

    _UVICORN_LOGGERS = ["", "uvicorn", "uvicorn.error", "uvicorn.access"]

    def setUp(self):
        self._snap = _snapshot_loggers(self._UVICORN_LOGGERS)

    def tearDown(self):
        _restore_loggers(self._snap)

    def test_returns_none_in_development(self):
        """Dev keeps uvicorn's default console logging — no override."""
        self.assertIsNone(build_uvicorn_log_config(_config(development_mode=True)))

    def test_production_uses_json_formatter_factory(self):
        """The formatter must be the JSONLogFormatter, referenced by import path."""
        cfg = build_uvicorn_log_config(_config(development_mode=False))
        formatter = next(iter(cfg["formatters"].values()))
        self.assertEqual(formatter["()"], "log_formatter.JSONLogFormatter")

    def test_production_routes_uvicorn_loggers_to_stdout(self):
        """uvicorn / uvicorn.access / uvicorn.error must all be wired to a stdout handler."""
        cfg = build_uvicorn_log_config(_config(development_mode=False))
        stdout_handlers = [h for h, spec in cfg["handlers"].items() if spec.get("stream") == "ext://sys.stdout"]
        self.assertTrue(stdout_handlers)
        for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
            self.assertIn(name, cfg["loggers"])
            self.assertTrue(set(cfg["loggers"][name]["handlers"]) & set(stdout_handlers))

    def test_production_config_is_valid_and_applies_json_to_uvicorn_loggers(self):
        """The dict must be a valid dictConfig that yields JSON formatting on uvicorn loggers."""
        cfg = build_uvicorn_log_config(_config(development_mode=False))
        logging.config.dictConfig(cfg)
        for name in ("", "uvicorn.access", "uvicorn.error"):
            handlers = logging.getLogger(name).handlers
            self.assertTrue(
                any(isinstance(h.formatter, JSONLogFormatter) for h in handlers),
                f"logger {name!r} should emit structured JSON",
            )


class TestServerLoggingWiring(unittest.TestCase):
    """main() and start_server() must hand the built log config to uvicorn so uvicorn's
    loggers are covered too (not just the application's own loggers)."""

    def setUp(self):
        self._snap = _snapshot_loggers(["", "kanchi.frontend"])

    def tearDown(self):
        _restore_loggers(self._snap)

    def _run_entrypoint_capturing_uvicorn(self, module, entry):
        """Invoke an entrypoint with uvicorn.run and Config.from_env stubbed; return run kwargs."""
        import config as config_module

        cfg = config_module.Config(broker_url="redis://localhost:6379/0", development_mode=False)
        captured = {}
        orig_run = module.uvicorn.run
        orig_from_env = config_module.Config.from_env
        module.uvicorn.run = lambda *a, **k: captured.update(kwargs=k)
        config_module.Config.from_env = staticmethod(lambda: cfg)
        try:
            entry()
        finally:
            module.uvicorn.run = orig_run
            config_module.Config.from_env = orig_from_env
        return captured["kwargs"], cfg

    # NOTE: start_server() (the `python app.py` path) is wired identically to main(), but
    # importing the `app` module triggers its module-level `app = create_app()`, which
    # requires CELERY_BROKER_URL at import time — so it can't be exercised in a unit test
    # without broker env. The production entrypoint is main.py (see start.sh), covered below;
    # the log-config logic itself is covered by TestUvicornLogConfig.

    def test_main_wires_json_log_config_into_uvicorn(self):
        import main as main_module

        orig_argv = sys.argv
        sys.argv = ["main.py"]
        try:
            kwargs, cfg = self._run_entrypoint_capturing_uvicorn(main_module, main_module.main)
        finally:
            sys.argv = orig_argv
        self.assertIn("log_config", kwargs)
        self.assertEqual(kwargs["log_config"], build_uvicorn_log_config(cfg))


if __name__ == "__main__":
    unittest.main()
