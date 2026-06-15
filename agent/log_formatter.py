"""Structured JSON logging for GKE / Cloud Logging.

GKE's logging agent ingests each container's stdout/stderr stream. When a line is
plain text it infers ``severity`` from the *stream* — everything written to stderr is
tagged ``ERROR``, regardless of the log record's real level. Python's
``logging.basicConfig`` defaults its handler to stderr, so every ``INFO`` line kanchi
emits surfaces in Cloud Logging as ``severity=ERROR``.

When a line is valid JSON, the agent instead promotes recognized structured fields —
notably ``severity`` — onto the LogEntry. Emitting one JSON object per record with an
explicit ``severity`` therefore preserves the record's true level (INFO/WARNING/ERROR/
CRITICAL) instead of collapsing it to the stream's inferred severity.
"""

import json
import logging
import sys
from datetime import UTC, datetime

# Python log levels map 1:1 onto the LogSeverity strings Cloud Logging understands.
# Unknown/custom levels fall back to DEFAULT so they are never silently dropped.
_SEVERITY_BY_LEVEL = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}


class CloudLoggingFormatter(logging.Formatter):
    """Render a ``logging.LogRecord`` as a single line of Cloud Logging JSON.

    The emitted object carries an explicit ``severity`` (mapped from the Python log
    level), the rendered ``message``, the ``logger`` name, and an ISO-8601 ``time``.
    When the record carries exception info, the formatted traceback is appended to the
    ``message`` so Cloud Logging / Error Reporting can surface it.
    """

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"

        payload = {
            "severity": _SEVERITY_BY_LEVEL.get(record.levelno, "DEFAULT"),
            "message": message,
            "logger": record.name,
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
        }
        return json.dumps(payload, default=str)


def build_uvicorn_log_config(config) -> dict | None:
    """Build a uvicorn ``log_config`` so uvicorn's own loggers emit Cloud Logging JSON.

    uvicorn otherwise applies its default logging config, installing handlers on the
    ``uvicorn`` / ``uvicorn.access`` / ``uvicorn.error`` loggers that write to stderr — so
    those records would still be tagged ERROR by GKE regardless of the application's own
    logging. This returns a ``logging.config.dictConfig`` dict that routes the root and
    uvicorn loggers through :class:`CloudLoggingFormatter` on stdout in production, and
    ``None`` in development (so uvicorn keeps its human-readable console logging locally).

    ``config`` is duck-typed: it must expose ``development_mode`` and ``log_level``.
    """
    if config.development_mode:
        return None

    level = _resolve_level(config.log_level)
    uvicorn_logger = {"handlers": ["stdout"], "level": level, "propagate": False}
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"cloud_json": {"()": "log_formatter.CloudLoggingFormatter"}},
        "handlers": {
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "cloud_json",
            },
        },
        "root": {"handlers": ["stdout"], "level": level},
        "loggers": {
            "uvicorn": dict(uvicorn_logger),
            "uvicorn.error": dict(uvicorn_logger),
            "uvicorn.access": dict(uvicorn_logger),
        },
    }


def _resolve_level(log_level: str) -> int:
    """Resolve a log-level name to its numeric value.

    Tolerant of case (``"info"`` resolves to ``INFO``) and of unknown names, which fall
    back to ``INFO`` so a misconfigured ``LOG_LEVEL`` degrades gracefully rather than
    crashing the monitoring agent at startup.
    """
    return logging.getLevelNamesMapping().get(log_level.upper(), logging.INFO)


def configure_logging(config) -> None:
    """Configure root logging for the application based on ``config``.

    Production (``development_mode`` falsey): install a single stdout handler emitting
    Cloud Logging JSON via :class:`CloudLoggingFormatter`, so GKE reads the record's true
    severity instead of tagging all stderr output as ERROR.

    Development: preserve human-readable text logging to the unified log file plus the
    console, including the dedicated ``kanchi.frontend`` logger.

    ``config`` is duck-typed: it must expose ``development_mode``, ``log_level`` and
    ``log_file``.
    """
    level = _resolve_level(config.log_level)

    if config.development_mode:
        # Clean the unified log file on startup, then log human-readable text to both
        # the file and the console.
        with open(config.log_file, "w") as f:
            f.write("")

        logging.basicConfig(
            level=level,
            format="%(asctime)s [BACKEND] %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(config.log_file), logging.StreamHandler()],
            force=True,
        )

        # Frontend logs share the unified file with their own tag and do not propagate.
        # Drop any handlers from a previous configure_logging call so repeated setup
        # (e.g. a reload) does not accumulate duplicate file handlers.
        frontend_logger = logging.getLogger("kanchi.frontend")
        for existing in list(frontend_logger.handlers):
            frontend_logger.removeHandler(existing)
            existing.close()
        frontend_logger.setLevel(level)
        fh = logging.FileHandler(config.log_file)
        fh.setFormatter(logging.Formatter("%(asctime)s [FRONTEND] %(levelname)s - %(message)s"))
        frontend_logger.addHandler(fh)
        frontend_logger.propagate = False

        logger = logging.getLogger(__name__)
        logger.info("Development mode enabled - unified logging active")
        return

    # Production: emit Cloud Logging JSON to stdout so GKE reads the real severity
    # instead of tagging every stderr line as ERROR.
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(CloudLoggingFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
