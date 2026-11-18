"""Tests for the outbound HTTP calls made with aiohttp.

These run the real aiohttp client against a local aiohttp server, so they
catch client API changes when the aiohttp version moves.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.actions.slack_action import SlackActionHandler
from services.auth_service import AuthError, AuthService


class _ServerTestCase(unittest.IsolatedAsyncioTestCase):
    """Starts a local aiohttp server with the routes from build_app()."""

    def build_app(self) -> web.Application:
        raise NotImplementedError

    async def asyncSetUp(self):
        self.requests: list[dict] = []
        self.server = TestServer(self.build_app())
        await self.server.start_server()

    async def asyncTearDown(self):
        await self.server.close()

    def url(self, path: str) -> str:
        return str(self.server.make_url(path))


class TestSlackActionHttp(_ServerTestCase):
    """SlackActionHandler.execute posts JSON to the webhook and maps the status."""

    def build_app(self) -> web.Application:
        async def ok(request: web.Request) -> web.Response:
            self.requests.append(await request.json())
            return web.Response(text="ok")

        async def fail(request: web.Request) -> web.Response:
            return web.Response(status=500, text="boom")

        app = web.Application()
        app.router.add_post("/ok", ok)
        app.router.add_post("/fail", fail)
        return app

    async def _execute(self, path: str):
        handler = SlackActionHandler(MagicMock(), MagicMock())
        config = SimpleNamespace(id="cfg-1", config={"webhook_url": self.url(path)})
        with patch("services.actions.slack_action.ActionConfigService") as service_cls:
            service_cls.return_value.get_config.return_value = config
            return await handler.execute(
                {"task_name": "tasks.add", "task_id": "abc"},
                {"config_id": "cfg-1", "template": "Task {{task_name}} failed"},
            )

    async def test_success_posts_payload_and_reports_success(self):
        result = await self._execute("/ok")

        self.assertEqual(result.status, "success", result.error_message)
        self.assertEqual(len(self.requests), 1)
        self.assertIn("Task tasks.add failed", str(self.requests[0]))

    async def test_error_status_reports_failure_with_body(self):
        result = await self._execute("/fail")

        self.assertEqual(result.status, "failed")
        self.assertIn("500", result.error_message)
        self.assertIn("boom", result.error_message)


class TestOAuthHttp(_ServerTestCase):
    """AuthService OAuth helpers send form/bearer requests and parse JSON replies."""

    def build_app(self) -> web.Application:
        async def token(request: web.Request) -> web.Response:
            form = await request.post()
            self.requests.append(dict(form))
            if form.get("code") == "bad":
                return web.Response(status=400, text="invalid_grant")
            return web.json_response({"access_token": "tok-123"})

        async def userinfo(request: web.Request) -> web.Response:
            if request.headers.get("Authorization") != "Bearer tok-123":
                return web.Response(status=401, text="unauthorized")
            return web.json_response({"email": "dev@example.com", "name": "Dev"})

        app = web.Application()
        app.router.add_post("/token", token)
        app.router.add_get("/userinfo", userinfo)
        return app

    def _service(self) -> AuthService:
        return AuthService(MagicMock(), MagicMock())

    def _provider(self) -> SimpleNamespace:
        return SimpleNamespace(
            name="google",
            client_id="cid",
            client_secret="secret",
            token_url=self.url("/token"),
            userinfo_url=self.url("/userinfo"),
        )

    async def test_exchange_code_posts_form_and_returns_json(self):
        data = await self._service()._exchange_oauth_code(self._provider(), "good", "https://app/cb")

        self.assertEqual(data, {"access_token": "tok-123"})
        self.assertEqual(self.requests[0]["code"], "good")
        self.assertEqual(self.requests[0]["grant_type"], "authorization_code")

    async def test_exchange_code_error_raises_auth_error(self):
        with self.assertRaises(AuthError):
            await self._service()._exchange_oauth_code(self._provider(), "bad", "https://app/cb")

    async def test_fetch_userinfo_sends_bearer_token(self):
        data = await self._service()._fetch_userinfo(self._provider(), {"access_token": "tok-123"})

        self.assertEqual(data["email"], "dev@example.com")

    async def test_fetch_userinfo_error_raises_auth_error(self):
        with self.assertRaises(AuthError):
            await self._service()._fetch_userinfo(self._provider(), {"access_token": "wrong"})


if __name__ == "__main__":
    unittest.main()
