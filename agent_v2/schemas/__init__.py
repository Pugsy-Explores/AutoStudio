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
    PlannerControllerOutput,
    PlanRisk,
    PlanSource,
    PlanStep,
)
from .execution_task import (
    CompiledExecutionGraph,
    ExecutionTask,
    TaskRuntimeState,
    TaskScheduler,
)
from .answer_validation import AnswerValidationResult
from .plan_state import PlanState, plan_state_from_plan_document
from .planner_decision import PlannerDecision, PlannerDecisionType
from .planner_plan_context import (
    ExplorationInsufficientContext,
    PlannerPlanContext,
)
from .exploration import (
    ExplorationCandidate,
    ExplorationContent,
    ExplorationDecision,
    ExplorationTarget,
    GraphExpansionResult,
    ExplorationItem,
    ExplorationItemMetadata,
    ExplorationRelevance,
    ExplorationResult,
    ExplorationResultMetadata,
    ExplorationState,
    ExplorationSource,
    ExplorationSummary,
    QueryIntent,
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
    "PlannerControllerOutput",
    "PlanRisk",
    "PlanSource",
    "PlanStep",
    "CompiledExecutionGraph",
    "ExecutionTask",
    "TaskRuntimeState",
    "TaskScheduler",
    "PlanState",
    "plan_state_from_plan_document",
    "AnswerValidationResult",
    "PlannerDecision",
    "PlannerDecisionType",
    "ExplorationInsufficientContext",
    "PlannerPlanContext",
    # exploration
    "ExplorationCandidate",
    "ExplorationContent",
    "ExplorationDecision",
    "ExplorationTarget",
    "GraphExpansionResult",
    "ExplorationItem",
    "ExplorationItemMetadata",
    "ExplorationRelevance",
    "ExplorationResult",
    "ExplorationResultMetadata",
    "ExplorationState",
    "ExplorationSource",
    "ExplorationSummary",
    "QueryIntent",
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
