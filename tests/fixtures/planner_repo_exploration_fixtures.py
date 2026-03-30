"""
Repo-realistic FinalExplorationSchema fixtures for PlannerV2 tests (no exploration execution).

Paths mirror this repository layout under agent_v2/.
"""

from __future__ import annotations

from agent_v2.schemas.exploration import (
    ExplorationContent,
    ExplorationItem,
    ExplorationItemMetadata,
    ExplorationRelevance,
    ExplorationResultMetadata,
    ExplorationSource,
    ExplorationSummary,
)
from agent_v2.schemas.final_exploration import (
    ExplorationAdapterTrace,
    ExplorationRelationshipEdge,
    FinalExplorationSchema,
)


def _item(
    item_id: str,
    ref: str,
    *,
    snippet: str = "",
    read_source: str | None = "head",
) -> ExplorationItem:
    return ExplorationItem(
        item_id=item_id,
        type="file",
        source=ExplorationSource(ref=ref),
        content=ExplorationContent(
            summary=f"Evidence for {ref}",
            key_points=[],
            entities=[],
        ),
        relevance=ExplorationRelevance(score=0.88, reason="repo fixture"),
        metadata=ExplorationItemMetadata(
            timestamp="2026-03-27T12:00:00Z",
            tool_name="read_file",
        ),
        snippet=snippet or f"# {ref}\n",
        read_source=read_source,  # type: ignore[arg-type]
    )


def exploration_simple_sufficient() -> FinalExplorationSchema:
    """Clear evidence for ModeManager; high confidence; no gaps."""
    return FinalExplorationSchema(
        exploration_id="exp_simple",
        instruction="Where is ACT mode wired in the runtime?",
        status="complete",
        evidence=[
            _item(
                "e1",
                "agent_v2/runtime/mode_manager.py",
                snippet="def _run_act(self, state: Any)",
            ),
            _item(
                "e2",
                "agent_v2/runtime/bootstrap.py",
                snippet="class V2PlannerAdapter",
            ),
        ],
        relationships=[],
        exploration_summary=ExplorationSummary(
            overall="ModeManager._run_act calls exploration then planner then PlanExecutor.",
            key_findings=[
                "ACT path is _run_explore_plan_execute in mode_manager.py",
                "Planner is invoked via V2PlannerAdapter.plan with exploration result.",
            ],
            knowledge_gaps=[],
            knowledge_gaps_empty_reason="Sources cite concrete files for ACT wiring.",
        ),
        metadata=ExplorationResultMetadata(
            total_items=2,
            created_at="2026-03-27T12:00:00Z",
            completion_status="complete",
            termination_reason="complete",
            source_summary={"symbol": 1, "line": 1, "head": 0},
        ),
        confidence="high",
        trace=ExplorationAdapterTrace(llm_used=True, synthesis_success=True),
    )


def exploration_with_gaps() -> FinalExplorationSchema:
    """Partial exploration: gaps remain; expect refinement before finish."""
    return FinalExplorationSchema(
        exploration_id="exp_gaps",
        instruction="How does replanning merge preserved steps when the plan changes?",
        status="incomplete",
        evidence=[
            _item(
                "e1",
                "agent_v2/runtime/replanner.py",
                snippet="def merge_preserved_completed_steps",
            ),
        ],
        relationships=[],
        exploration_summary=ExplorationSummary(
            overall="Found merge_preserved_completed_steps but not full executor handoff.",
            key_findings=["Replanner builds ReplanContext from ReplanRequest."],
            knowledge_gaps=[
                "Whether PlanExecutor always passes preserve_completed from constraints.",
                "Exact validation path for merged plans.",
            ],
            knowledge_gaps_empty_reason=None,
        ),
        metadata=ExplorationResultMetadata(
            total_items=1,
            created_at="2026-03-27T12:00:00Z",
            completion_status="incomplete",
            termination_reason="max_steps",
            source_summary={"symbol": 0, "line": 1, "head": 0},
        ),
        confidence="medium",
        trace=ExplorationAdapterTrace(llm_used=True, synthesis_success=True),
    )


def exploration_with_relationships() -> FinalExplorationSchema:
    """Graph edges imply order: read planner before mode_manager if callee chain."""
    return FinalExplorationSchema(
        exploration_id="exp_rel",
        instruction="Trace planner entry from bootstrap into ModeManager.",
        status="complete",
        evidence=[
            _item("e1", "agent_v2/planner/planner_v2.py", snippet="class PlannerV2"),
            _item("e2", "agent_v2/runtime/mode_manager.py", snippet="self.planner.plan"),
            _item("e3", "agent_v2/runtime/bootstrap.py", snippet="V2PlannerAdapter"),
        ],
        relationships=[
            ExplorationRelationshipEdge(
                from_key="row:planner_v2",
                to_key="row:bootstrap",
                type="related",
                confidence=0.9,
            ),
            ExplorationRelationshipEdge(
                from_key="row:bootstrap",
                to_key="row:mode_manager",
                type="callees",
                confidence=0.88,
            ),
        ],
        exploration_summary=ExplorationSummary(
            overall="PlannerV2 is wrapped by V2PlannerAdapter; ModeManager calls planner.plan.",
            key_findings=[
                "bootstrap wires PlannerV2",
                "ModeManager owns ACT pipeline",
            ],
            knowledge_gaps=[],
            knowledge_gaps_empty_reason="Relationship edges give ordering hints.",
        ),
        metadata=ExplorationResultMetadata(
            total_items=3,
            created_at="2026-03-27T12:00:00Z",
            completion_status="complete",
            termination_reason="complete",
            source_summary={"symbol": 2, "line": 0, "head": 1},
        ),
        confidence="high",
        trace=ExplorationAdapterTrace(llm_used=True, synthesis_success=True),
    )


def exploration_insufficient_low_confidence() -> FinalExplorationSchema:
    """Low confidence + gaps: conservative read-only refinement expected."""
    return FinalExplorationSchema(
        exploration_id="exp_low",
        instruction="What is the full behavior of the controller loop when enabled?",
        status="incomplete",
        evidence=[
            _item(
                "e1",
                "agent_v2/runtime/mode_manager.py",
                snippet="def _run_act_controller_loop",
            ),
        ],
        relationships=[],
        exploration_summary=ExplorationSummary(
            overall="Only partial view of controller loop; budget and gates not fully traced.",
            key_findings=["_run_act_controller_loop exists on ModeManager."],
            knowledge_gaps=[
                "Sub-exploration gate conditions vs planner controller JSON.",
                "Interaction with max_planner_controller_calls.",
            ],
            knowledge_gaps_empty_reason=None,
        ),
        metadata=ExplorationResultMetadata(
            total_items=1,
            created_at="2026-03-27T12:00:00Z",
            completion_status="incomplete",
            termination_reason="stalled",
            source_summary={"symbol": 0, "line": 1, "head": 0},
        ),
        confidence="low",
        trace=ExplorationAdapterTrace(llm_used=False, synthesis_success=False),
    )
