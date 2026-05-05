#!/usr/bin/env python3
"""
Standalone migration runner. Requires only DATABASE_URL — no broker needed.

Usage:
    DATABASE_URL=postgresql://user:pass@host/db uv run python migrate.py
    DATABASE_URL=postgresql://user:pass@host/db uv run python migrate.py --check
"""

import os
import sys
import traceback

import sqlalchemy as sa
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from alembic import command


def _build_config(database_url: str) -> AlembicConfig:
    cfg = AlembicConfig(os.path.join(os.path.dirname(__file__), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def check(database_url: str) -> int:
    """Exit 0 if schema is current, 1 if migrations are pending."""
    cfg = _build_config(database_url)
    engine = sa.create_engine(database_url)
    with engine.connect() as conn:
        current = set(MigrationContext.configure(conn).get_current_heads())
    expected = set(ScriptDirectory.from_config(cfg).get_heads())
    pending = expected - current
    if pending:
        print(
            f"Schema is out of date — {len(pending)} pending migration(s): {', '.join(sorted(pending))}",
            file=sys.stderr,
        )
        return 1
    print("Schema is up to date.")
    return 0


def upgrade(database_url: str) -> int:
    """Apply all pending migrations. Exit 0 on success, 1 on failure."""
    cfg = _build_config(database_url)
    try:
        command.upgrade(cfg, "head")
        return 0
    except Exception:
        print("Migration failed:", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL environment variable is required.", file=sys.stderr)
        return 1

    if "--check" in sys.argv:
        return check(database_url)

    return upgrade(database_url)


if __name__ == "__main__":
    sys.exit(main())
