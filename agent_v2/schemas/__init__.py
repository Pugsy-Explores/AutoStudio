"""
agent_v2.schemas — Phase 1 schema layer.

All schemas are strict, typed, Pydantic v2 BaseModels.
No runtime logic, no tool calls, no orchestration — only structure and validation.

Import order respects the dependency graph:
  execution (ErrorType) → plan → exploration → replan → tool → trace → context → policies → output → agent_state
"""
from .execution import (
    ErrorType,
    ExecutionError,
    ExecutionMetadata,
    ExecutionOutput,
    ExecutionResult,
    ExecutionStep,
    RetryState,
)
from .plan import (
    PlanDocument,
    PlanMetadata,
    PlanRisk,
    PlanSource,
    PlanStep,
    PlanStepExecution,
    PlanStepFailure,
    PlanStepLastResult,
)
from .exploration import (
    ExplorationContent,
    ExplorationItem,
    ExplorationItemMetadata,
    ExplorationRelevance,
    ExplorationResult,
    ExplorationResultMetadata,
    ExplorationSource,
    ExplorationSummary,
)
from .replan import (
    PlannerInput,
    ReplanChanges,
    ReplanCompletedStep,
    ReplanConstraints,
    ReplanContext,
    ReplanExecutionContext,
    ReplanExplorationContext,
    ReplanExplorationSummary,
    ReplanFailureContext,
    ReplanFailureError,
    ReplanMetadata,
    ReplanNewPlan,
    ReplanOriginalPlan,
    ReplanPartialResult,
    ReplanReasoning,
    ReplanRequest,
    ReplanResult,
    ReplanValidation,
)
from .tool import (
    ToolCall,
    ToolError,
    ToolResult,
)
from .trace import (
    Trace,
    TraceError,
    TraceMetadata,
    TraceStep,
)
from .context import (
    ContextItem,
    ContextWindow,
)
from .policies import (
    ExecutionPolicy,
    FailurePolicy,
)
from .output import (
    ExecutionSummary,
    FinalOutput,
)
from .agent_state import AgentState

__all__ = [
    # execution
    "ErrorType",
    "ExecutionError",
    "ExecutionMetadata",
    "ExecutionOutput",
    "ExecutionResult",
    "ExecutionStep",
    "RetryState",
    # plan
    "PlanDocument",
    "PlanMetadata",
    "PlanRisk",
    "PlanSource",
    "PlanStep",
    "PlanStepExecution",
    "PlanStepFailure",
    "PlanStepLastResult",
    # exploration
    "ExplorationContent",
    "ExplorationItem",
    "ExplorationItemMetadata",
    "ExplorationRelevance",
    "ExplorationResult",
    "ExplorationResultMetadata",
    "ExplorationSource",
    "ExplorationSummary",
    # replan
    "PlannerInput",
    "ReplanChanges",
    "ReplanCompletedStep",
    "ReplanConstraints",
    "ReplanContext",
    "ReplanExecutionContext",
    "ReplanExplorationContext",
    "ReplanExplorationSummary",
    "ReplanFailureContext",
    "ReplanFailureError",
    "ReplanMetadata",
    "ReplanNewPlan",
    "ReplanOriginalPlan",
    "ReplanPartialResult",
    "ReplanReasoning",
    "ReplanRequest",
    "ReplanResult",
    "ReplanValidation",
    # tool
    "ToolCall",
    "ToolError",
    "ToolResult",
    # trace
    "Trace",
    "TraceError",
    "TraceMetadata",
    "TraceStep",
    # context
    "ContextItem",
    "ContextWindow",
    # policies
    "ExecutionPolicy",
    "FailurePolicy",
    # output
    "ExecutionSummary",
    "FinalOutput",
    # agent state
    "AgentState",
]
