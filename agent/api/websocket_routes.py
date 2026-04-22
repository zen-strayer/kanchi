"""WebSocket routes and related endpoints."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import Config
from models import (
    ConnectionInfo,
    GetStoredMessage,
    ModeChangedResponse,
    PingMessage,
    PongResponse,
    SetModeMessage,
    StoredEventsResponse,
    SubscribeMessage,
    SubscriptionResponse,
    TaskEvent,
    WebSocketErrorResponse,
)
from security.auth import AuthError
from security.tokens import TokenError
from services.auth_service import AuthService

logger = logging.getLogger(__name__)

GET_STORED_LIMIT_MAX = 10_000
GET_STORED_LIMIT_DEFAULT = 1_000


async def _handle_get_stored_impl(app_state, websocket: WebSocket, message: dict):
    """Handle get_stored WebSocket message with limit validation.

    Args:
        app_state: Application state containing db_manager and connection_manager
        websocket: WebSocket connection
        message: The incoming message dict with optional 'limit' key
    """
    raw_limit = message.get("limit")
    if raw_limit is None:
        limit = GET_STORED_LIMIT_DEFAULT
    elif not isinstance(raw_limit, int) or raw_limit < 1:
        error_response = WebSocketErrorResponse(message=f"limit must be a positive integer. Got: {raw_limit!r}.")
        await app_state.connection_manager.send_personal_message(error_response.model_dump_json(), websocket)
        return
    else:
        limit = raw_limit

    if limit > GET_STORED_LIMIT_MAX:
        error_response = WebSocketErrorResponse(
            message=f"limit exceeds maximum allowed value of {GET_STORED_LIMIT_MAX}. Requested: {limit}."
        )
        await app_state.connection_manager.send_personal_message(error_response.model_dump_json(), websocket)
        return

    events_sent = 0

    if app_state.db_manager:
        from services import TaskService

        env = app_state.connection_manager.client_environments.get(websocket)
        with app_state.db_manager.get_session() as session:
            task_service = TaskService(session, active_env=None)
            recent_data = task_service.get_recent_events(limit=limit, page=0)
            for event_data in recent_data["data"]:
                filters = app_state.connection_manager.client_filters.get(websocket, {})
                if not _matches_filters(event_data, filters):
                    continue
                if not app_state.connection_manager._matches_environment(event_data, env):
                    continue
                await app_state.connection_manager.send_personal_message(event_data.model_dump_json(), websocket)
                events_sent += 1

    stored_response = StoredEventsResponse(count=events_sent, timestamp=datetime.now(UTC))
    await app_state.connection_manager.send_personal_message(stored_response.model_dump_json(), websocket)


async def _handle_set_mode_impl(app_state, websocket: WebSocket, message: dict[str, Any]):
    """Core logic for set_mode — extracted for testability."""
    mode = message.get("mode", "live")
    app_state.connection_manager.set_client_mode(websocket, mode)

    events_sent = 0
    if mode == "static" and app_state.db_manager:
        from services import TaskService

        env = app_state.connection_manager.client_environments.get(websocket)
        with app_state.db_manager.get_session() as session:
            task_service = TaskService(session, active_env=None)
            recent_data = task_service.get_recent_events(limit=100, page=0)
            for event_data in recent_data["data"]:
                filters = app_state.connection_manager.client_filters.get(websocket, {})
                if not _matches_filters(event_data, filters):
                    continue
                if not app_state.connection_manager._matches_environment(event_data, env):
                    continue
                await app_state.connection_manager.send_personal_message(event_data.model_dump_json(), websocket)
                events_sent += 1

    mode_response = ModeChangedResponse(
        mode=mode, timestamp=datetime.now(UTC), events_count=events_sent if mode == "static" else None
    )
    await app_state.connection_manager.send_personal_message(mode_response.model_dump_json(), websocket)


def _matches_filters(event_data: Any, filters: dict[str, Any]) -> bool:
    """Check if event data matches the given filters."""
    if not filters:
        return True

    # Convert event data to dict if it's a Pydantic model
    if hasattr(event_data, "model_dump"):
        event_dict = event_data.model_dump()
    elif hasattr(event_data, "dict"):
        event_dict = event_data.dict()
    else:
        event_dict = event_data if isinstance(event_data, dict) else {}

    # Apply filters
    for filter_key, filter_value in filters.items():
        if filter_key in event_dict:
            event_value = event_dict[filter_key]
            # Support both exact match and contains for string fields
            if isinstance(filter_value, str) and isinstance(event_value, str):
                if filter_value.lower() not in event_value.lower():
                    return False
            elif event_value != filter_value:
                return False
        else:
            # If filter key doesn't exist in event, filter doesn't match
            return False

    return True


def create_router(app_state) -> APIRouter:  # noqa: C901
    """Create websocket router with dependency injection."""
    router = APIRouter(tags=["websocket"])

    config = app_state.config or Config.from_env()

    @router.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):  # noqa: C901
        """WebSocket endpoint for real-time event streaming."""
        if not app_state.connection_manager:
            await websocket.close(code=1011, reason="Server not initialized")
            return

        if config.auth_enabled:
            token = websocket.query_params.get("token")
            if not token:
                auth_header = websocket.headers.get("Authorization")
                if auth_header and auth_header.startswith("Bearer "):
                    token = auth_header[len("Bearer ") :].strip()
            if not token:
                await websocket.close(code=4401, reason="Authentication required")
                return

            if not app_state.auth_manager or not app_state.db_manager:
                await websocket.close(code=1011, reason="Authentication not initialized")
                return

            try:
                with app_state.db_manager.get_session() as session:
                    auth_service = AuthService(session, app_state.auth_manager)
                    auth_context = auth_service.authenticate_access_token(token)
                    websocket.scope["auth_user"] = auth_context  # type: ignore[assignment]
            except (AuthError, TokenError) as exc:
                logger.warning("WebSocket authentication failed: %s", exc)
                await websocket.close(code=4401, reason="Unauthorized")
                return

        await app_state.connection_manager.connect(websocket)

        welcome = ConnectionInfo(
            status="connected",
            timestamp=datetime.now(UTC),
            message="Connected to Celery Event Monitor",
            total_connections=len(app_state.connection_manager.active_connections),
        )
        await app_state.connection_manager.send_personal_message(welcome.model_dump_json(), websocket)

        try:
            while True:
                data = await websocket.receive_text()

                try:
                    message = json.loads(data)

                    if message.get("type") == "ping":
                        pong_response = PongResponse(timestamp=datetime.now(UTC))
                        await app_state.connection_manager.send_personal_message(
                            pong_response.model_dump_json(), websocket
                        )

                    elif message.get("type") == "subscribe":
                        filters = message.get("filters", {})
                        environment_id = message.get("environment_id")

                        app_state.connection_manager.set_client_filters(websocket, filters)

                        if environment_id and app_state.db_manager:
                            from database import EnvironmentDB

                            with app_state.db_manager.get_session() as session:
                                env_db = (
                                    session.query(EnvironmentDB).filter_by(id=environment_id, is_active=True).first()
                                )
                                if env_db:
                                    app_state.connection_manager.set_client_environment(
                                        websocket,
                                        queue_patterns=env_db.queue_patterns or [],
                                        worker_patterns=env_db.worker_patterns or [],
                                    )
                                else:
                                    app_state.connection_manager.set_client_environment(websocket, [], [])

                        response = SubscriptionResponse(
                            status="acknowledged", filters=filters, timestamp=datetime.now(UTC)
                        )
                        await app_state.connection_manager.send_personal_message(response.model_dump_json(), websocket)

                    elif message.get("type") == "set_mode":
                        await handle_set_mode(websocket, message)

                    elif message.get("type") == "get_stored":
                        await handle_get_stored(websocket, message)

                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON received: {data}")

        except WebSocketDisconnect:
            app_state.connection_manager.disconnect(websocket)

    async def handle_set_mode(websocket: WebSocket, message: dict[str, Any]):
        """Handle set_mode WebSocket message."""
        await _handle_set_mode_impl(app_state, websocket, message)

    async def handle_get_stored(websocket: WebSocket, message: dict[str, Any]):
        """Handle get_stored WebSocket message."""
        await _handle_get_stored_impl(app_state, websocket, message)

    @router.get("/api/websocket/message-types")
    async def get_websocket_message_types():
        """Get schema information for WebSocket message types."""
        return {
            "incoming_messages": {
                "ping": PingMessage.model_json_schema(),
                "subscribe": SubscribeMessage.model_json_schema(),
                "set_mode": SetModeMessage.model_json_schema(),
                "get_stored": GetStoredMessage.model_json_schema(),
            },
            "outgoing_messages": {
                "pong": PongResponse.model_json_schema(),
                "subscription_response": SubscriptionResponse.model_json_schema(),
                "mode_changed": ModeChangedResponse.model_json_schema(),
                "stored_events_sent": StoredEventsResponse.model_json_schema(),
                "connection_info": ConnectionInfo.model_json_schema(),
                "task_event": TaskEvent.model_json_schema(),
            },
        }

    return router
