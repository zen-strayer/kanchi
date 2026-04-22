"""Tests for /metrics endpoint authentication gating."""

import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from fastapi import HTTPException


def _make_app_state(auth_enabled: bool, auth_passes: bool = True):
    config = MagicMock()
    config.auth_enabled = auth_enabled

    app_state = MagicMock()
    app_state.config = config

    if auth_enabled:
        if auth_passes:
            app_state.auth_dependencies.require_user = AsyncMock(return_value=MagicMock())
        else:

            async def _reject(request):
                raise HTTPException(status_code=401, detail="Authentication required")

            app_state.auth_dependencies.require_user = _reject
    else:
        app_state.auth_dependencies = None

    return app_state


class TestMetricsAuth(unittest.TestCase):
    def _get_client(self, app_state):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.metrics_routes import create_router

        app = FastAPI()
        app.include_router(create_router(app_state))
        return TestClient(app, raise_server_exceptions=False)

    def test_metrics_open_when_auth_disabled(self):
        """GET /metrics must return 200 when AUTH_ENABLED=false."""
        client = self._get_client(_make_app_state(auth_enabled=False))
        resp = client.get("/metrics")
        self.assertEqual(resp.status_code, 200)

    def test_metrics_requires_auth_when_auth_enabled_and_no_token(self):
        """GET /metrics must return 401 when AUTH_ENABLED=true and no valid token."""
        client = self._get_client(_make_app_state(auth_enabled=True, auth_passes=False))
        resp = client.get("/metrics")
        self.assertEqual(resp.status_code, 401)

    def test_metrics_accessible_with_valid_token_when_auth_enabled(self):
        """GET /metrics must return 200 when AUTH_ENABLED=true and a valid token is provided."""
        client = self._get_client(_make_app_state(auth_enabled=True, auth_passes=True))
        resp = client.get("/metrics", headers={"Authorization": "Bearer valid-token"})
        self.assertEqual(resp.status_code, 200)

    def test_metrics_returns_prometheus_content_type(self):
        """GET /metrics must return text/plain content-type (Prometheus format)."""
        client = self._get_client(_make_app_state(auth_enabled=False))
        resp = client.get("/metrics")
        self.assertIn("text/plain", resp.headers.get("content-type", ""))
