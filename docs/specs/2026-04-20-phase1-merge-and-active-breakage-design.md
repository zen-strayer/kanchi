# Phase 1: Merge & Active Breakage — Design Spec

**Date:** 2026-04-20  
**Status:** Approved  
**Scope:** Production-readiness for ZenBusiness internal deployment (GKE + PostgreSQL + Redis)

---

## Overview

Phase 1 addresses everything that is broken or regressed *right now* in the ZB deployment. It consists of two branch merges (already built, reviewed, and fixed) plus four targeted bug fixes. No new features. No refactoring beyond what's required to fix each bug.

After Phase 1 the deployment will have: a resilient broker monitor, no N+1 on orphaned task queries, a working "recent activity" counter on the dashboard, correct orphan state in the aggregated task list, a reliable WebSocket broadcaster, and accurate daily runtime averages.

---

## Items

### Item 1 — Merge `fix/monitor-reconnect-on-broker-disconnect`

**What:** Exponential backoff reconnect loop for `CeleryEventMonitor`. When the Redis broker drops, the monitor now retries with 1s→2s→4s→…→60s backoff instead of exiting the daemon thread silently.

**Review status:** Reviewed. One issue found and fixed — replaced `time.sleep()` + boolean flag with `threading.Event.wait()` so `stop()` interrupts the sleep immediately instead of hanging up to 60s on shutdown.

**Files changed:** `agent/monitor.py`, `agent/app.py`, `agent/tests/unit/test_monitor_reconnect.py`  
**Tests:** 9 tests, all passing.  
**Merge as:** PR into `main`, no additional changes needed.

---

### Item 2 — Merge `strayer.perf-fix-orphaned-count-queries`

**What:** Two performance fixes:
1. Eliminates N+1 on `GET /api/tasks/orphaned` — retry relationships now batch-loaded in a single query instead of one per task.
2. Replaces `Query.count()` (which materializes a subquery) with `query.with_entities(func.count(...)).scalar()` on both paginated endpoints.

**Review status:** Reviewed. One issue found and fixed — removed a redundant second `RetryRelationshipDB` query that was introduced alongside the batch load. Filter now uses `event.retry_count == 0` from the already-enriched events.

**Files changed:** `agent/api/task_routes.py`, `agent/services/task_service.py`, `agent/tests/unit/test_orphaned_tasks.py`, `agent/tests/unit/test_recent_events_count.py`  
**Tests:** 7 tests, all passing.  
**Merge as:** PR into `main`, after Item 1 is merged.

---

### Item 3 — Fix PostgreSQL datetime expression

**File:** `agent/services/task_service.py:395`  
**Method:** `get_task_summary_stats()`

**The bug:** The "recent activity" count on the dashboard filters with `func.datetime('now', '-1 hour')` — SQLite-only syntax. On the ZB PostgreSQL deployment this silently returns 0 or raises, breaking the counter entirely.

**Fix:** Replace the SQLite expression with a Python-computed bound parameter:

```python
# Before
.filter(TaskEventDB.timestamp >= func.datetime('now', '-1 hour'))

# After
from datetime import datetime, timezone, timedelta
one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
.filter(TaskEventDB.timestamp >= one_hour_ago)
```

This is dialect-agnostic and consistent with how timestamp comparisons are done elsewhere in the codebase.

**Tests needed:**
- `test_get_task_summary_stats_recent_activity_counts_last_hour` — create events inside and outside the 1-hour window, assert only recent ones are counted
- `test_get_task_summary_stats_empty_db_returns_zero` — no events, assert `recent_activity == 0`

---

### Item 4 — Fix `task_latest` orphan staleness

**File:** `agent/services/orphan_detection_service.py:62`  
**Method:** `_mark_tasks_as_orphaned()`

**The bug:** When a worker goes offline, `_mark_tasks_as_orphaned()` bulk-updates `task_events` to set `is_orphan=True` but never updates the `task_latest` snapshot table. The aggregated task list view (`/api/events/recent?aggregate=true`) reads from `task_latest`, so orphaned tasks continue showing `is_orphan=False` in the UI until they receive a new event — which they never will.

**Fix:** After the existing `task_events` bulk update, add a matching bulk update to `task_latest` within the same transaction:

```python
# Existing — task_events update
self.session.query(TaskEventDB).filter(
    TaskEventDB.task_id.in_(task_ids)
).update({'is_orphan': True, 'orphaned_at': orphaned_at}, synchronize_session=False)

# New — mirror update to task_latest snapshot
self.session.query(TaskLatestDB).filter(
    TaskLatestDB.task_id.in_(task_ids)
).update({'is_orphan': True, 'orphaned_at': orphaned_at}, synchronize_session=False)

self.session.commit()  # single commit covers both
```

**Tests needed:**
- `test_mark_tasks_as_orphaned_updates_task_latest` — after calling `_mark_tasks_as_orphaned()`, query `task_latest` and assert `is_orphan=True` and `orphaned_at` is set
- `test_mark_tasks_as_orphaned_task_latest_missing_row_is_safe` — task exists in `task_events` but not `task_latest` (edge case); assert no error is raised (SQLAlchemy bulk update with no matching rows is a no-op)

---

### Item 5 — Fix WebSocket broadcaster task state check

**File:** `agent/connection_manager.py`  
**Method:** `start_background_broadcaster()`

**The bug:** The guard condition is `if self._broadcast_task is None and not self._running`. If the broadcaster coroutine exits unexpectedly (unhandled exception escaping the inner `try/except`, external cancellation), `_broadcast_task` is a completed `asyncio.Future` — not `None`. The guard blocks restart because `_broadcast_task is None` is `False`, even though nothing is broadcasting.

**Fix:** Extend the `None` check to also treat a completed/cancelled task as inactive:

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

Also reset `self.message_queue = None` in `stop_background_broadcaster()` so a fresh queue is created on restart, preventing stale queued events from bleeding into a new session.

**Tests needed:**
- `test_broadcaster_restarts_after_task_completes_unexpectedly` — create a `ConnectionManager`, manually mark `_broadcast_task` as done (use `asyncio.create_task` with an immediately-returning coroutine and wait for it), call `start_background_broadcaster()`, assert a new task is created
- `test_broadcaster_does_not_double_start_when_running` — while `_running=True`, call `start_background_broadcaster()` again, assert only one task exists

---

### Item 6 — Fix `avg_runtime` incremental average math

**File:** `agent/services/daily_stats_service.py:117`  
**Method:** The task-succeeded handler that updates `DailyStats`

**The bug:** The rolling average formula uses `stats.succeeded` as the count of *previous* samples, but `stats.succeeded` is the total for the day *before* this event increments it — causing off-by-one and double-counting in the weighted sum. By the 3rd success the formula is computing a wrong weighted average.

**Fix:** Replace with Welford's online algorithm — simple, numerically stable, and correct:

```python
# Before (wrong)
stats.avg_runtime = (stats.avg_runtime * stats.succeeded + runtime) / (stats.succeeded + 1)

# After (correct — Welford's method)
n = stats.succeeded + 1   # new count after this success (increment happens after this block)
stats.avg_runtime = stats.avg_runtime + (runtime - stats.avg_runtime) / n
```

**Tests needed:** The existing 28 `DailyStatsService` tests must be audited — some may have been written against the buggy formula. Recalculate expected `avg_runtime` values using the correct formula. Add:
- `test_avg_runtime_correct_after_three_successes` — explicit check that after 3 tasks with runtimes [10, 20, 30], `avg_runtime == 20.0`
- `test_avg_runtime_single_task_equals_its_runtime` — after 1 task, avg equals that task's runtime

---

## PR Structure

| PR | Branch | Contents | Prerequisite |
|---|---|---|---|
| PR 1 | `fix/monitor-reconnect-on-broker-disconnect` | Item 1 — broker reconnect | none |
| PR 2 | `strayer.perf-fix-orphaned-count-queries` | Item 2 — N+1 + scalar count | PR 1 merged |
| PR 3 | `fix/phase1-active-bugs` (new branch off main) | Items 3, 4, 5, 6 — four bug fixes | PR 2 merged |

PRs 1 and 2 can be opened simultaneously but should be merged in order (no conflicts between them, ordering is just hygiene).

---

## Testing Strategy

All fixes follow TDD:
1. Write failing test that reproduces the bug
2. Apply the fix
3. Confirm test passes
4. Run full suite (`uv run pytest agent/tests/ -v`) — no regressions

PR 3 must not touch any existing test logic unless a test was written against the buggy `avg_runtime` formula (in which case the test is wrong and must be corrected, not the fix).

---

## Out of Scope

- No changes to API response shapes
- No changes to frontend
- No dependency upgrades
- No Dockerfile changes
- No schema migrations
