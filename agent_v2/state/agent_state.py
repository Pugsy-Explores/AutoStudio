from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Avoid importing agent_v2.schemas here (state stays a thin bag).
# exploration_result holds ExplorationResult from ExplorationRunner when set by ModeManager.


@dataclass
class AgentState:
    instruction: str

    # Full trajectory (replaces react_history)
    history: List[Dict[str, Any]] = field(default_factory=list)

    # Retrieved / working context
    context: Dict[str, Any] = field(default_factory=dict)

    # Phase 8 — last exploration phase output (schema: ExplorationResult)
    exploration_result: Optional[Any] = None

    # Planning / plan-execute (Phase 5+): full plan JSON or legacy list payload
    current_plan: Optional[Any] = None
    # Optional denormalized steps list for UI / trace (JSON dicts per PlanStep)
    current_plan_steps: Optional[List[Dict[str, Any]]] = None
    plan_index: int = 0

    # Step-level outputs
    step_results: List[Dict[str, Any]] = field(default_factory=list)

    # Metadata (timing, errors, counters, etc.)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Retry/failure tracking for adaptive execution.
    retry_count: int = 0
    last_error: Optional[str] = None
    debug_last_action: Optional[str] = None
