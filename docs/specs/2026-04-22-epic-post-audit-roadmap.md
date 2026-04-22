# Epic: Post-Audit Production Roadmap

**Date:** 2026-04-22
**Status:** Approved
**Source:** `docs/audit-2026-04-20.md`
**Scope:** ZenBusiness internal deployment — GKE + PostgreSQL + Redis

---

## Overview

This epic covers all remaining work identified in the 2026-04-20 codebase audit. Phase 1 (active bugs) and the uv/ruff/ty tooling migration are already complete and merged to main. The five phases below address the remaining findings in priority order: operational stability first, then performance, security, code quality, and release pipeline automation.

---

## Completed

| Work | Branch / PR | Status |
|---|---|---|
| Phase 1: Active Breakage | `fix/phase1-active-bugs` | Merged |
| uv + ruff + ty migration | `chore/uv-ruff-ty` | Merged |

---

## Phase 2 — Stability

**Goal:** Prevent crashes and silent failures in the current production deployment. All items are small and targeted.

**Branch:** `fix/phase2-stability`

| # | Item | File | Audit ref |
|---|---|---|---|
| 2.1 | `_mark_tasks_as_orphaned` sleeps while holding an open SQLAlchemy session — blocks all writes for 2s on SQLite on every worker-offline event. Open a fresh session after the sleep instead of receiving one. | `agent/event_handler.py:107` | Design weakness |
| 2.2 | Frontend WebSocket gives up permanently after 5 reconnect failures with no recovery path. Reset the attempt counter and reconnect on `document.visibilitychange`. | `frontend/app/stores/websocket.ts:34` | Bug #6 |
| 2.3 | Workflow engine spawns a bare `threading.Thread` per Celery event — no upper bound. Replace with a bounded `ThreadPoolExecutor(max_workers=10)`. Add `shutdown()` for clean teardown. | `agent/services/workflow_engine.py:39` | Design weakness |
| 2.4 | `CELERY_BROKER_URL` not set silently falls back to Celery's hardcoded `amqp://guest@localhost//`. Raise `ValueError` in `Config.__post_init__` with a clear diagnostic message. Fix type annotation from `str` to `str \| None`. | `agent/config.py:56` | Design weakness |
| 2.5 | Deprecated `asyncio.get_event_loop()` called inside an async context. Replace with `asyncio.get_running_loop()`. | `agent/connection_manager.py:25` | Code quality |

**Testing:** TDD for all Python items (4 new test files). Frontend item verified manually via `make dev`.

---

## Phase 3 — Performance & Scale

**Goal:** Prevent the deployment from degrading over time as data accumulates and traffic grows. None of these are breaking right now, but each has a predictable failure mode.

**Branch:** `feat/phase3-performance`

| # | Item | File | Audit ref |
|---|---|---|---|
| 3.1 | `task_events`, `workflow_executions`, and `task_progress_events` grow forever — no archival, pruning, or partition scheme. Add a configurable background pruning job, defaulting to 30-day retention, controlled by a `DATA_RETENTION_DAYS` env var (set to `0` to disable). | `agent/services/` (new `retention_service.py`) | Design weakness |
| 3.2 | WebSocket environment filtering is entirely client-side — all events stream to every connected client regardless of active environment, noted in a code comment. Filter at broadcast time using the client's registered environment. | `agent/api/websocket_routes.py:144`, `agent/connection_manager.py` | Design weakness |
| 3.3 | No upper bound on the `limit` field in the WebSocket `get_stored` message — a client can request arbitrarily large result sets. Cap at 10,000 and return an error response if exceeded. | `agent/api/websocket_routes.py:169` | Security MEDIUM |
| 3.4 | `sort_by` column name passed directly to SQLAlchemy with no validation — allows column name enumeration. Whitelist allowed column names and return a 400 for invalid values. | `agent/services/task_service.py:1244` | Security LOW |

**Testing:** TDD for all items. Retention service tested with time-shifted data. WS filtering tested via mocked connection manager.

---

## Phase 4 — Security Hardening

**Goal:** Reduce attack surface and fix security posture issues. These are not being actively exploited internally but represent real risk.

**Branch:** `fix/phase4-security`

| # | Item | Severity | File | Notes |
|---|---|---|---|---|
| 4.1 | `ENABLE_PICKLE_SERIALIZATION=true` enables pickle deserialization of broker messages — arbitrary code execution via malicious broker payloads. Add a prominent startup warning; document the risk clearly. Default remains `false`. | HIGH | `agent/config.py:93`, `agent/monitor.py:34` | Cannot be removed without breaking users who rely on pickle tasks — warning + docs is the right fix |
| 4.2 | Dockerfile runs as root — no `USER` instruction. Add a non-root user and switch to it before the `CMD`. | HIGH | `Dockerfile` | |
| 4.3 | Dockerfile hardcodes `amqp://guest:guest@localhost:5672//` as the default broker URL. Remove the default; `CELERY_BROKER_URL` is now required (Phase 2.4). | HIGH | `Dockerfile:43` | Phase 2.4 prerequisite |
| 4.4 | No brute-force protection on `/api/auth/login`. Add per-IP rate limiting (e.g., slowapi or a simple in-memory counter with TTL). | MEDIUM | `agent/api/auth_routes.py:83` | |
| 4.5 | Empty `ALLOWED_EMAIL_PATTERNS` permits any OAuth user to log in. When OAuth is enabled and `ALLOWED_EMAIL_PATTERNS` is empty, reject login with a clear config error. | MEDIUM | `agent/security/auth.py:233` | |
| 4.6 | JWT bearer token passed as a WebSocket URL query param — visible in server logs, proxy logs, and browser history. Move token delivery to the first WebSocket message after connection. | MEDIUM | `agent/api/websocket_routes.py:67` | Requires frontend change |
| 4.7 | Slack webhook URL not restricted to Slack domains — SSRF-adjacent risk. Validate that the URL hostname is `hooks.slack.com` before sending. | MEDIUM | `agent/services/action_config_service.py:123` | |
| 4.8 | `/metrics` Prometheus endpoint has no auth regardless of `AUTH_ENABLED`. Gate behind the same auth check as other endpoints when `AUTH_ENABLED=true`. | LOW | `agent/api/metrics_routes.py:11` | |

**Testing:** TDD for all server-side items. Rate limiting tested with rapid-fire requests. Auth bypass tests for 4.5. Slack domain validation unit tests.

---

## Phase 5 — Code Quality

**Goal:** Eliminate technical debt that slows down future changes. No production impact.

**Branch:** `chore/phase5-code-quality`

| # | Item | File | Audit ref |
|---|---|---|---|
| 5.1 | Pydantic v1/v2 mixed — `workflow.dict()` and `.model_dump()` coexist. Replace all `.dict()` calls with `.model_dump()` across the workflow code. | `agent/services/workflow_*.py`, `agent/api/workflow_routes.py` | Design weakness |
| 5.2 | `declarative_base()` from `sqlalchemy.ext.declarative` is deprecated since SQLAlchemy 1.4. Migrate to `sqlalchemy.orm.DeclarativeBase`. | `agent/database.py:20` | Code quality |
| 5.3 | Remove unused imports — `StaticPool` imported but never used; `send_kanchi_progress` imported but never called. | `agent/database.py:22`, `agent/app.py:25` | Code quality |
| 5.4 | `task-rejected` events (serialization failures, policy rejections) not handled — tasks appear in-flight forever. Add a handler that marks them as failed with a `rejected` event type. | `agent/event_handler.py`, `agent/constants.py` | Design weakness |
| 5.5 | `retried_by` stored as `Text` (JSON-serialized) in `TaskEventDB` but as native `JSON` in `TaskLatestDB`. Normalize to native `JSON` type in `TaskEventDB`. Requires an Alembic migration. | `agent/database.py:77` | Design weakness |
| 5.6 | Mixed `f-string` and `%s` logging styles throughout. Standardize to `%s` lazy interpolation (consistent with stdlib logging best practice — avoids string formatting cost when log level is suppressed). | Various | Code quality |

**Testing:** Pydantic migration verified by full test suite. `declarative_base` migration verified by all DB tests. `task-rejected` handling covered by new unit tests.

---

## Phase 6 — Release Pipeline

**Goal:** Automation that prevents regressions from landing and keeps dependencies current.

**Branch:** `chore/phase6-release-pipeline`

| # | Item | File | Notes |
|---|---|---|---|
| 6.1 | Test CI — add a GitHub Actions workflow running `uv run pytest tests/ -v` on every PR. Separate job from the existing lint workflow. | `.github/workflows/test.yml` | |
| 6.2 | Dependabot — configure automated dependency update PRs for both Python (`uv`) and npm packages. | `.github/dependabot.yml` | |
| 6.3 | Docker image pinning — `python:3.12-slim` and `node:20-alpine` use floating tags that can change silently. Pin to SHA digests in the Dockerfile. | `Dockerfile` | |

---

## Sequencing

Phases must be executed in order. Each phase merges to `main` before the next begins.

```
Phase 2 (Stability) → Phase 3 (Performance) → Phase 4 (Security) → Phase 5 (Code Quality) → Phase 6 (Release Pipeline)
```

Phase 4.3 (remove Dockerfile default broker URL) has a hard dependency on Phase 2.4 (startup validation) being merged first.

---

## Out of Scope

The following audit findings are noted but not scheduled:

- **JWT tokens in `localStorage`** — mitigating XSS would require a full auth refactor (httpOnly cookies, refresh token rotation). Deferred.
- **`kanchi-sdk` black box** — first-party upstream package with no source in this repo. Cannot be audited. Tracked as a known unknown.
- **Wildcard CORS + `allow_credentials=true`** — mitigated by browser spec in current context. Deferred.
- **`SESSION_SECRET_KEY` ephemeral randomization** — invalidates sessions on restart. Operational config issue, not a code fix. Documented in deployment guide.
- **Zero test coverage on `ConnectionManager`, `EventHandler`, `CeleryEventMonitor`, API routes** — addressed incrementally as each phase touches those files, not as a standalone phase.
