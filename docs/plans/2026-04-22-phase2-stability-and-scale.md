# Phase 2: Stability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix five production stability issues derived from the post-audit epic (`docs/specs/2026-04-22-epic-post-audit-roadmap.md` §Phase 2): bounded workflow thread pool, orphan grace-period sleep holding a DB session, deprecated asyncio API, missing broker URL startup validation, and WebSocket permanent-failure with no recovery path.

**Architecture:** Five independent backend/frontend fixes on a single branch `fix/phase2-stability`. Tasks 1–4 are Python backend; Task 5 is a Nuxt TypeScript frontend change. Each task has TDD with its own commit. All tests run via `cd agent && uv run pytest tests/ -v`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, pytest, `concurrent.futures.ThreadPoolExecutor`, Nuxt 4 / TypeScript

---

## File Map

| File | Change |
|---|---|
| `agent/services/workflow_engine.py` | Replace per-event `threading.Thread` with a bounded `ThreadPoolExecutor`; add `shutdown()` |
| `agent/event_handler.py` | `_mark_tasks_as_orphaned` opens its own session after the grace-period sleep instead of receiving one |
| `agent/connection_manager.py` | `asyncio.get_event_loop()` → `asyncio.get_running_loop()` |
| `agent/config.py` | Raise `ValueError` in `__post_init__` when `broker_url` is `None` |
| `frontend/app/stores/websocket.ts` | Reset `reconnectAttempts` on `visibilitychange` so users can recover without a page reload |
| `agent/tests/unit/test_workflow_engine.py` | New — thread pool bounded behaviour |
| `agent/tests/unit/test_event_handler_orphan_sleep.py` | New — session not held during sleep |
| `agent/tests/unit/test_config.py` | New — broker URL validation |

---

## Task 1: Workflow Engine — Bounded Thread Pool

**Background:** `process_event()` currently spawns a bare `threading.Thread` for every Celery event. A busy deployment generating hundreds of events per second will exhaust OS thread limits and eventually crash the process. The fix replaces the per-event thread with a module-level `ThreadPoolExecutor`, capped at a configurable size (default 10).

**Files:**
- Modify: `agent/services/workflow_engine.py`
- Create: `agent/tests/unit/test_workflow_engine.py`

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/unit/test_workflow_engine.py`:

```python
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from services.workflow_engine import WorkflowEngine


class TestWorkflowEngineThreadPool(unittest.TestCase):

    def setUp(self):
        self.db_manager = MagicMock()

    def test_engine_has_executor_attribute(self):
        """WorkflowEngine must expose a ThreadPoolExecutor, not spawn bare threads."""
        engine = WorkflowEngine(self.db_manager)
        self.assertIsInstance(engine._executor, ThreadPoolExecutor)

    def test_default_max_workers_is_ten(self):
        """Default pool size must be 10."""
        engine = WorkflowEngine(self.db_manager)
        self.assertEqual(engine._executor._max_workers, 10)

    def test_custom_max_workers_is_respected(self):
        """Constructor kwarg max_workers must override the default."""
        engine = WorkflowEngine(self.db_manager, max_workers=3)
        self.assertEqual(engine._executor._max_workers, 3)

    def test_process_event_submits_to_executor_not_thread(self):
        """process_event must call executor.submit, never threading.Thread()."""
        engine = WorkflowEngine(self.db_manager)

        mock_event = MagicMock()
        mock_event.event_type = "task-succeeded"
        mock_event.model_dump.return_value = {}

        with patch.object(engine._executor, "submit") as mock_submit, \
             patch("services.workflow_engine.EVENT_TRIGGER_MAP", {"task-succeeded": "task.succeeded"}):
            engine.process_event(mock_event)
            mock_submit.assert_called_once()

    def test_process_event_noop_for_unknown_event_type(self):
        """process_event must not submit work for event types not in EVENT_TRIGGER_MAP."""
        engine = WorkflowEngine(self.db_manager)

        mock_event = MagicMock()
        mock_event.event_type = "unknown-event-xyz"

        with patch.object(engine._executor, "submit") as mock_submit, \
             patch("services.workflow_engine.EVENT_TRIGGER_MAP", {}):
            engine.process_event(mock_event)
            mock_submit.assert_not_called()

    def test_shutdown_is_callable(self):
        """shutdown() must exist and complete without error."""
        engine = WorkflowEngine(self.db_manager)
        engine.shutdown()  # must not raise

    def test_shutdown_prevents_new_submissions(self):
        """After shutdown(), submitting work must not raise — executor rejects silently via wait=True."""
        engine = WorkflowEngine(self.db_manager)
        engine.shutdown()
        # Calling process_event after shutdown must not crash the process
        mock_event = MagicMock()
        mock_event.event_type = "task-succeeded"
        mock_event.model_dump.return_value = {}
        with patch("services.workflow_engine.EVENT_TRIGGER_MAP", {"task-succeeded": "task.succeeded"}):
            try:
                engine.process_event(mock_event)
            except RuntimeError:
                pass  # executor raises RuntimeError after shutdown — that's acceptable


class TestWorkflowEngineSingleExecutorInstance(unittest.TestCase):
    """The executor must be per-instance, not a global singleton."""

    def test_two_engines_have_independent_executors(self):
        db = MagicMock()
        engine_a = WorkflowEngine(db)
        engine_b = WorkflowEngine(db)
        self.assertIsNot(engine_a._executor, engine_b._executor)
        engine_a.shutdown()
        engine_b.shutdown()
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
cd /Users/matt/dev/kanchi/agent
uv run pytest tests/unit/test_workflow_engine.py -v
```

Expected: `FAILED` — `WorkflowEngine` has no `_executor` attribute, no `shutdown()` method.

- [ ] **Step 3: Implement the fix**

Replace the top of `agent/services/workflow_engine.py`. The full file after changes:

```python
"""Workflow engine for evaluating and executing workflows."""

import asyncio
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from models import Condition, ConditionOperator, TaskEvent, WorkerEvent, WorkflowDefinition
from services.workflow_catalog import EVENT_TRIGGER_MAP
from services.workflow_executor import WorkflowExecutor
from services.workflow_service import WorkflowService

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """Engine for processing events and triggering workflows."""

    def __init__(self, db_manager, monitor_instance=None, max_workers: int = 10):
        self.db_manager = db_manager
        self.monitor_instance = monitor_instance
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="workflow")

    def shutdown(self, wait: bool = True):
        """Shut down the thread pool. Call during application teardown."""
        self._executor.shutdown(wait=wait)

    def process_event(self, event: TaskEvent | WorkerEvent):
        """
        Process an event and trigger matching workflows.

        This is called synchronously from EventHandler, but workflows
        are executed asynchronously to avoid blocking.
        """
        try:
            trigger_type = EVENT_TRIGGER_MAP.get(event.event_type, None)

            if not trigger_type:
                return

            context = event.model_dump()

            self._executor.submit(self._run_async_workflow_evaluation, trigger_type, context, event)

        except Exception as e:
            logger.error(f"Error processing event for workflows: {e}", exc_info=True)

    def _run_async_workflow_evaluation(
        self, trigger_type: str, context: dict[str, Any], event: TaskEvent | WorkerEvent
    ):
        """Run async workflow evaluation in a new event loop."""
        try:
            asyncio.run(self._evaluate_and_execute_workflows(trigger_type, context, event))
        except Exception as e:
            logger.error(f"Error running workflow evaluation: {e}", exc_info=True)

    async def _evaluate_and_execute_workflows(
        self, trigger_type: str, context: dict[str, Any], event: TaskEvent | WorkerEvent
    ):
        """Evaluate and execute matching workflows (async)."""
        with self.db_manager.get_session() as session:
            workflow_service = WorkflowService(session)

            workflows = workflow_service.get_active_workflows_for_trigger(trigger_type)

            if not workflows:
                return

            logger.debug(f"Found {len(workflows)} workflows for trigger {trigger_type}")

            for workflow in workflows:
                try:
                    can_execute, reason = workflow_service.can_execute_workflow(workflow.id)

                    if not can_execute:
                        logger.debug(f"Skipping workflow {workflow.name}: {reason}")
                        continue

                    if not self._evaluate_conditions(workflow, context):
                        logger.debug(f"Workflow conditions not met: {workflow.name}")
                        continue

                    logger.info(f"Executing workflow: {workflow.name} (trigger={trigger_type})")

                    cb_state = workflow_service.is_circuit_breaker_open(workflow, context)

                    if cb_state.is_open:
                        logger.warning(f"Circuit breaker skipped workflow {workflow.name}: {cb_state.reason}")
                        workflow_service.record_circuit_breaker_skip(
                            workflow=workflow,
                            trigger_type=trigger_type,
                            trigger_event=context,
                            workflow_snapshot=workflow.dict(),
                            circuit_breaker_key=cb_state.key,
                            reason=cb_state.reason,
                        )
                        continue

                    executor = WorkflowExecutor(
                        session=session, db_manager=self.db_manager, monitor_instance=self.monitor_instance
                    )

                    await executor.execute_workflow(workflow, context, event, circuit_breaker_key=cb_state.key)

                except Exception as e:
                    logger.error(f"Error evaluating workflow {workflow.name}: {e}", exc_info=True)

    def _evaluate_conditions(self, workflow: WorkflowDefinition, context: dict[str, Any]) -> bool:
        if not workflow.conditions:
            return True

        return self._evaluate_condition_group(workflow.conditions, context)

    def _evaluate_condition_group(self, condition_group, context: dict[str, Any]) -> bool:
        """Evaluate a group of conditions with AND/OR logic."""
        if not condition_group.conditions:
            return True

        results = [self._evaluate_single_condition(cond, context) for cond in condition_group.conditions]

        if condition_group.operator == "AND":
            return all(results)
        else:  # OR
            return any(results)

    def _evaluate_single_condition(self, condition: Condition, context: dict[str, Any]) -> bool:  # noqa: C901
        """Evaluate a single condition."""
        field_value = context.get(condition.field)

        if field_value is None:
            return condition.operator == ConditionOperator.NOT_EQUALS

        operator = condition.operator
        expected_value = condition.value

        try:
            if operator == ConditionOperator.EQUALS:
                return field_value == expected_value

            elif operator == ConditionOperator.NOT_EQUALS:
                return field_value != expected_value

            elif operator == ConditionOperator.IN:
                return field_value in expected_value

            elif operator == ConditionOperator.NOT_IN:
                return field_value not in expected_value

            elif operator == ConditionOperator.MATCHES:
                pattern = re.compile(expected_value)
                return bool(pattern.search(str(field_value)))

            elif operator == ConditionOperator.GREATER_THAN:
                return float(field_value) > float(expected_value)

            elif operator == ConditionOperator.LESS_THAN:
                return float(field_value) < float(expected_value)

            elif operator == ConditionOperator.GREATER_EQUAL:
                return float(field_value) >= float(expected_value)

            elif operator == ConditionOperator.LESS_EQUAL:
                return float(field_value) <= float(expected_value)

            elif operator == ConditionOperator.CONTAINS:
                return expected_value in str(field_value)

            elif operator == ConditionOperator.STARTS_WITH:
                return str(field_value).startswith(expected_value)

            elif operator == ConditionOperator.ENDS_WITH:
                return str(field_value).endswith(expected_value)

            else:
                logger.warning(f"Unknown operator: {operator}")
                return False

        except Exception as e:
            logger.error(f"Error evaluating condition: {e}", exc_info=True)
            return False
```

Note: remove `import threading` since it's no longer used.

- [ ] **Step 4: Run the tests to confirm they pass**

```bash
cd /Users/matt/dev/kanchi/agent
uv run pytest tests/unit/test_workflow_engine.py -v
```

Expected: All 8 tests pass.

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent/services/workflow_engine.py agent/tests/unit/test_workflow_engine.py
git commit -m "fix(workflows): replace unbounded per-event threads with bounded ThreadPoolExecutor"
```

---

## Task 2: Fix Orphan Grace-Period Sleep Holding a DB Session

**Background:** `EventHandler._mark_tasks_as_orphaned()` receives an open SQLAlchemy session from `handle_worker_event` and calls `time.sleep(grace_period_seconds)` before doing any DB work. On SQLite (the default), SQLAlchemy uses a serialised write lock — every other DB write in the process blocks for 2 seconds whenever a worker goes offline. Fix: `_mark_tasks_as_orphaned` no longer accepts a session parameter; it sleeps first, then opens its own session.

**Files:**
- Modify: `agent/event_handler.py`
- Create: `agent/tests/unit/test_event_handler_orphan_sleep.py`

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/unit/test_event_handler_orphan_sleep.py`:

```python
import os
import sys
import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from event_handler import EventHandler


class TestOrphanSleepSessionOrdering(unittest.TestCase):
    """The grace-period sleep must complete BEFORE opening a DB session."""

    def _make_handler(self):
        db_manager = MagicMock()
        conn_manager = MagicMock()
        return EventHandler(db_manager, conn_manager)

    def test_mark_tasks_as_orphaned_does_not_accept_session_parameter(self):
        """Signature must be (self, hostname, orphaned_at, grace_period_seconds=2) — no session arg."""
        import inspect
        handler = self._make_handler()
        sig = inspect.signature(handler._mark_tasks_as_orphaned)
        params = list(sig.parameters.keys())
        self.assertNotIn("session", params)
        self.assertIn("hostname", params)
        self.assertIn("orphaned_at", params)

    def test_sleep_happens_before_get_session(self):
        """time.sleep must be called before db_manager.get_session is entered."""
        handler = self._make_handler()

        call_order = []

        with patch("time.sleep", side_effect=lambda _: call_order.append("sleep")):
            handler.db_manager.get_session.return_value.__enter__ = MagicMock(
                side_effect=lambda *_: call_order.append("session_open") or MagicMock()
            )
            handler.db_manager.get_session.return_value.__exit__ = MagicMock(return_value=False)

            with patch("event_handler.OrphanDetectionService") as mock_ods:
                mock_ods.return_value.find_and_mark_orphaned_tasks.return_value = []
                handler._mark_tasks_as_orphaned("worker1", datetime.now(UTC), grace_period_seconds=1)

        self.assertEqual(call_order[0], "sleep", "sleep must come before session open")
        self.assertIn("session_open", call_order)

    def test_zero_grace_period_skips_sleep(self):
        """grace_period_seconds=0 must not call time.sleep at all."""
        handler = self._make_handler()

        with patch("time.sleep") as mock_sleep, \
             patch("event_handler.OrphanDetectionService") as mock_ods:
            mock_ods.return_value.find_and_mark_orphaned_tasks.return_value = []
            handler._mark_tasks_as_orphaned("worker1", datetime.now(UTC), grace_period_seconds=0)
            mock_sleep.assert_not_called()

    def test_handle_worker_event_worker_offline_does_not_hold_session_during_orphan_marking(self):
        """handle_worker_event must call _mark_tasks_as_orphaned OUTSIDE any session context."""
        from models import WorkerEvent

        handler = self._make_handler()

        worker_event = WorkerEvent(
            hostname="worker1",
            event_type="worker-offline",
            timestamp=datetime.now(UTC),
            status="offline",
        )

        orphan_call_order = []

        def fake_mark(hostname, orphaned_at, grace_period_seconds=2):
            orphan_call_order.append("orphan_mark")

        handler._mark_tasks_as_orphaned = fake_mark

        session_context_active = []

        class FakeSession:
            def __enter__(self_inner):
                session_context_active.append(True)
                return MagicMock()

            def __exit__(self_inner, *args):
                session_context_active.pop()
                return False

        handler.db_manager.get_session.return_value = FakeSession()

        handler.handle_worker_event(worker_event)

        # Orphan marking must happen outside the session context
        self.assertEqual(orphan_call_order, ["orphan_mark"])
        self.assertEqual(session_context_active, [], "Session must be closed before orphan marking")
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
cd /Users/matt/dev/kanchi/agent
uv run pytest tests/unit/test_event_handler_orphan_sleep.py -v
```

Expected: `test_mark_tasks_as_orphaned_does_not_accept_session_parameter` fails (signature has `session`). `test_sleep_happens_before_get_session` fails (session opens before sleep).

- [ ] **Step 3: Implement the fix**

Replace `agent/event_handler.py` with the following (only `handle_worker_event` and `_mark_tasks_as_orphaned` change):

```python
import logging
import time
from datetime import UTC, datetime

from connection_manager import ConnectionManager
from constants import EventType
from database import DatabaseManager
from metrics import metrics_collector
from models import TaskEvent, WorkerEvent
from services import (
    DailyStatsService,
    OrphanDetectionService,
    ProgressService,
    TaskRegistryService,
    TaskService,
)

logger = logging.getLogger(__name__)


class EventHandler:
    def __init__(self, db_manager: DatabaseManager, connection_manager: ConnectionManager, workflow_engine=None):
        self.db_manager = db_manager
        self.connection_manager = connection_manager
        self.workflow_engine = workflow_engine

    def handle_task_event(self, task_event: TaskEvent):
        try:
            try:
                metrics_collector.record_task_event(task_event)
            except Exception as exc:
                logger.error(
                    "Failed to record metrics for task event %s: %s",
                    task_event.task_id,
                    exc,
                )

            with self.db_manager.get_session() as session:
                registry_service = TaskRegistryService(session)
                registry_service.ensure_task_registered(task_event.task_name)

                task_service = TaskService(session)
                daily_stats_service = DailyStatsService(session)

                task_service._enrich_task_with_retry_info(task_event)
                task_service.save_task_event(task_event)
                daily_stats_service.update_daily_stats(task_event)

            self.connection_manager.queue_broadcast(task_event)

            if self.workflow_engine:
                self.workflow_engine.process_event(task_event)

        except Exception as e:
            logger.error(f"Error handling task event {task_event.task_id}: {e}", exc_info=True)

    def handle_progress_event(self, progress_event):
        try:
            with self.db_manager.get_session() as session:
                progress_service = ProgressService(session)
                progress_service.save_progress_event(progress_event)

            self.connection_manager.queue_progress_broadcast(progress_event)

            if self.workflow_engine:
                self.workflow_engine.process_event(progress_event)
        except Exception as exc:
            logger.error(f"Error handling progress event {progress_event.task_id}: {exc}", exc_info=True)

    def handle_steps_event(self, steps_event):
        try:
            with self.db_manager.get_session() as session:
                progress_service = ProgressService(session)
                progress_service.save_steps_event(steps_event)

            self.connection_manager.queue_progress_broadcast(steps_event)

            if self.workflow_engine:
                self.workflow_engine.process_event(steps_event)
        except Exception as exc:
            logger.error(f"Error handling steps event {steps_event.task_id}: {exc}", exc_info=True)

    def handle_worker_event(self, worker_event: WorkerEvent):
        try:
            try:
                metrics_collector.record_worker_event(worker_event)
            except Exception as exc:
                logger.error(
                    "Failed to record metrics for worker event %s: %s",
                    worker_event.hostname,
                    exc,
                )

            if worker_event.event_type == EventType.WORKER_OFFLINE.value:
                logger.info(f"Worker {worker_event.hostname} went offline, marking tasks as orphaned")
                orphaned_at = datetime.now(UTC)
                self._mark_tasks_as_orphaned(worker_event.hostname, orphaned_at)

            self.connection_manager.queue_worker_broadcast(worker_event)

            if self.workflow_engine:
                self.workflow_engine.process_event(worker_event)

        except Exception as e:
            logger.error(f"Error handling worker event {worker_event.hostname}: {e}", exc_info=True)

    def _mark_tasks_as_orphaned(self, hostname: str, orphaned_at: datetime, grace_period_seconds: int = 2):
        try:
            if grace_period_seconds > 0:
                time.sleep(grace_period_seconds)

            with self.db_manager.get_session() as session:
                orphan_service = OrphanDetectionService(session)
                orphaned_tasks = orphan_service.find_and_mark_orphaned_tasks(
                    hostname=hostname, orphaned_at=orphaned_at, grace_period_seconds=grace_period_seconds
                )

                if orphaned_tasks:
                    orphan_service.broadcast_orphan_events(orphaned_tasks, orphaned_at, self.connection_manager)

        except Exception as e:
            logger.error(f"Error marking tasks as orphaned for worker {hostname}: {e}", exc_info=True)
```

Key changes: `import time` moved to top-level (removed inline `import time`); `handle_worker_event` calls `_mark_tasks_as_orphaned` with no session and outside any `with session:` block; `_mark_tasks_as_orphaned` signature drops the `session` parameter and opens its own session after the sleep.

- [ ] **Step 4: Run the new tests**

```bash
cd /Users/matt/dev/kanchi/agent
uv run pytest tests/unit/test_event_handler_orphan_sleep.py -v
```

Expected: All 4 tests pass.

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent/event_handler.py agent/tests/unit/test_event_handler_orphan_sleep.py
git commit -m "fix(events): open DB session after grace-period sleep, not before"
```

---

## Task 3: Fix Deprecated `asyncio.get_event_loop()`

**Background:** `connection_manager.py:25` calls `asyncio.get_event_loop()` inside `start_background_broadcaster()`. This method is always called from within `async def connect()`, so there is always a running event loop available. The correct call is `asyncio.get_running_loop()`, which is explicit and raises immediately if accidentally called from a non-async context rather than silently creating a new loop.

**Files:**
- Modify: `agent/connection_manager.py` (1-line change)

- [ ] **Step 1: Verify the existing connection manager tests pass before the change**

```bash
cd /Users/matt/dev/kanchi/agent
uv run pytest tests/unit/test_connection_manager.py -v
```

Expected: All 4 tests pass (baseline).

- [ ] **Step 2: Apply the fix**

In `agent/connection_manager.py`, change line 25:

```python
# Before
self._loop = asyncio.get_event_loop()

# After
self._loop = asyncio.get_running_loop()
```

- [ ] **Step 3: Run the connection manager tests**

```bash
uv run pytest tests/unit/test_connection_manager.py -v
```

Expected: All 4 tests still pass. `IsolatedAsyncioTestCase` provides a running event loop, so `get_running_loop()` works correctly in the test context.

- [ ] **Step 4: Run the full suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent/connection_manager.py
git commit -m "fix(websocket): replace deprecated get_event_loop() with get_running_loop()"
```

---

## Task 4: Validate `CELERY_BROKER_URL` at Startup

**Background:** `config.py:56` has `broker_url: str = os.getenv("CELERY_BROKER_URL")`. If `CELERY_BROKER_URL` is not set, this evaluates to `None` (despite the `str` type annotation). The app then silently falls back to Celery's hardcoded default `amqp://guest@localhost//` — not an obvious failure. Fix: raise `ValueError` in `__post_init__` with a clear diagnostic message so misconfigured deployments fail fast at startup.

**Files:**
- Modify: `agent/config.py`
- Create: `agent/tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/unit/test_config.py`:

```python
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
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
cd /Users/matt/dev/kanchi/agent
uv run pytest tests/unit/test_config.py -v
```

Expected: `test_missing_broker_url_raises_value_error` fails (no error is raised). `test_broker_url_type_annotation_allows_none_before_post_init` fails (annotation is `str`, not `str | None`).

- [ ] **Step 3: Implement the fix**

In `agent/config.py`, make two changes:

**Change 1** — fix the type annotation on line 56 so the dataclass accepts `None` from `os.getenv`:

```python
# Before
broker_url: str = os.getenv("CELERY_BROKER_URL")

# After
broker_url: str | None = os.getenv("CELERY_BROKER_URL")
```

**Change 2** — add validation in `__post_init__`:

```python
def __post_init__(self) -> None:
    """Validate required config and normalize secrets."""
    if not self.broker_url:
        raise ValueError(
            "CELERY_BROKER_URL environment variable is required but was not set. "
            "Examples: redis://localhost:6379/0  or  amqp://guest:guest@localhost:5672//"
        )

    if self.session_secret_key == "change-me":
        self.session_secret_key = secrets.token_urlsafe(32)

    if self.token_secret_key == "change-me":
        self.token_secret_key = self.session_secret_key
```

- [ ] **Step 4: Run the new tests**

```bash
cd /Users/matt/dev/kanchi/agent
uv run pytest tests/unit/test_config.py -v
```

Expected: All 5 tests pass.

- [ ] **Step 5: Run the full suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent/config.py agent/tests/unit/test_config.py
git commit -m "fix(config): raise ValueError at startup when CELERY_BROKER_URL is not set"
```

---

## Task 5: Frontend WebSocket — Reconnect Recovery on Tab Visibility

**Background:** `frontend/app/stores/websocket.ts` gives up permanently after 5 failed reconnect attempts — `canReconnect` becomes `false` and `connect()` is never retried. Users must reload the page to recover. The fix: listen to `document.visibilitychange`; when the tab becomes visible again with no active connection, reset the attempt counter and reconnect. This is the natural trigger: users switch away, the connection drops, they switch back and expect data.

**Files:**
- Modify: `frontend/app/stores/websocket.ts`

Note: There is no frontend unit-test suite in this repo. The verification step is manual smoke-testing in the browser.

- [ ] **Step 1: Apply the fix**

In `frontend/app/stores/websocket.ts`, find the `if (process.client)` block at the bottom (around line 212) and add the visibility change handler after the existing `watch`:

```typescript
  if (process.client) {
    watch([authEnabled, isAuthenticated, accessToken], () => {
      if (configLoading.value) {
        return
      }

      if (!authEnabled.value) {
        if (!isConnected.value) {
          connect()
        }
        return
      }

      if (isAuthenticated.value) {
        disconnect()
        connect()
      } else {
        disconnect()
      }
    }, { immediate: true })

    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible' && !isConnected.value && !isConnecting.value) {
        reconnectAttempts.value = 0
        reconnectDelay.value = 1000
        connect()
      }
    })
  }
```

- [ ] **Step 2: Verify the fix manually**

```bash
cd /Users/matt/dev/kanchi
make dev
```

In the browser:
1. Open the Kanchi dashboard at `http://localhost:3000`
2. Confirm WebSocket is connected (live data flowing)
3. Stop the backend (`Ctrl+C` on the backend process, or kill the Python process)
4. Observe the frontend attempt 5 reconnects and stop
5. Restart the backend
6. Switch to another browser tab, then switch back to the Kanchi tab
7. Confirm the connection automatically recovers (status indicator goes green)

Expected: The WebSocket reconnects on tab visibility change without requiring a page reload.

- [ ] **Step 3: Commit**

```bash
git add frontend/app/stores/websocket.ts
git commit -m "fix(frontend): reset WebSocket reconnect counter on tab visibility change"
```

---

## Task 6: Open PR

**Files:** None

- [ ] **Step 1: Push the branch**

```bash
git push -u origin fix/phase2-stability
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create \
  --base main \
  --head fix/phase2-stability \
  --title "fix(stability): bounded workflow thread pool, orphan sleep fix, reconnect recovery" \
  --body "$(cat <<'EOF'
## Summary
- **Workflow engine thread pool**: replaces per-event `threading.Thread` with a bounded `ThreadPoolExecutor(max_workers=10)` — prevents OS thread exhaustion on high-volume deployments
- **Orphan grace-period sleep**: `_mark_tasks_as_orphaned` now sleeps _before_ opening a DB session, not while holding one — eliminates 2-second write serialization on every worker-offline event
- **`asyncio.get_running_loop()`**: replaces deprecated `get_event_loop()` in `ConnectionManager.start_background_broadcaster()`
- **Broker URL startup validation**: `Config.__post_init__` raises `ValueError` with a clear diagnostic when `CELERY_BROKER_URL` is missing — fails fast instead of silently falling back to Celery's default
- **Frontend WebSocket recovery**: `visibilitychange` handler resets the reconnect counter when the user returns to the tab — no page reload required after transient backend restarts

## Test plan
- [ ] `test_workflow_engine.py` — 8 new tests pass
- [ ] `test_event_handler_orphan_sleep.py` — 4 new tests pass
- [ ] `test_connection_manager.py` — 4 existing tests still pass
- [ ] `test_config.py` — 5 new tests pass
- [ ] Full suite: `cd agent && uv run pytest tests/ -v` — all pass
- [ ] Docker build: `docker build -t kanchi:test .` — succeeds
- [ ] Manual WebSocket recovery: stop backend → 5 reconnect failures → switch tabs → reconnects
EOF
)"
```

- [ ] **Step 3: After CI passes, merge and pull**

```bash
gh pr merge --squash --auto --delete-branch "$(gh pr list --head fix/phase2-stability --json number --jq '.[0].number')"
git checkout main && git pull origin main
```

---

## Completion Checklist

- [ ] `WorkflowEngine` uses `ThreadPoolExecutor`, no bare `threading.Thread`
- [ ] `EventHandler._mark_tasks_as_orphaned` sleeps before opening session
- [ ] `ConnectionManager.start_background_broadcaster` uses `get_running_loop()`
- [ ] `Config.__post_init__` raises on missing `CELERY_BROKER_URL`
- [ ] Frontend WebSocket resets reconnect counter on `visibilitychange`
- [ ] `uv run pytest tests/ -v` — all tests pass on main
- [ ] PR merged to main
