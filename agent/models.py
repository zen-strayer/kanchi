import ast
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from utils.payload_sanitizer import sanitize_payload


class TaskEvent(BaseModel):
    """Represents a Celery task event"""

    task_id: str
    task_name: str
    event_type: str
    timestamp: datetime
    args: list = Field(default_factory=list)
    kwargs: dict = Field(default_factory=dict)
    retries: int = 0
    eta: str | None = None
    expires: str | None = None
    hostname: str | None = None
    worker_name: str | None = None
    queue: str | None = None
    exchange: str = ""
    routing_key: str = "default"
    root_id: str | None = None
    parent_id: str | None = None
    result: Any | None = None
    runtime: float | None = None
    exception: str | None = None
    traceback: str | None = None
    retry_of: Optional["TaskEvent"] = None
    retried_by: list["TaskEvent"] = Field(default_factory=list)
    is_retry: bool = False
    has_retries: bool = False
    retry_count: int = 0
    is_orphan: bool = False
    orphaned_at: datetime | None = None
    resolved: bool = False
    resolved_by: str | None = None
    resolved_at: datetime | None = None

    model_config = {"from_attributes": True, "json_encoders": {datetime: lambda v: v.isoformat() if v else None}}

    @classmethod
    def from_celery_event(cls, event: dict, task_name: str | None = None) -> "TaskEvent":
        return cls(
            task_id=event.get("uuid", ""),
            task_name=task_name or event.get("name", "unknown"),
            event_type=event.get("type", "unknown"),
            timestamp=datetime.now(UTC),
            args=event.get("args"),
            kwargs=event.get("kwargs"),
            retries=event.get("retries", 0),
            eta=event.get("eta"),
            expires=event.get("expires"),
            hostname=event.get("hostname"),
            queue=event.get("queue"),
            exchange=event.get("exchange") or "",
            routing_key=event.get("routing_key") or "default",
            root_id=event.get("root_id", event.get("uuid", "")),
            parent_id=event.get("parent_id"),
            result=event.get("result"),
            runtime=event.get("runtime"),
            exception=event.get("exception"),
            traceback=event.get("traceback"),
        )

    @field_validator("args", mode="before")
    @classmethod
    def validate_args(cls, v):
        if v is None:
            return []
        if isinstance(v, list):
            sanitized, _ = sanitize_payload(v)
            return sanitized if isinstance(sanitized, list) else ([] if sanitized is None else [sanitized])
        if isinstance(v, tuple):
            sanitized, _ = sanitize_payload(list(v))
            return sanitized if isinstance(sanitized, list) else ([] if sanitized is None else [sanitized])
        if isinstance(v, str):
            try:
                parsed = ast.literal_eval(v) if v and v != "()" else []
                if isinstance(parsed, tuple):
                    parsed = list(parsed)
                elif not isinstance(parsed, list):
                    parsed = []
                sanitized, _ = sanitize_payload(parsed)
                return sanitized if isinstance(sanitized, list) else []
            except Exception:
                return []
        sanitized, _ = sanitize_payload(v)
        return sanitized if isinstance(sanitized, list) else []

    @field_validator("kwargs", mode="before")
    @classmethod
    def validate_kwargs(cls, v):
        if v is None:
            return {}
        if isinstance(v, dict):
            sanitized, _ = sanitize_payload(v)
            return sanitized if isinstance(sanitized, dict) else {}
        if isinstance(v, str):
            try:
                parsed = ast.literal_eval(v) if v and v != "{}" else {}
                sanitized, _ = sanitize_payload(parsed if isinstance(parsed, dict) else {})
                return sanitized if isinstance(sanitized, dict) else {}
            except Exception:
                return {}
        sanitized, _ = sanitize_payload(v)
        return sanitized if isinstance(sanitized, dict) else {}

    @field_validator("timestamp", "orphaned_at", "resolved_at", mode="before")
    @classmethod
    def validate_datetime(cls, v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=UTC)
        return v

    @field_validator("result", mode="before")
    @classmethod
    def sanitize_result(cls, v):
        sanitized, _ = sanitize_payload(v)
        return sanitized


TaskEvent.model_rebuild()


class StepDefinition(BaseModel):
    """Single step definition for task progress."""

    key: str
    label: str
    description: str | None = None
    total: int | None = None
    order: int | None = None


class TaskStepsEvent(BaseModel):
    """Progress steps definition event."""

    task_id: str
    task_name: str
    steps: list[StepDefinition]
    timestamp: datetime
    event_type: Literal["kanchi-task-steps"] = "kanchi-task-steps"

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat() if v else None}}


class TaskProgressEvent(BaseModel):
    """Task progress update event."""

    task_id: str
    task_name: str
    progress: float
    timestamp: datetime
    step_key: str | None = None
    message: str | None = None
    meta: dict[str, Any] | None = None
    event_type: Literal["kanchi-task-progress"] = "kanchi-task-progress"

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat() if v else None}}

    @classmethod
    def from_celery_event(cls, event: dict) -> "TaskProgressEvent":
        sanitized_meta, _ = sanitize_payload(event.get("meta"))
        ts_value = event.get("timestamp")
        if isinstance(ts_value, (int, float)):
            ts = datetime.fromtimestamp(ts_value, tz=UTC)
        elif isinstance(ts_value, str):
            try:
                ts = datetime.fromisoformat(ts_value)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
            except Exception:
                ts = datetime.now(UTC)
        else:
            ts = datetime.now(UTC)
        return cls(
            task_id=event.get("task_id", ""),
            task_name=event.get("task_name", ""),
            progress=float(event.get("progress", 0)),
            timestamp=ts,
            step_key=event.get("step_key"),
            message=event.get("message"),
            meta=sanitized_meta if isinstance(sanitized_meta, dict) else None,
        )


class TaskProgressSnapshot(BaseModel):
    """Aggregate view of progress and steps for a task."""

    task_id: str
    latest: TaskProgressEvent | None = None
    steps: list[StepDefinition] = Field(default_factory=list)
    history: list[TaskProgressEvent] = Field(default_factory=list)


class WorkerInfo(BaseModel):
    """Worker information model"""

    hostname: str
    status: str
    timestamp: datetime
    active_tasks: int = 0
    processed_tasks: int = 0
    sw_ident: str | None = None
    sw_ver: str | None = None
    sw_sys: str | None = None
    loadavg: list[float] | None = None
    freq: float | None = None

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.replace(tzinfo=UTC).isoformat() if v.tzinfo is None else v.isoformat()}


class WorkerEvent(BaseModel):
    """Worker event model"""

    hostname: str
    event_type: str
    timestamp: datetime
    active: int | None = None
    processed: int | None = None
    pool: dict[str, Any] | None = None
    loadavg: list[float] | None = None
    freq: float | None = None
    sw_ident: str | None = None
    sw_ver: str | None = None
    sw_sys: str | None = None

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.replace(tzinfo=UTC).isoformat() if v.tzinfo is None else v.isoformat()}

    @classmethod
    def from_celery_event(cls, event: dict) -> "WorkerEvent":
        """Create WorkerEvent from Celery worker event"""
        event_type = event.get("type", "unknown")

        return cls(
            hostname=event.get("hostname", "unknown"),
            event_type=event_type,
            timestamp=datetime.fromtimestamp(event.get("timestamp", datetime.now(UTC).timestamp()), tz=UTC),
            active=event.get("active"),
            processed=event.get("processed"),
            pool=event.get("pool"),
            loadavg=event.get("loadavg"),
            freq=event.get("freq"),
            sw_ident=event.get("sw_ident"),
            sw_ver=event.get("sw_ver"),
            sw_sys=event.get("sw_sys"),
        )


class ConnectionInfo(BaseModel):
    """WebSocket connection info"""

    status: str
    timestamp: datetime
    message: str
    total_connections: int = 0


class SubscriptionResponse(BaseModel):
    """WebSocket subscription response"""

    status: str
    filters: dict
    timestamp: datetime


class LogEntry(BaseModel):
    """Log entry from frontend"""

    level: str
    message: str
    timestamp: datetime | None = None
    context: dict[str, Any] | None = None


class TaskRegistryResponse(BaseModel):
    """Task registry API response model"""

    id: str
    name: str
    human_readable_name: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    first_seen: datetime
    last_seen: datetime

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.replace(tzinfo=UTC).isoformat() if v.tzinfo is None else v.isoformat()}


class TaskRegistryUpdate(BaseModel):
    """Task registry update request model"""

    human_readable_name: str | None = None
    description: str | None = None
    tags: list[str] | None = None


class TaskRegistryStats(BaseModel):
    """Statistics for a specific task"""

    task_name: str
    total_executions: int = 0
    succeeded: int = 0
    failed: int = 0
    pending: int = 0
    retried: int = 0
    avg_runtime: float | None = None
    last_execution: datetime | None = None

    class Config:
        json_encoders = {
            datetime: lambda v: (
                v.replace(tzinfo=UTC).isoformat() if v and v.tzinfo is None else (v.isoformat() if v else None)
            )
        }


class TaskDailyStatsResponse(BaseModel):
    """Daily statistics response model"""

    task_name: str
    date: date
    total_executions: int = 0
    succeeded: int = 0
    failed: int = 0
    pending: int = 0
    retried: int = 0
    revoked: int = 0
    orphaned: int = 0
    avg_runtime: float | None = None
    min_runtime: float | None = None
    max_runtime: float | None = None
    p50_runtime: float | None = None
    p95_runtime: float | None = None
    p99_runtime: float | None = None
    first_execution: datetime | None = None
    last_execution: datetime | None = None

    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: (
                v.replace(tzinfo=UTC).isoformat() if v and v.tzinfo is None else (v.isoformat() if v else None)
            ),
            date: lambda v: v.isoformat(),
        }


class EnvironmentResponse(BaseModel):
    """Environment API response model"""

    id: str
    name: str
    description: str | None = None
    queue_patterns: list[str] = Field(default_factory=list)
    worker_patterns: list[str] = Field(default_factory=list)
    is_active: bool = False
    is_default: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.replace(tzinfo=UTC).isoformat() if v.tzinfo is None else v.isoformat()}


class EnvironmentCreate(BaseModel):
    """Environment creation request model"""

    name: str
    description: str | None = None
    queue_patterns: list[str] = Field(default_factory=list)
    worker_patterns: list[str] = Field(default_factory=list)
    is_default: bool = False


class EnvironmentUpdate(BaseModel):
    """Environment update request model"""

    name: str | None = None
    description: str | None = None
    queue_patterns: list[str] | None = None
    worker_patterns: list[str] | None = None
    is_default: bool | None = None


class UserSessionResponse(BaseModel):
    """User session API response model"""

    session_id: str
    active_environment_id: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    last_active: datetime

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.replace(tzinfo=UTC).isoformat() if v.tzinfo is None else v.isoformat()}


class UserSessionCreate(BaseModel):
    """User session creation request model"""

    session_id: str
    active_environment_id: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)


class UserSessionUpdate(BaseModel):
    """User session update request model"""

    active_environment_id: str | None = None
    preferences: dict[str, Any] | None = None


class AppSetting(BaseModel):
    """Database-backed application setting."""

    key: str
    value: Any
    value_type: Literal["string", "number", "boolean", "json"] = "string"
    label: str | None = None
    description: str | None = None
    category: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        json_encoders = {datetime: lambda v: v.replace(tzinfo=UTC).isoformat() if v.tzinfo is None else v.isoformat()}


class AppSettingUpdate(BaseModel):
    """Create/update payload for an application setting."""

    value: Any
    value_type: Literal["string", "number", "boolean", "json"] | None = None
    label: str | None = None
    description: str | None = None
    category: str | None = None


class TaskIssueConfig(BaseModel):
    """Configuration for the task issue summary section."""

    lookback_hours: int = Field(default=24, ge=1, le=168)


class AppConfigSnapshot(BaseModel):
    """Grouped configuration snapshot returned to clients."""

    task_issue_summary: TaskIssueConfig


class UserInfo(BaseModel):
    """Authenticated user information returned to clients."""

    id: str
    email: str
    provider: str
    name: str | None = None
    avatar_url: str | None = None


class AuthTokens(BaseModel):
    """Token bundle returned after login/refresh."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_expires_in: int
    session_id: str


class AuthConfigResponse(BaseModel):
    """Backend authentication configuration."""

    auth_enabled: bool
    basic_enabled: bool
    oauth_providers: list[str] = Field(default_factory=list)
    allowed_email_patterns: list[str] = Field(default_factory=list)


class LoginResponse(BaseModel):
    """Login response payload."""

    user: UserInfo
    tokens: AuthTokens
    provider: str


class BasicLoginRequest(BaseModel):
    """Basic authentication request payload."""

    username: str
    password: str
    session_id: str | None = None


class RefreshRequest(BaseModel):
    """Refresh token request payload."""

    refresh_token: str


class LogoutRequest(BaseModel):
    """Logout request payload."""

    session_id: str | None = None


class TimelineBucket(BaseModel):
    """Single time bucket in timeline"""

    timestamp: datetime
    total_executions: int = 0
    succeeded: int = 0
    failed: int = 0
    retried: int = 0

    class Config:
        json_encoders = {datetime: lambda v: v.replace(tzinfo=UTC).isoformat() if v.tzinfo is None else v.isoformat()}


class TaskTimelineResponse(BaseModel):
    """Timeline response showing execution frequency over time"""

    task_name: str
    start_time: datetime
    end_time: datetime
    bucket_size_minutes: int
    buckets: list[TimelineBucket]

    class Config:
        json_encoders = {datetime: lambda v: v.replace(tzinfo=UTC).isoformat() if v.tzinfo is None else v.isoformat()}


class PingMessage(BaseModel):
    """WebSocket ping message"""

    type: Literal["ping"] = "ping"


class PongResponse(BaseModel):
    """WebSocket pong response"""

    type: Literal["pong"] = "pong"
    timestamp: datetime


class SubscribeMessage(BaseModel):
    """WebSocket subscribe message"""

    type: Literal["subscribe"] = "subscribe"
    filters: dict[str, list[str]] | None = Field(default_factory=dict)


class SetModeMessage(BaseModel):
    """WebSocket set mode message"""

    type: Literal["set_mode"] = "set_mode"
    mode: Literal["live", "static"]


class ModeChangedResponse(BaseModel):
    """WebSocket mode changed response"""

    type: Literal["mode_changed"] = "mode_changed"
    mode: Literal["live", "static"]
    timestamp: datetime
    events_count: int | None = None


class GetStoredMessage(BaseModel):
    """WebSocket get stored events message"""

    type: Literal["get_stored"] = "get_stored"
    limit: int | None = None


class StoredEventsResponse(BaseModel):
    """WebSocket stored events response"""

    type: Literal["stored_events_sent"] = "stored_events_sent"
    count: int
    timestamp: datetime


class WebSocketErrorResponse(BaseModel):
    """WebSocket error response sent when a request cannot be fulfilled."""

    type: Literal["error"] = "error"
    message: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConditionOperator(StrEnum):
    """Supported condition operators."""

    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    IN = "in"
    NOT_IN = "not_in"
    MATCHES = "matches"
    GREATER_THAN = "gt"
    LESS_THAN = "lt"
    GREATER_EQUAL = "gte"
    LESS_EQUAL = "lte"
    CONTAINS = "contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"


class TriggerConfig(BaseModel):
    """Base trigger configuration."""

    type: str
    config: dict[str, Any] = Field(default_factory=dict)


class Condition(BaseModel):
    """Single condition for workflow filtering."""

    field: str
    operator: ConditionOperator
    value: Any


class ConditionGroup(BaseModel):
    """Group of conditions with AND/OR logic."""

    operator: Literal["AND", "OR"] = "AND"
    conditions: list[Condition] = Field(default_factory=list)


class CircuitBreakerConfig(BaseModel):
    """Configuration for workflow-level circuit breaking."""

    enabled: bool = True
    max_executions: int = Field(default=1, ge=1, description="Number of executions allowed per window")
    window_seconds: int = Field(default=300, ge=1, description="Sliding window size in seconds")
    context_field: str | None = Field(
        default=None, description="Event context field used to group executions (e.g., root_id, task_id)"
    )

    @field_validator("context_field")
    @classmethod
    def validate_context_field(cls, v):
        if v is None:
            return v
        value = v.strip()
        if not value:
            raise ValueError("context_field cannot be empty")
        return value


class CircuitBreakerState(BaseModel):
    """Result of circuit breaker check."""

    is_open: bool
    reason: str | None = None
    key: str | None = None
    field: str | None = None


class ActionConfig(BaseModel):
    """Configuration for a single action."""

    type: str
    config_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    continue_on_failure: bool = True


class ActionResult(BaseModel):
    """Result of action execution."""

    action_type: str
    status: Literal["success", "failed", "skipped"]
    result: dict[str, Any] | None = None
    error_message: str | None = None
    duration_ms: int = 0


class WorkflowDefinition(BaseModel):
    """Complete workflow definition."""

    id: str | None = None
    name: str
    description: str | None = None
    enabled: bool = True

    trigger: TriggerConfig
    conditions: ConditionGroup | None = None
    actions: list[ActionConfig]

    priority: int = 100
    max_executions_per_hour: int | None = None
    cooldown_seconds: int = 0
    circuit_breaker: CircuitBreakerConfig | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: str | None = None

    execution_count: int = 0
    last_executed_at: datetime | None = None
    success_count: int = 0
    failure_count: int = 0

    class Config:
        from_attributes = True


class WorkflowCreateRequest(BaseModel):
    """Request model for creating a workflow."""

    name: str
    description: str | None = None
    enabled: bool = True
    trigger: TriggerConfig
    conditions: ConditionGroup | None = None
    actions: list[ActionConfig]
    priority: int = 100
    max_executions_per_hour: int | None = None
    cooldown_seconds: int = 0
    circuit_breaker: CircuitBreakerConfig | None = None


class WorkflowUpdateRequest(BaseModel):
    """Request model for updating a workflow."""

    name: str | None = None
    description: str | None = None
    enabled: bool | None = None
    trigger: TriggerConfig | None = None
    conditions: ConditionGroup | None = None
    actions: list[ActionConfig] | None = None
    priority: int | None = None
    max_executions_per_hour: int | None = None
    cooldown_seconds: int | None = None
    circuit_breaker: CircuitBreakerConfig | None = None


class WorkflowExecutionRecord(BaseModel):
    """Execution history record."""

    id: int
    workflow_id: str
    triggered_at: datetime
    trigger_type: str
    trigger_event: dict[str, Any]
    status: Literal["pending", "running", "completed", "failed", "rate_limited", "circuit_open"]
    actions_executed: list[dict[str, Any]] | None = None
    error_message: str | None = None
    stack_trace: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = None
    workflow_snapshot: dict[str, Any] | None = None
    circuit_breaker_key: str | None = None

    class Config:
        from_attributes = True


class ActionConfigDefinition(BaseModel):
    """Reusable action configuration."""

    id: str | None = None
    name: str
    description: str | None = None
    action_type: str
    config: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: str | None = None
    usage_count: int = 0
    last_used_at: datetime | None = None

    class Config:
        from_attributes = True


class ActionConfigCreateRequest(BaseModel):
    """Request model for creating action config."""

    name: str
    description: str | None = None
    action_type: str
    config: dict[str, Any]


class ActionConfigUpdateRequest(BaseModel):
    """Request model for updating action config."""

    name: str | None = None
    description: str | None = None
    config: dict[str, Any] | None = None
