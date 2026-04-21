# Phase 1: Merge & Active Breakage — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Merge two reviewed branches and fix four bugs that are broken in the ZB production deployment right now.

**Architecture:** Three PRs in sequence. PR1 and PR2 are existing branches already reviewed and patched. PR3 is a new branch (`fix/phase1-active-bugs`) branched from main after PR2 merges, containing four targeted bug fixes each with TDD.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, pytest, uv — run all tests with `cd agent && uv run pytest tests/ -v`

---

## File Map

| File | Change |
|---|---|
| `agent/services/task_service.py` | Replace SQLite-only datetime with Python-computed timedelta (Item 3) |
| `agent/services/orphan_detection_service.py` | Add `TaskLatestDB` bulk update after `TaskEventDB` update (Item 4) |
| `agent/connection_manager.py` | Extend broadcaster guard to check `task.done()` + reset queue on stop (Item 5) |
| `agent/services/daily_stats_service.py` | Replace avg formula with Welford's method (Item 6) |
| `agent/tests/unit/test_task_summary_stats.py` | New — tests for Items 3 |
| `agent/tests/unit/test_orphan_detection.py` | Extend — add task_latest assertions (Item 4) |
| `agent/tests/unit/test_connection_manager.py` | New — tests for Item 5 |
| `agent/tests/unit/test_daily_stats_service.py` | Extend — add precise avg_runtime test (Item 6) |

---

## Task 1: Open PR 1 — Monitor Reconnect Branch

**Branch:** `fix/monitor-reconnect-on-broker-disconnect`

- [ ] **Step 1: Verify tests pass on the branch**

```bash
git checkout fix/monitor-reconnect-on-broker-disconnect
cd agent && uv run pytest tests/unit/test_monitor_reconnect.py -v
```

Expected: 9 tests pass.

- [ ] **Step 2: Open the PR**

```bash
gh pr create \
  --base main \
  --head fix/monitor-reconnect-on-broker-disconnect \
  --title "fix(monitor): reconnect on broker disconnect with exponential backoff" \
  --body "$(cat <<'EOF'
## Summary
- Wraps `recv.capture()` in an exponential backoff retry loop (1s → 60s cap)
- Uses `threading.Event.wait()` instead of `time.sleep()` so `stop()` interrupts the sleep immediately — no more 60s shutdown hang
- Resets `celery.events.State` on each reconnect to avoid stale task tracking

## Test plan
- [ ] 9 unit tests in `agent/tests/unit/test_monitor_reconnect.py` all pass
- [ ] Verify manually: stop Redis, observe reconnect attempts in logs, restart Redis, observe recovery
EOF
)"
```

- [ ] **Step 3: Merge the PR**

Once CI passes (or manually review + merge via GitHub UI), merge to main.

```bash
git checkout main && git pull origin main
```

---

## Task 2: Open PR 2 — Perf / N+1 Fix Branch

**Branch:** `strayer.perf-fix-orphaned-count-queries`

- [ ] **Step 1: Verify tests pass on the branch**

```bash
git checkout strayer.perf-fix-orphaned-count-queries
cd agent && uv run pytest tests/unit/test_orphaned_tasks.py tests/unit/test_recent_events_count.py -v
```

Expected: 7 tests pass.

- [ ] **Step 2: Open the PR**

```bash
gh pr create \
  --base main \
  --head strayer.perf-fix-orphaned-count-queries \
  --title "fix(tasks): eliminate N+1 on orphaned tasks and use scalar count for pagination" \
  --body "$(cat <<'EOF'
## Summary
- Eliminates N+1 on `GET /api/tasks/orphaned`: retry relationships now batch-loaded in a single query via `_bulk_enrich_with_retry_info`, filter uses `event.retry_count == 0` from enriched events instead of a second DB round trip
- Replaces `Query.count()` (materializes a subquery) with `query.with_entities(func.count(...)).scalar()` on both paginated endpoints

## Test plan
- [ ] `test_orphaned_tasks.py` — 4 tests pass
- [ ] `test_recent_events_count.py` — 3 tests pass
EOF
)"
```

- [ ] **Step 3: Merge the PR**

Once CI passes, merge to main.

```bash
git checkout main && git pull origin main
```

---

## Task 3: Create PR 3 Branch

- [ ] **Step 1: Create branch from updated main**

```bash
git checkout main
git pull origin main
git checkout -b fix/phase1-active-bugs
```

---

## Task 4: Fix PostgreSQL Datetime Expression

**Bug:** `func.datetime('now', '-1 hour')` in `get_task_summary_stats()` is SQLite-only syntax. On PostgreSQL (ZB production) this returns 0, breaking the "recent activity" counter on the dashboard.

**Files:**
- Modify: `agent/services/task_service.py` (around line 395)
- Create: `agent/tests/unit/test_task_summary_stats.py`

- [ ] **Step 1: Write the failing test**

Create `agent/tests/unit/test_task_summary_stats.py`:

```python
import unittest
from datetime import datetime, timezone, timedelta

from services.task_service import TaskService
from tests.base import DatabaseTestCase


class TestGetTaskSummaryStats(DatabaseTestCase):

    def setUp(self):
        super().setUp()
        self.service = TaskService(self.session)
        self.now = datetime.now(timezone.utc)

    def test_recent_activity_counts_events_within_last_hour(self):
        # Event 30 minutes ago — should be counted
        self.create_task_event_db(
            task_id="recent-1",
            event_type="task-started",
            timestamp=self.now - timedelta(minutes=30),
        )
        # Event 2 hours ago — should NOT be counted
        self.create_task_event_db(
            task_id="old-1",
            event_type="task-started",
            timestamp=self.now - timedelta(hours=2),
        )

        result = self.service.get_task_summary_stats()

        self.assertEqual(result['recent_activity'], 1)

    def test_recent_activity_returns_zero_when_no_events(self):
        result = self.service.get_task_summary_stats()

        self.assertEqual(result['recent_activity'], 0)

    def test_recent_activity_counts_all_events_within_last_hour(self):
        for i in range(5):
            self.create_task_event_db(
                task_id=f"task-{i}",
                event_type="task-started",
                timestamp=self.now - timedelta(minutes=10),
            )

        result = self.service.get_task_summary_stats()

        self.assertEqual(result['recent_activity'], 5)
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd agent && uv run pytest tests/unit/test_task_summary_stats.py -v
```

Expected: Tests fail — `recent_activity` returns 0 even for recent events because the SQLite `func.datetime()` expression does not evaluate correctly on this path when timestamps are timezone-aware Python datetimes.

- [ ] **Step 3: Apply the fix**

In `agent/services/task_service.py`, find `get_task_summary_stats()` and replace the filter line:

```python
# Before — find this block:
recent_activity = (
    self.session.query(func.count(TaskEventDB.id).label('last_hour_events'))
    .filter(TaskEventDB.timestamp >= func.datetime('now', '-1 hour'))
    .scalar()
)

# After — replace with (datetime, timezone, timedelta are already imported at top of file):
recent_activity = (
    self.session.query(func.count(TaskEventDB.id).label('last_hour_events'))
    .filter(TaskEventDB.timestamp >= datetime.now(timezone.utc) - timedelta(hours=1))
    .scalar()
)
```

- [ ] **Step 4: Run the tests to confirm they pass**

```bash
cd agent && uv run pytest tests/unit/test_task_summary_stats.py -v
```

Expected: 3 tests pass.

- [ ] **Step 5: Run the full suite to check for regressions**

```bash
cd agent && uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent/services/task_service.py agent/tests/unit/test_task_summary_stats.py
git commit -m "fix(tasks): replace SQLite-only datetime with dialect-agnostic Python timedelta"
```

---

## Task 5: Fix `task_latest` Orphan Staleness

**Bug:** `_mark_tasks_as_orphaned()` updates `task_events` to set `is_orphan=True` but never updates the `task_latest` snapshot table. The aggregated task list view reads from `task_latest`, so orphaned tasks continue showing `is_orphan=False` in the UI indefinitely.

**Files:**
- Modify: `agent/services/orphan_detection_service.py`
- Modify: `agent/tests/unit/test_orphan_detection.py`

- [ ] **Step 1: Write the failing tests**

Open `agent/tests/unit/test_orphan_detection.py` and add these two test methods to the existing `TestOrphanDetectionService` class:

```python
def test_mark_tasks_as_orphaned_also_updates_task_latest(self):
    """task_latest snapshot must reflect is_orphan=True after orphan detection runs."""
    from database import TaskLatestDB

    # Create a task_event row
    self.create_task_event_db(
        task_id="orphan-latest-1",
        event_type="task-started",
        timestamp=self.base_time,
        hostname="worker1",
    )
    # Create matching task_latest row (normally written by _upsert_task_latest)
    task_latest = TaskLatestDB(
        task_id="orphan-latest-1",
        event_id=1,
        task_name="tasks.example",
        event_type="task-started",
        timestamp=self.base_time,
        hostname="worker1",
        is_orphan=False,
    )
    self.session.add(task_latest)
    self.session.commit()

    orphaned_at = self.base_time + timedelta(seconds=10)
    self.service.find_and_mark_orphaned_tasks(
        hostname="worker1",
        orphaned_at=orphaned_at,
    )

    self.session.expire_all()
    updated = self.session.query(TaskLatestDB).filter_by(task_id="orphan-latest-1").first()
    self.assertIsNotNone(updated)
    self.assertTrue(updated.is_orphan)
    self.assertIsNotNone(updated.orphaned_at)

def test_mark_tasks_as_orphaned_task_latest_missing_row_is_safe(self):
    """If task_latest has no row for a task_id, the bulk update is a silent no-op."""
    self.create_task_event_db(
        task_id="orphan-no-latest",
        event_type="task-started",
        timestamp=self.base_time,
        hostname="worker1",
    )
    # Intentionally do NOT create a task_latest row

    orphaned_at = self.base_time + timedelta(seconds=10)
    # Should not raise
    try:
        self.service.find_and_mark_orphaned_tasks(
            hostname="worker1",
            orphaned_at=orphaned_at,
        )
    except Exception as e:
        self.fail(f"find_and_mark_orphaned_tasks raised unexpectedly: {e}")
```

- [ ] **Step 2: Run the new tests to confirm they fail**

```bash
cd agent && uv run pytest tests/unit/test_orphan_detection.py::TestOrphanDetectionService::test_mark_tasks_as_orphaned_also_updates_task_latest tests/unit/test_orphan_detection.py::TestOrphanDetectionService::test_mark_tasks_as_orphaned_task_latest_missing_row_is_safe -v
```

Expected: First test fails — `updated.is_orphan` is `False`. Second test passes (no-op is already safe).

- [ ] **Step 3: Apply the fix**

In `agent/services/orphan_detection_service.py`:

1. Add `TaskLatestDB` to the import at the top:

```python
# Before:
from database import TaskEventDB

# After:
from database import TaskEventDB, TaskLatestDB
```

2. In `_mark_tasks_as_orphaned()`, add the `task_latest` bulk update after the existing `task_events` update, before the `commit()`:

```python
def _mark_tasks_as_orphaned(
    self,
    orphaned_tasks: List[TaskEventDB],
    orphaned_at: datetime,
    grace_period_seconds: int
):
    task_ids = [task.task_id for task in orphaned_tasks]

    self.session.query(TaskEventDB).filter(
        TaskEventDB.task_id.in_(task_ids)
    ).update({
        'is_orphan': True,
        'orphaned_at': orphaned_at
    }, synchronize_session=False)

    # Mirror update to task_latest snapshot so the aggregated view stays in sync
    self.session.query(TaskLatestDB).filter(
        TaskLatestDB.task_id.in_(task_ids)
    ).update({
        'is_orphan': True,
        'orphaned_at': orphaned_at
    }, synchronize_session=False)

    self.session.commit()

    logger.info(
        f"Marked {len(orphaned_tasks)} tasks as orphaned for offline worker "
        f"(grace period: {grace_period_seconds}s)"
    )
```

- [ ] **Step 4: Run all orphan detection tests**

```bash
cd agent && uv run pytest tests/unit/test_orphan_detection.py -v
```

Expected: All tests pass (the new two plus all existing ones).

- [ ] **Step 5: Run the full suite**

```bash
cd agent && uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent/services/orphan_detection_service.py agent/tests/unit/test_orphan_detection.py
git commit -m "fix(tasks): update task_latest snapshot when marking tasks as orphaned"
```

---

## Task 6: Fix WebSocket Broadcaster Guard

**Bug:** `start_background_broadcaster()` checks `if self._broadcast_task is None`. If the broadcaster coroutine exits unexpectedly (unhandled exception or external cancellation), `_broadcast_task` is a completed `asyncio.Future` — not `None` — so the guard blocks restart even though nothing is broadcasting. Also, `stop_background_broadcaster()` does not reset `message_queue`, so stale queued events from a previous session can bleed into a new connection.

**Files:**
- Modify: `agent/connection_manager.py`
- Create: `agent/tests/unit/test_connection_manager.py`

- [ ] **Step 1: Write the failing tests**

Create `agent/tests/unit/test_connection_manager.py`:

```python
import asyncio
import unittest

from connection_manager import ConnectionManager


class TestConnectionManagerBroadcaster(unittest.IsolatedAsyncioTestCase):

    async def test_broadcaster_restarts_after_task_completes_unexpectedly(self):
        """If _broadcast_task is done (not None), start_background_broadcaster must create a new one."""
        manager = ConnectionManager()

        # Simulate a task that immediately completes (as if it crashed and exited)
        async def noop():
            return

        manager._running = False
        manager._loop = asyncio.get_event_loop()
        manager.message_queue = asyncio.Queue()
        manager._broadcast_task = asyncio.create_task(noop())
        await asyncio.sleep(0)  # let noop() complete so task.done() == True

        self.assertTrue(manager._broadcast_task.done())

        # Should detect done task and start a fresh one
        manager.start_background_broadcaster()

        self.assertIsNotNone(manager._broadcast_task)
        self.assertFalse(manager._broadcast_task.done())

        # Cleanup
        await manager.stop_background_broadcaster()

    async def test_broadcaster_does_not_double_start_when_already_running(self):
        """Calling start_background_broadcaster twice must not create a second task."""
        manager = ConnectionManager()
        manager._loop = asyncio.get_event_loop()
        manager.message_queue = asyncio.Queue()

        manager.start_background_broadcaster()
        first_task = manager._broadcast_task

        manager.start_background_broadcaster()  # second call — should be a no-op
        second_task = manager._broadcast_task

        self.assertIs(first_task, second_task)

        await manager.stop_background_broadcaster()

    async def test_stop_resets_message_queue(self):
        """stop_background_broadcaster must reset message_queue to None."""
        manager = ConnectionManager()
        manager._loop = asyncio.get_event_loop()

        manager.start_background_broadcaster()
        self.assertIsNotNone(manager.message_queue)

        await manager.stop_background_broadcaster()

        self.assertIsNone(manager.message_queue)

    async def test_fresh_queue_created_on_restart(self):
        """After stop + start, message_queue must be a new empty Queue instance."""
        manager = ConnectionManager()
        manager._loop = asyncio.get_event_loop()

        manager.start_background_broadcaster()
        first_queue = manager.message_queue
        await manager.stop_background_broadcaster()

        manager.start_background_broadcaster()
        second_queue = manager.message_queue

        self.assertIsNotNone(second_queue)
        self.assertIsNot(first_queue, second_queue)

        await manager.stop_background_broadcaster()
```

- [ ] **Step 2: Run the failing tests**

```bash
cd agent && uv run pytest tests/unit/test_connection_manager.py -v
```

Expected: `test_broadcaster_restarts_after_task_completes_unexpectedly` fails — the guard blocks restart because `_broadcast_task is not None`. `test_stop_resets_message_queue` fails — queue is not reset to `None`.

- [ ] **Step 3: Apply the fix**

In `agent/connection_manager.py`, make two changes:

**Change 1** — `start_background_broadcaster()`: extend the `None` check to also treat a done/cancelled task as inactive:

```python
def start_background_broadcaster(self):
    task_inactive = self._broadcast_task is None or self._broadcast_task.done()
    if task_inactive and not self._running:
        self._running = True
        self._loop = asyncio.get_event_loop()
        self.message_queue = asyncio.Queue()
        self._broadcast_task = asyncio.create_task(self._background_broadcaster())
        logger.info("Background broadcaster started")
```

**Change 2** — `stop_background_broadcaster()`: reset `message_queue` to `None` after cancellation so the next `start_background_broadcaster()` creates a fresh queue:

```python
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
```

- [ ] **Step 4: Run the new tests**

```bash
cd agent && uv run pytest tests/unit/test_connection_manager.py -v
```

Expected: All 4 tests pass.

- [ ] **Step 5: Run the full suite**

```bash
cd agent && uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add agent/connection_manager.py agent/tests/unit/test_connection_manager.py
git commit -m "fix(websocket): restart broadcaster if task completed unexpectedly, reset queue on stop"
```

---

## Task 7: Fix `avg_runtime` Incremental Average Math

**Bug:** `_update_runtime_stats()` uses `count = stats.succeeded` as the previous sample count. But `stats.succeeded` has already been incremented before this method is called, so `count` is the *new* total — causing the weighted-sum formula to over-weight the existing average. Result: every avg_runtime after the first task is wrong.

**Root cause detail:** When `task-succeeded` arrives, `stats.succeeded += 1` runs first (line ~51), then `_update_runtime_stats()` is called (line ~69). Inside the method, `count = stats.succeeded` is therefore the new count, not the old count. The formula `(avg * count + new) / (count + 1)` then divides by one more than it should.

**Files:**
- Modify: `agent/services/daily_stats_service.py`
- Modify: `agent/tests/unit/test_daily_stats_service.py`

- [ ] **Step 1: Write a precise failing test**

Open `agent/tests/unit/test_daily_stats_service.py` and add this test to `TestDailyStatsService`:

```python
def test_avg_runtime_is_exact_mean_of_three_tasks(self):
    """Welford's method must produce the true mean: (10+20+30)/3 == 20.0."""
    for i, runtime in enumerate([10.0, 20.0, 30.0]):
        self.service.update_daily_stats(self.create_task_event(
            task_id=f"task-avg-{i}",
            task_name="tasks.avg_check",
            event_type="task-succeeded",
            timestamp=self.base_time + timedelta(seconds=i),
            runtime=runtime,
        ))

    stats = self.service.get_stats_for_date("tasks.avg_check", date(2024, 6, 15))
    self.assertAlmostEqual(stats.avg_runtime, 20.0, places=9)

def test_avg_runtime_single_task_equals_its_runtime(self):
    """First task sets avg_runtime directly — no formula involved."""
    self.service.update_daily_stats(self.create_task_event(
        task_id="task-single",
        task_name="tasks.single_check",
        event_type="task-succeeded",
        timestamp=self.base_time,
        runtime=42.5,
    ))

    stats = self.service.get_stats_for_date("tasks.single_check", date(2024, 6, 15))
    self.assertAlmostEqual(stats.avg_runtime, 42.5, places=9)
```

- [ ] **Step 2: Run the new test to confirm it fails**

```bash
cd agent && uv run pytest tests/unit/test_daily_stats_service.py::TestDailyStatsService::test_avg_runtime_is_exact_mean_of_three_tasks -v
```

Expected: FAIL — the buggy formula produces ~17.78 instead of 20.0.

- [ ] **Step 3: Apply the fix**

In `agent/services/daily_stats_service.py`, replace the `_update_runtime_stats` method body:

```python
def _update_runtime_stats(self, stats: TaskDailyStatsDB, runtime: float):
    """
    Update runtime statistics (avg, min, max).

    avg_runtime uses Welford's online algorithm. stats.succeeded has already
    been incremented before this method is called, so it equals the new count n.
    """
    if stats.min_runtime is None or runtime < stats.min_runtime:
        stats.min_runtime = runtime
    if stats.max_runtime is None or runtime > stats.max_runtime:
        stats.max_runtime = runtime

    if stats.avg_runtime is None:
        stats.avg_runtime = runtime
    else:
        n = stats.succeeded  # already the new count (incremented before this call)
        stats.avg_runtime = stats.avg_runtime + (runtime - stats.avg_runtime) / n
```

- [ ] **Step 4: Run the new tests**

```bash
cd agent && uv run pytest tests/unit/test_daily_stats_service.py::TestDailyStatsService::test_avg_runtime_is_exact_mean_of_three_tasks tests/unit/test_daily_stats_service.py::TestDailyStatsService::test_avg_runtime_single_task_equals_its_runtime -v
```

Expected: Both pass.

- [ ] **Step 5: Run the full daily stats test suite**

```bash
cd agent && uv run pytest tests/unit/test_daily_stats_service.py -v
```

Expected: All tests pass. The existing `test_update_daily_stats_calculates_avg_runtime` uses `assertGreater(6.0)` / `assertLess(10.0)` range assertions — the correct result of 8.0 falls within that range, so it passes without modification.

- [ ] **Step 6: Run the full suite**

```bash
cd agent && uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 7: Commit**

```bash
git add agent/services/daily_stats_service.py agent/tests/unit/test_daily_stats_service.py
git commit -m "fix(stats): use Welford's online algorithm for correct avg_runtime calculation"
```

---

## Task 8: Open PR 3

- [ ] **Step 1: Push the branch**

```bash
git push -u origin fix/phase1-active-bugs
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create \
  --base main \
  --head fix/phase1-active-bugs \
  --title "fix(phase1): four active production bugs — datetime, orphan snapshot, broadcaster, avg_runtime" \
  --body "$(cat <<'EOF'
## Summary
- **PostgreSQL datetime fix**: replaces `func.datetime('now', '-1 hour')` (SQLite-only) with a Python-computed `datetime.now(utc) - timedelta(hours=1)` — fixes the broken "recent activity" counter on the dashboard
- **task_latest orphan staleness**: `_mark_tasks_as_orphaned()` now mirrors the `task_events` bulk update to `task_latest` in the same transaction — aggregated task list now shows correct orphan state immediately
- **WebSocket broadcaster guard**: `start_background_broadcaster()` now checks `task.done()` in addition to `is None` — broadcaster restarts correctly if the task exits unexpectedly; `stop_background_broadcaster()` resets `message_queue` to prevent stale event bleed
- **avg_runtime math**: replaces incorrect weighted-sum formula with Welford's online algorithm — daily runtime averages are now correct

## Test plan
- [ ] `test_task_summary_stats.py` — 3 new tests pass
- [ ] `test_orphan_detection.py` — 2 new tests + all 9 existing pass
- [ ] `test_connection_manager.py` — 4 new tests pass
- [ ] `test_daily_stats_service.py` — 2 new tests + all 28 existing pass
- [ ] Full suite: `cd agent && uv run pytest tests/ -v` — all pass
EOF
)"
```

- [ ] **Step 3: Merge and pull**

Once CI passes, merge to main.

```bash
git checkout main && git pull origin main
```

---

## Completion Checklist

- [ ] PR 1 merged (`fix/monitor-reconnect-on-broker-disconnect`)
- [ ] PR 2 merged (`strayer.perf-fix-orphaned-count-queries`)
- [ ] PR 3 merged (`fix/phase1-active-bugs`)
- [ ] Full test suite green on main
- [ ] ZB deployment updated (Helm chart image tag or deploy trigger)
