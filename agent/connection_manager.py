import asyncio
import fnmatch
import logging

from fastapi import WebSocket

from models import TaskEvent, WorkerEvent

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self.client_filters: dict[WebSocket, dict] = {}
        self.client_modes: dict[WebSocket, str] = {}
        self.client_environments: dict[WebSocket, dict | None] = {}
        self.message_queue: asyncio.Queue | None = None
        self._broadcast_task = None
        self._running = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def start_background_broadcaster(self):
        task_inactive = self._broadcast_task is None or self._broadcast_task.done()
        if task_inactive and not self._running:
            self._running = True
            self._loop = asyncio.get_running_loop()
            self.message_queue = asyncio.Queue()
            self._broadcast_task = asyncio.create_task(self._background_broadcaster())
            logger.info("Background broadcaster started")

    async def _background_broadcaster(self):
        while self._running:
            try:
                try:
                    message_type, data = await asyncio.wait_for(self.message_queue.get(), timeout=0.1)

                    if message_type == "task":
                        await self._broadcast_task_event(data)
                    elif message_type == "worker":
                        await self._broadcast_worker_event(data)
                    elif message_type == "progress":
                        await self._broadcast_progress_event(data)

                except TimeoutError:
                    continue

            except Exception as e:
                logger.error("Error in background broadcaster: %s", e, exc_info=True)
                await asyncio.sleep(0.1)

    async def stop_background_broadcaster(self):
        self._running = False
        if self._broadcast_task:
            self._broadcast_task.cancel()
            try:
                await self._broadcast_task
            except asyncio.CancelledError:
                pass
            self._broadcast_task = None
        self.message_queue = None

    def register_accepted(self, websocket: WebSocket) -> None:
        """Register a WebSocket that has already been accepted (e.g. during first-message auth)."""
        self.active_connections.append(websocket)
        self.client_filters[websocket] = {}
        self.client_modes[websocket] = "live"
        self.client_environments[websocket] = None
        logger.info("Client connected (pre-accepted). Total connections: %s", len(self.active_connections))
        if len(self.active_connections) == 1:
            self.start_background_broadcaster()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        self.client_filters[websocket] = {}
        self.client_modes[websocket] = "live"
        self.client_environments[websocket] = None
        logger.info("Client connected. Total connections: %s", len(self.active_connections))

        if len(self.active_connections) == 1:
            self.start_background_broadcaster()

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if websocket in self.client_filters:
            del self.client_filters[websocket]
        if websocket in self.client_modes:
            del self.client_modes[websocket]
        if websocket in self.client_environments:
            del self.client_environments[websocket]
        logger.info("Client disconnected. Total connections: %s", len(self.active_connections))

    def queue_broadcast(self, task_event: TaskEvent):
        self._queue_event("task", task_event)

    def queue_worker_broadcast(self, worker_event: WorkerEvent):
        self._queue_event("worker", worker_event)

    def queue_progress_broadcast(self, progress_event):
        self._queue_event("progress", progress_event)

    def _queue_event(self, event_type: str, event):
        if self.active_connections and self._loop and self.message_queue:
            try:
                self._loop.call_soon_threadsafe(self.message_queue.put_nowait, (event_type, event))
            except Exception as e:
                logger.error("Error queuing %s event: %s", event_type, e, exc_info=True)

    async def _broadcast_task_event(self, task_event: TaskEvent):
        await self._broadcast_event(task_event, check_filters=True)

    async def _broadcast_worker_event(self, worker_event: WorkerEvent):
        await self._broadcast_event(worker_event, check_filters=False)

    async def _broadcast_progress_event(self, progress_event):
        await self._broadcast_event(progress_event, check_filters=True)

    async def _broadcast_event(self, event, check_filters: bool):
        if not self.active_connections:
            return

        message = event.model_dump_json()
        disconnected = []

        for connection in self.active_connections:
            try:
                if self.client_modes.get(connection, "live") != "live":
                    continue

                if check_filters:
                    filters = self.client_filters.get(connection, {})
                    if not self._should_send_to_client(event, filters):
                        continue

                    env = self.client_environments.get(connection)
                    if not self._matches_environment(event, env):
                        continue

                await connection.send_text(message)
            except Exception as e:
                logger.error("Error broadcasting to client: %s", e)
                disconnected.append(connection)

        for connection in disconnected:
            self.disconnect(connection)

    def _should_send_to_client(self, task_event: TaskEvent, filters: dict) -> bool:
        if not filters:
            return True

        event_types = filters.get("event_types", [])
        if event_types and task_event.event_type not in event_types:
            return False

        task_names = filters.get("task_names", [])
        if task_names and task_event.task_name not in task_names:
            return False

        return True

    def set_client_filters(self, websocket: WebSocket, filters: dict):
        self.client_filters[websocket] = filters

    def set_client_mode(self, websocket: WebSocket, mode: str):
        if mode in ["live", "static"]:
            self.client_modes[websocket] = mode
            logger.info("Client mode set to: %s", mode)

    def set_client_environment(self, websocket: WebSocket, queue_patterns: list[str], worker_patterns: list[str]):
        """Register environment filter patterns for a client connection."""
        self.client_environments[websocket] = {
            "queue_patterns": queue_patterns,
            "worker_patterns": worker_patterns,
        }

    def _matches_environment(self, event, env: dict | None) -> bool:
        """Return True if the event matches the environment filter patterns.

        When no environment is registered (env is None), all events pass through.
        An event must satisfy both queue_patterns and worker_patterns (if non-empty).
        """
        if not env:
            return True

        queue_patterns = env.get("queue_patterns") or []
        worker_patterns = env.get("worker_patterns") or []

        if queue_patterns:
            event_queue = getattr(event, "queue", None) or ""
            if not any(fnmatch.fnmatch(event_queue, p) for p in queue_patterns):
                return False

        if worker_patterns:
            event_hostname = getattr(event, "hostname", None) or ""
            if not any(fnmatch.fnmatch(event_hostname, p) for p in worker_patterns):
                return False

        return True

    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.error("Error sending message to client: %s", e)
            self.disconnect(websocket)
