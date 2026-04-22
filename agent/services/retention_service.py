"""Background data retention service — prunes old rows from unbounded tables."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from database import DatabaseManager, TaskEventDB, TaskProgressDB, WorkflowExecutionDB

logger = logging.getLogger(__name__)

_PRUNE_INTERVAL_SECONDS = 3600


class RetentionService:
    """Deletes rows older than a configurable number of days."""

    def __init__(self, session: Session):
        self.session = session

    def prune(self, retention_days: int) -> dict[str, int]:
        """Delete records older than retention_days from the three unbounded tables.

        Returns a dict of {table_name: rows_deleted}.
        """
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)

        task_events_deleted = (
            self.session.query(TaskEventDB).filter(TaskEventDB.timestamp < cutoff).delete(synchronize_session=False)
        )

        progress_deleted = (
            self.session.query(TaskProgressDB)
            .filter(TaskProgressDB.timestamp < cutoff)
            .delete(synchronize_session=False)
        )

        workflow_deleted = (
            self.session.query(WorkflowExecutionDB)
            .filter(WorkflowExecutionDB.triggered_at < cutoff)
            .delete(synchronize_session=False)
        )

        self.session.commit()

        return {
            "task_events": task_events_deleted,
            "task_progress_events": progress_deleted,
            "workflow_executions": workflow_deleted,
        }


class RetentionBackgroundTask:
    """Asyncio periodic task that calls RetentionService.prune() every hour."""

    def __init__(self, db_manager: DatabaseManager, retention_days: int):
        self.db_manager = db_manager
        self.retention_days = retention_days
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self.retention_days == 0:
            logger.info("Data retention disabled (DATA_RETENTION_DAYS=0)")
            return
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Retention background task started (retention_days=%d)", self.retention_days)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("Retention background task stopped")

    async def _run_loop(self) -> None:
        while True:
            try:
                with self.db_manager.get_session() as session:
                    service = RetentionService(session)
                    counts = service.prune(self.retention_days)
                    total = sum(counts.values())
                    if total:
                        logger.info("Retention pruning complete: %s", counts)
                    else:
                        logger.debug("Retention pruning complete: nothing to delete")
            except Exception as exc:
                logger.error("Error during retention pruning: %s", exc, exc_info=True)
            await asyncio.sleep(_PRUNE_INTERVAL_SECONDS)
