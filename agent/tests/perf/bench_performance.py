#!/usr/bin/env python3
"""
Performance benchmark: before vs after for the N+1 and COUNT subquery fixes.
NOT committed -- run locally and paste results into the PR description.

Usage (SQLite in-memory, default):
    cd /path/to/kanchi/agent && uv run python tests/perf/bench_performance.py

Usage (PostgreSQL):
    DATABASE_URL=postgresql://kanchi:kanchi@localhost:5432/kanchi \\
        uv run python tests/perf/bench_performance.py
"""

import os
import sys
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, create_engine, func, text
from sqlalchemy import event as sa_event
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from database import Base, RetryRelationshipDB, TaskEventDB, TaskLatestDB
from services.task_service import TaskService

# Adjust this to match your Cloud SQL round-trip time.
# 75ms is the observed p50 latency in the dev cluster via the sidecar proxy.
CLOUD_SQL_RTT_MS = 75

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
IS_POSTGRES = DATABASE_URL.startswith("postgresql")


# ---------------------------------------------------------------------------
# Query counter
# ---------------------------------------------------------------------------


class QueryCounter:
    def __init__(self, engine):
        self.count = 0
        self._engine = engine
        sa_event.listen(engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        self.count += 1

    def reset(self):
        self.count = 0


# ---------------------------------------------------------------------------
# Schema setup
# ---------------------------------------------------------------------------


def setup_schema(engine):
    if IS_POSTGRES:
        # Use alembic so we get the real production schema (indexes, constraints, etc.)
        from database import DatabaseManager

        db = DatabaseManager(DATABASE_URL)
        db.run_migrations()
        print("  Schema: alembic migrations applied")
    else:
        Base.metadata.create_all(engine)
        print("  Schema: SQLAlchemy create_all (SQLite)")


def teardown_data(session):
    """Remove seeded benchmark rows so Postgres can be reused across runs."""
    if IS_POSTGRES:
        session.execute(text("DELETE FROM task_latest WHERE task_id LIKE 'task-%'"))
        session.execute(text("DELETE FROM task_events WHERE task_id LIKE 'orphan-%'"))
        session.execute(text("DELETE FROM retry_relationships WHERE task_id LIKE 'orphan-%'"))
        session.commit()
        print("  Cleaned up benchmark rows from Postgres.")


# ---------------------------------------------------------------------------
# Seeding helpers
# ---------------------------------------------------------------------------


def seed_orphaned(session, n: int):
    now = datetime.now(UTC)
    rows = []
    for i in range(n):
        tid = f"orphan-{i}"
        for j, etype in enumerate(["task-sent", "task-received", "task-started"]):
            rows.append(
                TaskEventDB(
                    task_id=tid,
                    task_name="tasks.bench",
                    event_type=etype,
                    timestamp=now + timedelta(seconds=j),
                    is_orphan=(etype == "task-started"),
                    orphaned_at=now + timedelta(seconds=j) if etype == "task-started" else None,
                )
            )
    session.bulk_save_objects(rows)
    session.commit()


def seed_task_latest(session, n: int):
    now = datetime.now(UTC)
    batch = 5_000
    for start in range(0, n, batch):
        end = min(start + batch, n)
        session.bulk_save_objects(
            [
                TaskLatestDB(
                    task_id=f"task-{i}",
                    event_id=i + 1,
                    task_name="tasks.bench",
                    event_type="task-succeeded",
                    timestamp=now - timedelta(seconds=i),
                    resolved=False,
                )
                for i in range(start, end)
            ]
        )
        session.commit()
        print(f"  task_latest: {end:,} / {n:,}\r", end="", flush=True)
    print()

    if IS_POSTGRES:
        # ANALYZE so the query planner has fresh stats for the benchmark
        session.execute(text("ANALYZE task_latest"))
        session.commit()
        print("  ANALYZE task_latest done")


# ---------------------------------------------------------------------------
# "Before" implementations (simulating old code patterns)
# ---------------------------------------------------------------------------


def orphaned_n1(session, svc):
    """Old N+1 pattern: per-task queries for retry chain and final-state checks."""
    FINAL = {"task-succeeded", "task-failed", "task-revoked", "task-rejected", "task-retried"}

    latest_sq = (
        session.query(TaskEventDB.task_id, func.max(TaskEventDB.timestamp).label("max_ts"))
        .filter(TaskEventDB.is_orphan.is_(True))
        .group_by(TaskEventDB.task_id)
        .subquery()
    )
    orphaned_db = (
        session.query(TaskEventDB)
        .join(
            latest_sq,
            and_(
                TaskEventDB.task_id == latest_sq.c.task_id,
                TaskEventDB.timestamp == latest_sq.c.max_ts,
            ),
        )
        .order_by(TaskEventDB.orphaned_at.desc())
        .all()
    )
    events = [svc._db_to_task_event(e) for e in orphaned_db]
    svc._bulk_enrich_with_retry_info(events)
    svc._attach_resolution_info(events)

    result = []
    for ev in events:
        rr = session.query(RetryRelationshipDB).filter_by(task_id=ev.task_id).first()
        if rr and rr.retry_chain:
            continue
        has_final = (
            session.query(TaskEventDB)
            .filter(
                TaskEventDB.task_id == ev.task_id,
                TaskEventDB.event_type.in_(FINAL),
            )
            .first()
            is not None
        )
        if not has_final:
            result.append(ev)
    return result


def count_subquery(session):
    """Old count pattern: sort before count (triggers subquery wrapping in SQLAlchemy)."""
    from sqlalchemy import desc

    q = session.query(TaskLatestDB).order_by(desc(TaskLatestDB.timestamp), desc(TaskLatestDB.task_id))
    total = q.count()
    rows = q.offset(0).limit(100).all()
    return total, rows


# ---------------------------------------------------------------------------
# "After" implementations (current code)
# ---------------------------------------------------------------------------


def orphaned_batch(svc):
    """New batch pattern: 3 queries regardless of task count."""
    return svc.get_unretried_orphaned_tasks()


def count_scalar(svc):
    """New count pattern: scalar count without sort subquery."""
    result = svc.get_recent_events(limit=100, page=0)
    return result["pagination"]["total"], result["data"]


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def timed(fn, counter, *args, **kwargs):
    counter.reset()
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    elapsed = time.perf_counter() - t0
    return elapsed, counter.count, out


def main():
    db_label = f"postgresql ({DATABASE_URL.split('@')[-1]})" if IS_POSTGRES else "sqlite:///:memory:"
    print(f"Database: {db_label}")
    print()

    engine = create_engine(DATABASE_URL, echo=False)
    print("Setting up schema...")
    setup_schema(engine)

    Session = sessionmaker(bind=engine)
    session = Session()
    counter = QueryCounter(engine)

    N_ORPHANED = 500
    N_LATEST = 350_000

    print()
    print("Seeding...")
    print(f"  {N_ORPHANED:,} orphaned tasks (3 events each)...")
    counter.reset()
    seed_orphaned(session, N_ORPHANED)

    print(f"  {N_LATEST:,} rows in task_latest...")
    counter.reset()
    seed_task_latest(session, N_LATEST)
    print()

    svc = TaskService(session)

    # --- Orphaned N+1 vs Batch ---
    print("=" * 60)
    print("Orphaned Tasks: N+1 vs Batch Queries")
    print(f"Scale: {N_ORPHANED:,} orphaned tasks")
    print()

    t_before, q_before, _ = timed(orphaned_n1, counter, session, svc)
    t_after, q_after, _ = timed(orphaned_batch, counter, svc)

    cloud_before = q_before * CLOUD_SQL_RTT_MS / 1000
    cloud_after = q_after * CLOUD_SQL_RTT_MS / 1000
    speedup = cloud_before / max(cloud_after, 0.001)

    db_col = "postgres" if IS_POSTGRES else "sqlite "
    print(f"  BEFORE  queries={q_before:>5,}  {db_col}={t_before:.3f}s  cloud_sql_est={cloud_before:.1f}s")
    print(f"  AFTER   queries={q_after:>5,}  {db_col}={t_after:.3f}s  cloud_sql_est={cloud_after:.3f}s")
    print(f"  -> {(1 - q_after / max(q_before, 1)) * 100:.1f}% fewer queries  ~{speedup:.0f}x faster on Cloud SQL")
    print()

    # --- COUNT subquery vs scalar ---
    print("=" * 60)
    print("Recent Events: Subquery COUNT vs Scalar COUNT")
    print(f"Scale: {N_LATEST:,} rows in task_latest")
    print()

    t_before2, q_before2, _ = timed(count_subquery, counter, session)
    t_after2, q_after2, _ = timed(count_scalar, counter, svc)

    speedup2 = t_before2 / max(t_after2, 0.0001)
    print(f"  BEFORE  queries={q_before2}  {db_col}={t_before2:.3f}s  (sorted subquery, full materialization)")
    print(f"  AFTER   queries={q_after2}   {db_col}={t_after2:.3f}s  (scalar count, no subquery)")
    print(f"  -> ~{speedup2:.1f}x faster on {('PostgreSQL' if IS_POSTGRES else 'SQLite')}")
    print()
    print("Note: Cloud SQL projection uses 75ms/query (observed dev p50).")
    print("Paste the BEFORE/AFTER blocks above into the PR description.")

    teardown_data(session)
    session.close()
    engine.dispose()


if __name__ == "__main__":
    main()
