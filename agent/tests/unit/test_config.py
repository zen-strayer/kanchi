"""Tests for Config startup validation."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from config import Config


class TestConfigBrokerValidation(unittest.TestCase):

    def test_missing_broker_url_raises_value_error(self):
        """Config must raise ValueError with a helpful message when broker_url is None."""
        with self.assertRaises(ValueError) as ctx:
            Config(broker_url=None)
        self.assertIn("CELERY_BROKER_URL", str(ctx.exception))

    def test_empty_string_broker_url_raises_value_error(self):
        """An empty string is as useless as None — must also raise."""
        with self.assertRaises(ValueError):
            Config(broker_url="")

    def test_redis_url_is_accepted(self):
        """A valid Redis broker URL must not raise."""
        config = Config(broker_url="redis://localhost:6379/0")
        self.assertEqual(config.broker_url, "redis://localhost:6379/0")

    def test_amqp_url_is_accepted(self):
        """A valid AMQP broker URL must not raise."""
        config = Config(broker_url="amqp://guest:guest@localhost:5672//")
        self.assertEqual(config.broker_url, "amqp://guest:guest@localhost:5672//")

    def test_broker_url_type_annotation_allows_none_before_post_init(self):
        """The field type should be str | None so the dataclass field accepts None before validation."""
        import inspect
        hints = {}
        for cls in Config.__mro__:
            if hasattr(cls, "__annotations__"):
                hints.update(cls.__annotations__)
        # broker_url should be annotated as str | None
        broker_annotation = str(hints.get("broker_url", ""))
        self.assertIn("None", broker_annotation)


if __name__ == "__main__":
    unittest.main()
