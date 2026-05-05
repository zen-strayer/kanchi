#!/usr/bin/env python3
"""
Simulate the zero-downtime retried_by migration against a large PostgreSQL dataset.

Spins up a temporary Postgres container, seeds it with configurable millions of rows,
runs all Alembic migrations up to the revision before ccfacd9fe52b, then applies the
target migration and verifies data integrity and timing.

Usage:
    uv run python scripts/simulate_migration.py [--rows N] [--batch-size N]

Defaults: 2 000 000 rows in task_events, 200 000 rows in task_latest.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

CONTAINER_NAME = "kanchi-migration-sim"
PG_PORT = 5435
PG_DB = "kanchi_sim"
PG_USER = "kanchi"
PG_PASSWORD = "sim_password"
DATABASE_URL = f"postgresql://{PG_USER}:{PG_PASSWORD}@localhost:{PG_PORT}/{PG_DB}"

AGENT_DIR = Path(__file__).parent.parent / "agent"
TARGET_REVISION = "ccfacd9fe52b"
BASE_REVISION = "930bf786783a"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: str, *, check: bool = True, capture: bool = False, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    import os
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        cmd, shell=True, check=check, capture_output=capture, text=True, env=env
    )


def pg_env():
    return {
        "DATABASE_URL": DATABASE_URL,
        # Config.from_env() requires this even though migrations don't use it
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
    }


def wait_for_pg(timeout: int = 30) -> None:
    print("  Waiting for PostgreSQL to accept connections", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = run(
            f"docker exec {CONTAINER_NAME} pg_isready -U {PG_USER} -d {PG_DB} -q",
            check=False,
        )
        if result.returncode == 0:
            print(" ready.")
            return
        print(".", end="", flush=True)
        time.sleep(1)
    raise RuntimeError("PostgreSQL did not become ready in time")


def stop_container() -> None:
    run(f"docker rm -f {CONTAINER_NAME}", check=False)


# ---------------------------------------------------------------------------
# Seed helpers — use COPY for maximum throughput
# ---------------------------------------------------------------------------

def seed_data(conn, task_event_rows: int, task_latest_rows: int) -> None:
    """Bulk-insert rows using COPY via psycopg."""
    import json
    import io
    import random
    import string

    print(f"  Seeding {task_event_rows:,} task_events rows … ", end="", flush=True)
    t0 = time.time()

    def _rand_task_id(i):
        return f"task-{i:08d}-{''.join(random.choices(string.ascii_lowercase, k=6))}"

    retried_by_variants = [
        None,                                   # 30 % NULL
        "[]",                                   # 10 % empty array
        '["retry-00000001"]',                   # 40 % single retry
        '["retry-00000001","retry-00000002"]',  # 20 % two retries
    ]
    weights = [0.30, 0.10, 0.40, 0.20]

    with conn.cursor() as cur:
        with cur.copy(
            "COPY task_events (task_id, task_name, event_type, timestamp, retried_by) "
            "FROM STDIN (FORMAT TEXT, DELIMITER '\t', NULL '\\N')"
        ) as copy:
            for i in range(task_event_rows):
                rb = random.choices(retried_by_variants, weights=weights)[0]
                copy.write_row((
                    _rand_task_id(i),
                    "tasks.example",
                    "task-succeeded",
                    "2025-01-01 00:00:00+00",
                    rb,  # None → NULL handled by psycopg
                ))
    conn.commit()
    print(f"done in {time.time() - t0:.1f}s")

    print(f"  Seeding {task_latest_rows:,} task_latest rows … ", end="", flush=True)
    t0 = time.time()
    with conn.cursor() as cur:
        with cur.copy(
            "COPY task_latest (task_id, event_id, task_name, event_type, timestamp, retried_by, resolved) "
            "FROM STDIN (FORMAT TEXT, DELIMITER '\t', NULL '\\N')"
        ) as copy:
            for i in range(task_latest_rows):
                rb = random.choices(retried_by_variants, weights=weights)[0]
                copy.write_row((
                    _rand_task_id(i),
                    i + 1,
                    "tasks.example",
                    "task-succeeded",
                    "2025-01-01 00:00:00+00",
                    rb,  # None → NULL handled by psycopg
                    False,
                ))
    conn.commit()
    print(f"done in {time.time() - t0:.1f}s")


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify(conn, task_event_rows: int, task_latest_rows: int) -> None:
    with conn.cursor() as cur:
        # Row counts
        cur.execute("SELECT COUNT(*) FROM task_events")
        actual_events = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM task_latest")
        actual_latest = cur.fetchone()[0]

        assert actual_events == task_event_rows, (
            f"task_events row count mismatch: expected {task_event_rows}, got {actual_events}"
        )
        assert actual_latest == task_latest_rows, (
            f"task_latest row count mismatch: expected {task_latest_rows}, got {actual_latest}"
        )
        print(f"  ✓ Row counts preserved: {actual_events:,} task_events, {actual_latest:,} task_latest")

        # No retried_by_old or retried_by_new columns remain
        cur.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name IN ('task_events','task_latest')
              AND column_name IN ('retried_by_old','retried_by_new')
        """)
        leftover = [r[0] for r in cur.fetchall()]
        assert not leftover, f"Unexpected columns still present: {leftover}"
        print("  ✓ No shadow columns remain (retried_by_old / retried_by_new)")

        # Column type is now JSON
        cur.execute("""
            SELECT table_name, data_type
            FROM information_schema.columns
            WHERE table_name IN ('task_events','task_latest')
              AND column_name = 'retried_by'
            ORDER BY table_name
        """)
        for table, dtype in cur.fetchall():
            assert dtype == "json", f"{table}.retried_by expected type 'json', got '{dtype}'"
        print("  ✓ Column type is 'json' in both tables")

        # NULLs preserved
        cur.execute("SELECT COUNT(*) FROM task_events WHERE retried_by IS NULL")
        null_count = cur.fetchone()[0]
        assert null_count > 0, "Expected some NULL retried_by rows but found none"
        print(f"  ✓ NULL values preserved ({null_count:,} NULL rows in task_events)")

        # Sample spot-check: JSON arrays are valid and readable
        cur.execute("""
            SELECT retried_by FROM task_events
            WHERE retried_by IS NOT NULL
            LIMIT 5
        """)
        for (val,) in cur.fetchall():
            assert isinstance(val, list), f"Expected list from JSON column, got {type(val)}: {val!r}"
        print("  ✓ Non-NULL retried_by values returned as Python lists (valid JSON)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=2_000_000,
                        help="Number of task_events rows to seed (default: 2 000 000)")
    parser.add_argument("--latest-rows", type=int, default=None,
                        help="Number of task_latest rows (default: rows // 10)")
    parser.add_argument("--keep", action="store_true",
                        help="Keep the container running after the test")
    args = parser.parse_args()

    task_event_rows = args.rows
    task_latest_rows = args.latest_rows or max(1, task_event_rows // 10)

    print("=" * 60)
    print("Zero-downtime migration simulation")
    print(f"  task_events rows : {task_event_rows:,}")
    print(f"  task_latest rows : {task_latest_rows:,}")
    print("=" * 60)

    # ── 1. Start PostgreSQL container ──────────────────────────────────────
    print("\n[1/5] Starting PostgreSQL container …")
    stop_container()
    run(
        f"docker run -d --name {CONTAINER_NAME} "
        f"-p {PG_PORT}:5432 "
        f"-e POSTGRES_USER={PG_USER} "
        f"-e POSTGRES_PASSWORD={PG_PASSWORD} "
        f"-e POSTGRES_DB={PG_DB} "
        f"postgres:16-alpine"
    )
    wait_for_pg()

    try:
        # ── 2. Apply migrations up to the revision BEFORE ours ────────────
        print(f"\n[2/5] Applying Alembic migrations up to {BASE_REVISION} …")
        run(
            f"cd {AGENT_DIR} && uv run alembic upgrade {BASE_REVISION}",
            env_extra=pg_env(),
        )
        print("  Migrations applied.")

        # ── 3. Seed data ───────────────────────────────────────────────────
        print(f"\n[3/5] Seeding {task_event_rows + task_latest_rows:,} rows …")
        import psycopg
        with psycopg.connect(DATABASE_URL) as conn:
            # Temporarily disable fsync for seeding speed (safe for throwaway DB)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SET synchronous_commit = off")
            conn.autocommit = False
            seed_data(conn, task_event_rows, task_latest_rows)

        # ── 4. Run the migration under test ───────────────────────────────
        print(f"\n[4/5] Running migration {TARGET_REVISION} (zero-downtime path) …")
        t_start = time.time()
        run(
            f"cd {AGENT_DIR} && uv run alembic upgrade {TARGET_REVISION}",
            env_extra=pg_env(),
        )
        elapsed = time.time() - t_start
        print(f"\n  Migration completed in {elapsed:.1f}s  ({elapsed/60:.1f} min)")

        # ── 5. Verify ──────────────────────────────────────────────────────
        print("\n[5/5] Verifying data integrity …")
        with psycopg.connect(DATABASE_URL) as conn:
            verify(conn, task_event_rows, task_latest_rows)

        print("\n" + "=" * 60)
        print("✅  SIMULATION PASSED")
        print(f"    {task_event_rows:,} task_events + {task_latest_rows:,} task_latest")
        print(f"    Migration time: {elapsed:.1f}s ({elapsed/60:.1f} min)")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌  SIMULATION FAILED: {e}", file=sys.stderr)
        if not args.keep:
            stop_container()
        sys.exit(1)
    finally:
        if not args.keep:
            print("\nCleaning up container …")
            stop_container()
        else:
            print(f"\nContainer '{CONTAINER_NAME}' left running on port {PG_PORT}.")
            print(f"DATABASE_URL={DATABASE_URL}")


if __name__ == "__main__":
    main()
