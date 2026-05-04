"""Workflow executor for running workflow actions."""

import logging
import traceback
from typing import Any

from sqlalchemy.orm import Session

from models import TaskEvent, WorkerEvent, WorkflowDefinition
from services.action_executor import ActionExecutor
from services.workflow_service import WorkflowService

logger = logging.getLogger(__name__)


class WorkflowExecutor:
    """Executes workflow actions and manages execution lifecycle."""

    def __init__(self, session: Session, db_manager, monitor_instance=None):
        self.session = session
        self.db_manager = db_manager
        self.monitor_instance = monitor_instance

    async def execute_workflow(
        self,
        workflow: WorkflowDefinition,
        context: dict[str, Any],
        event: TaskEvent | WorkerEvent,
        circuit_breaker_key: str | None = None,
    ):
        """Execute a workflow's actions."""
        workflow_service = WorkflowService(self.session)

        if circuit_breaker_key is None:
            circuit_breaker_key, _ = workflow_service.resolve_circuit_breaker_key(workflow, context)

        execution_id = workflow_service.record_workflow_execution_start(
            workflow_id=workflow.id,
            trigger_type=workflow.trigger.type,
            trigger_event=context,
            workflow_snapshot=workflow.model_dump(),
            circuit_breaker_key=circuit_breaker_key,
        )

        logger.info("Started workflow execution: %s (execution_id=%s)", workflow.name, execution_id)

        action_results = []
        overall_status = "completed"
        error_message = None
        stack_trace = None

        try:
            action_executor = ActionExecutor(
                session=self.session, db_manager=self.db_manager, monitor_instance=self.monitor_instance
            )

            for idx, action_config in enumerate(workflow.actions):
                logger.info(
                    "Executing action %s/%s: %s (workflow=%s)",
                    idx + 1,
                    len(workflow.actions),
                    action_config.type,
                    workflow.name,
                )

                result = await action_executor.execute(
                    action_type=action_config.type, context=context, params=action_config.params
                )

                action_results.append(
                    {
                        "action_type": result.action_type,
                        "status": result.status,
                        "result": result.result,
                        "error_message": result.error_message,
                        "duration_ms": result.duration_ms,
                    }
                )

                if result.status == "failed":
                    logger.warning(
                        "Action failed: %s - %s (workflow=%s)",
                        action_config.type,
                        result.error_message,
                        workflow.name,
                    )

                    if not action_config.continue_on_failure:
                        logger.info("Stopping workflow execution due to action failure")
                        overall_status = "failed"
                        error_message = f"Action {action_config.type} failed: {result.error_message}"
                        break

            workflow_service.update_workflow_stats(workflow_id=workflow.id, success=(overall_status == "completed"))

            logger.info(
                "Completed workflow execution: %s (status=%s, actions=%s)",
                workflow.name,
                overall_status,
                len(action_results),
            )

        except Exception as e:
            overall_status = "failed"
            error_message = str(e)
            stack_trace = traceback.format_exc()
            logger.error("Workflow execution error: %s - %s", workflow.name, e, exc_info=True)

        finally:
            workflow_service.update_workflow_execution(
                execution_id=execution_id,
                status=overall_status,
                actions_executed=action_results,
                error_message=error_message,
                stack_trace=stack_trace,
            )
