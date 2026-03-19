# Hierarchical Phased Orchestration — Architecture Plan

**Type:** Architecture Direction Document  
**Status:** Approved design direction. Not an implementation task.  
**Date:** 2026-03-20  
**Author:** Principal Engineer  
**Inputs:** `INTENT_UNDERSTANDING_STACK_AUDIT.md`, `INTENT_UNDERSTANDING_GAP_REPORT.md`, `MIXED_INTENT_DESIGN_ANALYSIS.md`, `UPSTREAM_ARTIFACT_DISAMBIGUATION_STACK_AUDIT.md`, `SYMBOL_ONLY_EDIT_DISAMBIGUATION_AUDIT.md`, `plan_resolver.py`, `planner.py`, `planner_utils.py`, `deterministic_runner.py`, `execution_loop.py`, `replanner.py`, `goal_evaluator.py`, `step_dispatcher.py`

---

## 1. Executive Summary

### 1.1 Current Architecture Is Single-Plan / Single-Lane

AutoStudio today is a **single-plan, single-lane, single-phase** execution engine. One instruction produces one plan. That plan is assigned one `dominant_artifact_mode` (`"code"` or `"docs"`) at the start of execution — set once, immutable for the entire task. All steps execute under that lane. Replanning is lane-local. Goal evaluation is flat and task-global.

This is a principled and well-implemented design for the class of tasks it handles today. It is not the right long-term architecture for the class of tasks a serious coding agent must handle.

### 1.2 Target Architecture Is Hierarchical and Phase-Based

The target architecture decomposes one instruction into an ordered sequence of **phases**. Each phase is a subgoal with its own lane assignment, retrieval context, validation contract, and retry/replan policy. A **parent orchestrator** holds the ordered phase list, monitors each phase outcome, and applies a policy decision: continue to next phase, retry current phase, replan from current phase, request clarification, or stop.

This is the architecture that matches how high-end coding agents work: plan → decompose → execute in phases → validate each phase → escalate or continue. It is the architecture this platform is moving toward.

### 1.3 "Clarify and Refuse Mixed Intent" Is Not the Destination

Design C from `MIXED_INTENT_DESIGN_ANALYSIS.md` — explicit rejection of mixed intent with a clarification prompt — is **not** the target architecture. It is a valid **fallback safety mechanism** for when decomposition fails or a phase cannot be safely grounded. It is not a solution to the architectural limitation. The real limitation is structural: the platform has no concept of subgoals, per-phase lanes, or parent-level continuation policy. Clarification-only is the architecture of last resort, not the architecture of ambition.

The path forward is hierarchical decomposition. Clarification exists only when decomposition is unsafe or impossible.

---

## 2. Current Architectural Constraints

### 2.1 "One Instruction → One Plan" Is Enforced Here

**`agent/orchestrator/plan_resolver.py` → `get_plan()` (lines 164–285)**

`get_plan` accepts a single `instruction` string and always returns a single flat plan dict with a `steps` list. There is no concept of phase splitting, subgoal sequencing, or returning multiple plans. Every call returns exactly one plan with one `plan_id`. This is the hard entry point contract: one instruction in, one plan out.

```164:195:agent/orchestrator/plan_resolver.py
def get_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
) -> dict:
    ...
    if _is_docs_artifact_intent(instruction):
        ...
        return plan_result
    decision = route_instruction(instruction)
    ...
    return _ensure_plan_id(plan(instruction, retry_context=retry_context))
```

**`planner/planner.py` → `plan()` (lines 147–267)**

The planner model receives one `instruction` and emits one JSON plan. The fallback (`_build_controlled_fallback_plan`) also produces one flat plan. No path through the planner produces structured phases or a parent container.

### 2.2 "One Dominant Lane" Is Derived and Assumed Immutable

**`agent/orchestrator/deterministic_runner.py` → `run_deterministic()` (line 41)**

```41:41:agent/orchestrator/deterministic_runner.py
dominant_artifact_mode = "docs" if is_explicit_docs_lane_by_structure(plan_result) else "code"
```

This single line derives the lane from plan structure and stores it in `state.context["dominant_artifact_mode"]`. The comment at line 59 makes it explicit: *"Set once; immutable for the task/attempt."* This lane propagates to every downstream component: dispatcher, replanner, goal evaluator, explain gate.

**`planner/planner_utils.py` → `is_explicit_docs_lane_by_structure()` (lines 16–39)**

Returns a single `bool`. A plan is either docs-lane or code-lane. There is no mixed lane, no per-phase lane, no nullable case meaning "not yet determined."

### 2.3 Mixed Docs/Code Plans Are Hard-Rejected Here

**`planner/planner_utils.py` → `validate_plan()` (lines 79–104)**

```79:104:planner/planner_utils.py
# Phase 6A: single-lane per task (Option A) plan-level contract.
has_any_docs_step = any(
    isinstance(s, dict) and s.get("artifact_mode") == "docs" for s in (steps or [])
)
docs_by_structure = is_explicit_docs_lane_by_structure(plan_dict)
if has_any_docs_step or docs_by_structure:
    for s in steps:
        a = (s.get("action") or "").upper()
        if a in ("SEARCH", "EDIT"):
            return False
        if a in _DOCS_COMPATIBLE_SET:
            if s.get("artifact_mode") != "docs":
                return False
    return True
# Code-lane plan: no docs steps allowed.
for s in steps:
    if isinstance(s, dict) and s.get("artifact_mode") == "docs":
        return False
return True
```

This is a hard binary: any plan with a mix of docs and code steps fails validation. The planner fallback then produces a single-step SEARCH plan with the instruction truncated to 200 characters (line 139 of `planner.py`). Mixed intent is silently discarded.

### 2.4 Retries and Replanning Are Lane-Local

**`agent/orchestrator/replanner.py` → `_enforce_replan_lane_contract()` and `_dominant_lane()`**

The replanner always reads `dominant_artifact_mode` from `state.context` to determine the current lane and enforces that the new plan stays within that lane. There is no mechanism for the replanner to shift to a different lane, even if the failure reason is that the original lane was wrong for part of the task. Lane lock is absolute during an attempt.

**`agent/execution/step_dispatcher.py` → `_enforce_runtime_lane_contract()` (lines 73–120)**

Runtime lane enforcement is applied on every `dispatch()` call. Steps that violate the stored `dominant_artifact_mode` return a `FATAL_FAILURE` lane violation result immediately. This is correct behavior for the current model but becomes a blocker when phases legitimately change lanes.

### 2.5 Intent Is Flattened Too Early

**`agent/orchestrator/plan_resolver.py` → `get_plan()` (lines 200–275)**

The docs heuristic fires at line 201, the router at line 215. By line 240, the plan is already a short-circuit single-step plan for `CODE_SEARCH`, `CODE_EXPLAIN`, or `INFRA`. By line 280, the planner has already run and collapsed the instruction into one flat step sequence. After `get_plan` returns, there is no structured representation of what the user actually wanted to achieve beyond the flat step list. Any compound intent — "find the docs, then explain the replanner flow" — is resolved by this point into a single lane with a single action sequence. The richness of the original instruction is gone.

---

## 3. Why the Current Model Is Insufficient

### 3.1 "Find Architecture Docs and Explain Replanner Flow"

This is a natural, reasonable, multi-step agent task. The user wants two things: first, locate documentation artifacts; second, explain a code flow. A capable coding agent would decompose this into two sequential subgoals with different retrieval strategies and different success criteria.

In the current architecture, this instruction hits `_is_docs_artifact_intent()`, which returns `False` because `"explain"` is in `_NON_DOCS_TOKENS`. The router returns `CODE_EXPLAIN`. The plan becomes a single code-lane `EXPLAIN` step. Docs discovery never runs. The user gets a code-only explanation without the architectural context they asked for. This is a category-level failure, not a retrieval quality issue. No amount of retrieval tuning fixes it.

### 3.2 "Locate Symbol → Inspect Implementation → Explain Behavior"

A multi-hop task: first locate a symbol in the codebase, then read its implementation, then explain what it does. These are three semantically distinct subgoals. Today, if this routes to `CODE_EXPLAIN`, it becomes a single EXPLAIN step. The EXPLAIN step's retrieval may or may not surface the right symbol body. If retrieval is weak, the replanner produces a new SEARCH → EXPLAIN plan — still in the same code lane, still without a structured locate-then-inspect-then-explain phase structure. The replanner cannot know that the user wanted a deliberate locate step before the explain step.

### 3.3 "Search → Edit → Validate"

One of the most important coding agent patterns: search the codebase to understand what needs to change, make the edit, then run tests to validate. This is arguably the defining multi-phase task. Today it routes to `CODE_EDIT` → planner → multi-step plan. The plan may happen to contain SEARCH → EDIT steps. But there is no per-phase validation, no concept that the search phase succeeded before the edit phase starts, no escalation if the search phase produced weak context and the edit would be dangerous. The edit phase starts immediately after the search steps, regardless of search quality, with no parent-level gate between them.

### 3.4 Why These Are Architectural, Not Retrieval/Prompt Issues

All three examples above share the same root cause: the platform has **no way to represent "I want to do X, then do Y, where X and Y are independent subgoals with independent success criteria."** That is a structural absence. You cannot express it in the current `steps` list because all steps share one lane, one `dominant_artifact_mode`, one replanning namespace, and one global goal evaluation. The only way to fix this is to introduce the concept of a **phase** — an independently bounded, lane-owning, validatable unit of work — and a **parent orchestrator** that sequences phases and applies policy between them.

---

## 4. Target Architecture

### 4.1 Conceptual Model

```
User Instruction
        │
        ▼
  Parent Decomposer
  (extract ordered phases from instruction)
        │
        ▼
  ParentPlan
  ┌─────────────────────────────────────────────────────────┐
  │  Phase 1: subgoal="find architecture docs"              │
  │           lane="docs"                                   │
  │           steps=[SEARCH_CANDIDATES, BUILD_CONTEXT, EXPLAIN] │
  │           validation=PhaseValidationContract            │
  ├─────────────────────────────────────────────────────────┤
  │  Phase 2: subgoal="explain replanner flow"              │
  │           lane="code"                                   │
  │           steps=[SEARCH, BUILD_CONTEXT, EXPLAIN]        │
  │           validation=PhaseValidationContract            │
  └─────────────────────────────────────────────────────────┘
        │
        ▼  (execute phases in order)
  Phase 1 Execution
  ├── execution_loop (single-lane, existing)
  ├── PhaseResult(success=True, context_output=...)
  └── Parent Policy: CONTINUE
        │
        ▼
  Phase 2 Execution (may receive Phase 1 context)
  ├── execution_loop (single-lane, existing)
  ├── PhaseResult(success=True, context_output=...)
  └── Parent Policy: DONE
        │
        ▼
  ParentGoalState: all phases succeeded → task complete
```

### 4.2 Parent Task

The parent task is the original user instruction. It owns the `ParentPlan` (ordered phase list), the `ParentExecutionState` (phase statuses, accumulated outputs, escalation history), and the `ParentGoalState` (whether all required phases have succeeded). The parent is not an LLM agent — it is a deterministic orchestrator that sequences phases and applies a policy function between them.

### 4.3 Phase Decomposition

A **phase** is an independently bounded subgoal. Each phase has:
- A subgoal description (what this phase is trying to accomplish)
- A lane assignment (`"code"` or `"docs"`) — this is **phase-local**, not task-global
- An ordered step list (the flat plan for this phase, same format as today)
- A `PhaseValidationContract` (what constitutes success for this phase)
- A `PhaseRetryPolicy` (max retries, allowed escalation paths)

Phases are ordered. Each phase must complete (or be explicitly skipped by parent policy) before the next begins.

### 4.4 Per-Phase Lane Ownership

Lane ownership is **phase-local**, not task-global. A task with a docs phase followed by a code phase has `dominant_artifact_mode = "docs"` during Phase 1 and `dominant_artifact_mode = "code"` during Phase 2. The existing single-lane machinery (dispatcher lane enforcement, replanner lane contract, validate_plan) operates **within each phase** exactly as it does today. No changes to those components are required to make them work per-phase — only the scope of "task" needs to change to "phase."

### 4.5 Per-Phase Validation Contract

Each phase has an independent success criterion. Examples:
- **Docs discovery phase:** at least one docs artifact in `ranked_context`, EXPLAIN succeeded.
- **Code search phase:** `ranked_context` non-empty, anchor coverage above threshold.
- **Edit phase:** at least one patch applied, no lane violations.
- **Validation phase:** test run succeeded or no test failures introduced.

These success criteria map closely to what `GoalEvaluator.evaluate_with_reason` computes today, scoped to a single phase rather than the full task.

### 4.6 Per-Phase Retry / Replan / Grounding Checks

Within a phase, the existing retry and replanning machinery runs unchanged. The replanner operates within the phase lane. The stall detector operates within the phase attempt count. If a phase exhausts its retry budget without success, it returns a `PhaseResult` with `success=False` and a `failure_class` explaining why.

### 4.7 Parent-Level Policy and Escalation

The parent orchestrator receives each `PhaseResult` and applies a deterministic policy:

| Phase outcome | Parent policy options |
|---|---|
| `success=True` | CONTINUE to next phase (nominal path) |
| `success=False`, `failure_class=insufficient_grounding` | RETRY phase (up to parent retry budget) |
| `success=False`, `failure_class=lane_violation` | STOP (configuration error) |
| `success=False`, `failure_class=goal_not_satisfied` | REPLAN phase (emit new phase plan via replanner) |
| Phase cannot be grounded safely | REQUEST_CLARIFICATION (last resort) |
| All retry/replan budgets exhausted | STOP with partial result |

The parent does not reason about individual steps — it only reasons about phase outcomes and applies policy at the phase boundary. This is the escalation-at-the-narrowest-scope-first principle: retry at step level first (within phase), then replan at phase level, then escalate to parent, then clarify.

---

## 5. Core Design Principles

### P1 — Preserve Single-Lane Path as Compatibility Mode

The current flat-plan, single-lane execution path must remain fully functional throughout migration. A `ParentPlan` with exactly one phase, where that phase contains the exact flat plan produced today, is behaviorally identical to the current system. Stage 1 of migration is precisely this: wrap without changing.

### P2 — Lane Ownership Is Phase-Local, Not Task-Global

The `dominant_artifact_mode` field in `state.context` becomes scoped to a single phase execution. Between phases, a new `AgentState` is constructed (or the context is reset) with the new phase's lane. The field itself does not change; only its lifecycle changes from task-global to phase-local.

### P3 — Keep Validation Strict Within a Phase

`validate_plan` continues to reject mixed-lane plans. This is correct and should not change. The phase itself is single-lane. Mixing lanes within a single phase is still forbidden. The change is that the parent plan can have multiple phases with different lanes — not that a single phase can be mixed.

### P4 — Parent Policy Owns Continuation / Escalation

No component below the parent orchestrator should make cross-phase decisions. The replanner does not know it is inside a phase. The dispatcher does not know there is a next phase. The goal evaluator evaluates the current phase only. All cross-phase logic lives in the parent orchestrator's policy function.

### P5 — Ambiguity Escalates at the Narrowest Scope First

Step failure → step retry (within phase, existing). Phase goal not met → phase replan (within phase, existing replanner). Phase cannot be grounded at all → parent policy decision. Parent cannot safely decompose → clarification. Clarification is the last resort, not the first response.

### P6 — Reuse Current Modules; Do Not Rewrite Them

The retrieval pipeline, context builder, patch pipeline, dispatcher, validator, goal evaluator, and replanner are **not** changing in any substantive way. They are wrapped inside a phase boundary and their scope changes from task to phase. This is the "extend, do not replace" principle from the architecture rules.

### P7 — Decomposition Is Bounded and Deterministic Where Possible

Phase decomposition should not be a free-form LLM call if it can be expressed as a bounded heuristic or a small model classification. The existing docs-heuristic and router patterns are the right model: deterministic detection → known phase template. Only when detection fails does an LLM decompose the instruction. Even then, the decomposition output must be validated against a known schema.

### P8 — A Two-Phase Task Is the Initial Target; Avoid Over-Engineering Depth

The immediate goal is to support **two ordered phases**. Nested phases, deeply recursive decomposition, and dynamic phase injection at runtime are not in scope for Stage 2 or Stage 3. That complexity is deliberately deferred until two-phase execution is stable and well-tested.

---

## 6. Minimal New Schemas

These schemas are **design descriptions**, not implementation code. Exact Python types and field names are determined during implementation.

### 6.1 PhasePlan

Represents one bounded subgoal and its execution strategy.

```json
{
  "phase_id": "phase_a1b2c3d4",
  "phase_index": 0,
  "subgoal": "Find architecture documentation artifacts",
  "lane": "docs",
  "steps": [
    {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", ...},
    {"id": 2, "action": "BUILD_CONTEXT", "artifact_mode": "docs", ...},
    {"id": 3, "action": "EXPLAIN", "artifact_mode": "docs", ...}
  ],
  "validation": {
    "require_ranked_context": true,
    "require_explain_success": true,
    "min_candidates": 1
  },
  "retry_policy": {
    "max_retries": 2,
    "allowed_failure_classes": ["insufficient_grounding", "goal_not_satisfied"]
  }
}
```

Note: `steps` is identical in structure to the existing flat plan step list. The existing planner, replanner, validate_plan, and execution_loop consume it without modification.

### 6.2 ParentPlan

Wraps an ordered list of phases and identifies the originating instruction.

```json
{
  "parent_plan_id": "pplan_3f8b8a7d",
  "instruction": "Find architecture docs and explain replanner flow",
  "decomposition_type": "sequential_two_phase",
  "phases": [
    { ... PhasePlan 1 ... },
    { ... PhasePlan 2 ... }
  ],
  "compatibility_mode": false
}
```

`compatibility_mode: true` means `phases` contains exactly one phase whose `steps` is the current flat plan. This flag signals to the parent orchestrator that it is running a legacy single-phase plan and no multi-phase behavior applies.

### 6.3 PhaseResult

Returned by the phase executor when a phase completes (success or failure).

```json
{
  "phase_id": "phase_a1b2c3d4",
  "phase_index": 0,
  "success": true,
  "failure_class": null,
  "goal_met": true,
  "goal_reason": "explain_like_explain_succeeded",
  "completed_steps": 3,
  "context_output": {
    "ranked_context": [...],
    "retrieved_symbols": [...],
    "files_modified": [],
    "patches_applied": []
  },
  "attempt_count": 1,
  "elapsed_ms": 1240
}
```

`context_output` is the slice of `AgentState.context` that the parent orchestrator may pass to the next phase as initial context.

### 6.4 ParentExecutionState Additions

Added to the existing `AgentState.context` when running under parent orchestration:

```json
{
  "parent_plan_id": "pplan_3f8b8a7d",
  "current_phase_index": 1,
  "phase_results": [
    { ... PhaseResult 0 ... }
  ],
  "parent_policy_history": [
    {"phase_index": 0, "decision": "CONTINUE", "reason": "phase_succeeded"}
  ],
  "phase_context_handoff": {
    "from_phase_index": 0,
    "ranked_context": [...],
    "retrieved_symbols": [...]
  }
}
```

### 6.5 ParentGoalState

Evaluated at the end of all phases by the parent orchestrator.

```json
{
  "all_required_phases_succeeded": true,
  "any_phase_failed": false,
  "escalation_count": 0,
  "final_decision": "DONE",
  "phase_summary": [
    {"phase_index": 0, "success": true},
    {"phase_index": 1, "success": true}
  ]
}
```

---

## 7. Module-by-Module Migration Strategy

### 7.1 `agent/orchestrator/plan_resolver.py`

**Current role:** Entry point for plan production. Applies docs heuristic, router short-circuits, and planner. Returns one flat plan.

**What breaks for hierarchical phases:** `get_plan` has no concept of multiple phases. It cannot return a `ParentPlan` with multiple `PhasePlan` objects.

**Recommended category:** EXTEND

**New responsibilities after migration:**
- Add a `get_parent_plan(instruction, ...) -> ParentPlan` function alongside `get_plan`.
- `get_parent_plan` calls existing decomposition logic and, when mixed intent is detected, produces a two-phase `ParentPlan`.
- When single intent is detected, `get_parent_plan` wraps `get_plan` output in a one-phase `ParentPlan` with `compatibility_mode=True`.
- The existing `get_plan` function is **not modified**. It remains the entry point for single-phase and compatibility-mode execution.

### 7.2 `planner/planner.py`

**Current role:** LLM-driven plan generation. Produces one flat step list. Normalizes and validates. Handles fallbacks.

**What breaks for hierarchical phases:** Nothing. The planner produces a flat step list for a single phase. That is exactly what is needed per phase.

**Recommended category:** KEEP AS-IS

**New responsibilities after migration:** None. The planner is called once per phase, with a phase-scoped instruction (the subgoal description, not the full parent instruction). The phase runner wraps `plan(subgoal_instruction)`. The planner does not need to know it is inside a phase.

### 7.3 `planner/planner_utils.py`

**Current role:** Action normalization, plan validation, docs-lane structure detection, step sequence extraction.

**What breaks for hierarchical phases:** `validate_plan` rejects mixed-lane plans — but that is **correct behavior**. Each phase is single-lane. `is_explicit_docs_lane_by_structure` is phase-scoped (operates on one phase's step list). Nothing breaks.

**Recommended category:** KEEP AS-IS

**New responsibilities after migration:** None. These utilities operate on a single phase's flat step list. They are used unchanged by the phase executor.

### 7.4 `agent/orchestrator/deterministic_runner.py`

**Current role:** Top-level Mode 1 entry point. Calls `get_plan`, derives `dominant_artifact_mode` once, creates `AgentState`, runs `execution_loop`.

**What breaks for hierarchical phases:** The single `dominant_artifact_mode` derivation at line 41 and the single `execution_loop` call at line 73 are the structural bottleneck. In a multi-phase world, this runner must sequence phase execution, not task execution.

**Recommended category:** WRAP

**New responsibilities after migration:**
- Introduce `run_hierarchical(parent_plan, ...)` that iterates over phases.
- Per phase: derive phase-scoped `dominant_artifact_mode`, create phase-scoped `AgentState`, call existing `execution_loop`, collect `PhaseResult`.
- After each phase: apply parent policy function.
- The existing `run_deterministic` function is **not modified**. It is the single-phase compatibility path, called by `run_hierarchical` when `compatibility_mode=True`.

### 7.5 `agent/orchestrator/execution_loop.py`

**Current role:** Step iteration, validation, replan, goal evaluation. Shared by deterministic and agent modes.

**What breaks for hierarchical phases:** Nothing. The execution loop already operates on a step list within a bounded `AgentState`. The scope of that state is already conceptually "one plan attempt." Making that one phase instead of one task is a naming change, not a behavioral change.

**Recommended category:** KEEP AS-IS

**New responsibilities after migration:** None. The execution loop runs per-phase. The `AgentState` it receives is phase-scoped. `GoalEvaluator` evaluates the phase goal. Replanner replans within the phase lane. The loop does not know it is a phase rather than a full task.

### 7.6 `agent/orchestrator/replanner.py`

**Current role:** LLM-driven plan revision on failure. Enforces lane contract. Returns new plan.

**What breaks for hierarchical phases:** Nothing within a phase. The replanner is phase-local by the architecture — it receives the phase's `AgentState` and failure information, and produces a new step list for the current phase. It does not need to know about other phases.

The only risk is that the replanner might need to be told "you are replanning phase 2 of 3" for context quality — but this is a prompt concern (out of scope here) and can be addressed by passing `phase_context` as part of `retry_context`.

**Recommended category:** KEEP AS-IS (minor extension later)

**New responsibilities after migration:** Accept optional `phase_context` in `retry_context` to provide the replanner with phase-level framing. This is a backward-compatible addition to the existing `retry_context` dict.

### 7.7 `agent/orchestrator/goal_evaluator.py`

**Current role:** Deterministic evaluation of task success from `AgentState`. Returns `(bool, reason, signals)`.

**What breaks for hierarchical phases:** `GoalEvaluator.evaluate_with_reason` currently evaluates the whole task. In a phase context, it should evaluate whether **this phase's subgoal** was met. The subgoal may be narrower than the full instruction (e.g., "find docs" vs. "find docs and explain replanner").

**Recommended category:** EXTEND

**New responsibilities after migration:**
- Add an optional `phase_subgoal: str | None` parameter to `evaluate_with_reason`. When provided, evaluation uses the phase subgoal instead of the full instruction for `is_explain_like_instruction` and goal signal matching.
- When `phase_subgoal` is `None`, behavior is identical to today.
- The parent orchestrator calls `evaluate_with_reason(phase_subgoal, phase_state)` per phase.
- A separate `evaluate_parent_goal(parent_plan, phase_results) -> (bool, reason)` function is added to aggregate phase results into a final task outcome.

### 7.8 `agent/execution/step_dispatcher.py`

**Current role:** Per-step dispatch. Enforces lane contract from `state.context["dominant_artifact_mode"]`. Routes action to tool. Handles EDIT, SEARCH, EXPLAIN, INFRA, BUILD_CONTEXT.

**What breaks for hierarchical phases:** Nothing. The dispatcher reads `dominant_artifact_mode` from `state.context`. If the phase executor sets this correctly for the current phase before calling `execution_loop`, the dispatcher operates correctly without any changes.

**Recommended category:** KEEP AS-IS

**New responsibilities after migration:** None. The dispatcher is already phase-correct because it reads lane from `state.context`, which is phase-scoped by the phase executor.

---

## 8. Recommended First Implementation Stages

### Stage 1 — Parent Plan Wrapper with Single-Phase Compatibility Path

**Scope:** Introduce `ParentPlan`, `PhasePlan`, and `PhaseResult` schemas. Introduce `run_hierarchical` in `deterministic_runner.py`. When a standard single-intent instruction is received, `get_parent_plan` wraps the existing `get_plan` output in a `ParentPlan` with one phase and `compatibility_mode=True`. `run_hierarchical` detects compatibility mode and delegates to `run_deterministic` unchanged.

**Invariants:**
- All existing single-intent scenarios pass with zero behavioral change.
- `run_deterministic` is not modified.
- `validate_plan`, `planner.py`, `execution_loop`, `replanner`, `step_dispatcher` are not modified.
- Schema objects are pure data structures; no execution logic in them.

**Blast radius:** New code only. Existing paths completely untouched.

**Risks:** Schema design commits early. Keep schemas minimal (as defined in §6) to avoid over-constraining Stage 2.

**Test strategy:**
- Schema validation tests for `PhasePlan`, `ParentPlan`, `PhaseResult`.
- Compatibility mode round-trip test: existing instruction → `get_parent_plan` → `compatibility_mode=True` → `run_hierarchical` → same output as `run_deterministic`.
- All existing scenario tests must pass without modification.

**Rollout gate:** All existing tests pass. No regressions. New schema tests pass.

---

### Stage 2 — Two-Phase Sequential Execution for Mixed-Lane Tasks

**Scope:** Implement genuine two-phase execution. Add `_is_two_phase_mixed_intent(instruction)` heuristic in `plan_resolver.py`. When fired, `get_parent_plan` emits a two-phase `ParentPlan`: Phase 0 is docs lane (using existing `_docs_seed_plan`), Phase 1 is code lane (using `plan(instruction)` for the code subgoal). `run_hierarchical` executes both phases in order using the existing `execution_loop`. `GoalEvaluator` is called per phase using the phase subgoal.

Phase context handoff: after Phase 0 succeeds, its `ranked_context` is injected into Phase 1's initial `AgentState.context` so the code-explain phase can reference docs found in Phase 0.

**Invariants:**
- Single-intent instructions route to Stage 1 compatibility path. Zero behavioral change for them.
- Each phase is single-lane. `validate_plan` unchanged. Lane enforcement in dispatcher unchanged.
- Phase 1 does not start if Phase 0 returns `success=False` (parent policy: STOP on first-phase failure).
- No changes to `replanner.py`, `step_dispatcher.py`, `execution_loop.py`, `planner_utils.py`.

**Blast radius:** New two-phase decomposition branch in `plan_resolver.py`. New phase iteration logic in `run_hierarchical`. `GoalEvaluator` gets optional `phase_subgoal` parameter (backward-compatible). No other changes.

**Risks:**
- Phase subgoal derivation: splitting "Find architecture docs and explain replanner flow" into two subgoal descriptions must be deterministic or use a small model with fallback.
- Context handoff: passing Phase 0's `ranked_context` to Phase 1 increases Phase 1 context size. Pruning must be applied.
- Heuristic false positives: `_is_two_phase_mixed_intent` must be narrow. It should only fire on clear docs+code combinations, not on ambiguous phrases.

**Test strategy:**
- Mixed-intent integration test: "Find architecture docs and explain replanner flow" → `ParentPlan` with two phases → both execute → `ParentGoalState` with both phases succeeded.
- Phase-local validation tests: Phase 0 failure → parent stops → Phase 1 does not run.
- Backward compatibility: all existing single-phase tests pass.
- Phase context handoff test: Phase 1 initial context includes Phase 0 `ranked_context`.

**Rollout gate:** Two-phase integration test passes. No regressions on existing scenarios. Phase boundary contract tests pass.

---

### Stage 3 — Phase-Local Replanning and Parent Policy Escalation

**Scope:** Implement the full parent policy function. Each `PhaseResult` failure class triggers a deterministic parent policy decision: RETRY, REPLAN, STOP, or REQUEST_CLARIFICATION. Phase-local retries are already handled by `execution_loop` (no change). This stage adds parent-level retry budget per phase and parent-level replan trigger (call replanner at parent scope, not within the phase's execution loop).

Introduce `PhaseRetryPolicy` enforcement: if a phase fails and has remaining parent-level retry budget, the parent orchestrator re-runs the phase with the failure context passed as `retry_context`. This is distinct from the step-level replanning already in `execution_loop` — it is a full re-execution of the phase, not a single step replan.

Clarification becomes an explicit policy outcome: when a phase has no remaining retries, no safe replan path, and the failure is `insufficient_grounding` or `goal_not_satisfied`, the parent emits a clarification request rather than silently failing.

**Invariants:**
- Phase execution itself (execution_loop, replanner, dispatcher) is unchanged.
- Parent policy is deterministic: given `PhaseResult.failure_class` and retry counts, the policy output is deterministic.
- Clarification is only a parent-level outcome, never a phase-level one.
- `compatibility_mode=True` plans are not subject to parent policy (they use existing stall detection).

**Blast radius:** New parent policy function. `ParentExecutionState` additions. `PhaseRetryPolicy` enforcement. No changes to existing execution path modules.

**Risks:**
- Parent retry budget must be coordinated with phase-internal retry budget to avoid double-counting.
- Clarification output format must be defined (does it surface as a structured result to the caller, or as a special PhaseResult type?).

**Test strategy:**
- Parent policy unit tests: each `failure_class` → expected parent policy decision.
- Phase retry integration test: phase fails, parent retries, phase succeeds on second attempt.
- Clarification path test: phase exhausts parent retries → clarification outcome.
- Parent stall prevention test: parent retry budget cap prevents infinite loop.

**Rollout gate:** Parent policy tests pass. Phase retry integration tests pass. No regressions.

---

### Stage 4 — Broader Decomposition Patterns (Deferred, Justified Only)

**Scope:** Support more than two phases, richer subgoal types (e.g., SEARCH → EDIT → TEST as three phases), and decomposition of instructions into N subgoals where N > 2.

**Condition for unlocking:** Stage 3 is stable in production. Metrics show measurable user-facing failure classes that Stage 3 cannot address. Specific failure patterns justify a third phase. The team has tested two-phase execution thoroughly.

**Explicitly not in Stage 4:**
- Free-form LLM-as-orchestrator (replaces the deterministic parent policy — forbidden by architecture rules).
- Nested or recursive phases.
- Dynamic phase injection mid-execution (a phase adds new phases based on its output).
- Parallel phase execution (phases are always sequential).

**Test strategy:** Only if and when this stage is approved. Do not write tests for Stage 4 capabilities during Stage 1–3 implementation.

**Rollout gate:** Must be explicitly approved as a separate architecture decision document.

---

## 9. Fallback and Clarification Policy

Clarification is a **fallback safety mechanism**, not an architectural direction. The following rules govern when it applies.

**Clarification is valid when:**
1. The parent decomposer cannot safely split the instruction into phases (the instruction is ambiguous in a way that would require guessing the user's priority between incompatible subgoals).
2. A phase has exhausted its entire retry and replan budget without succeeding.
3. A phase cannot be safely grounded (e.g., no retrieval candidates exist for the subgoal, and the system cannot make meaningful progress).

**Clarification is NOT valid when:**
- The instruction is decomposable into known phase patterns (docs + code, search + edit, locate + explain).
- Phase failure is due to a retrievable issue (weak retrieval, wrong anchor) — use retry and replan instead.
- The instruction is unambiguous but maps to a multi-phase task — decompose it, do not ask for clarification.

**Implementation rule:** Clarification output must be a structured `ParentGoalState` outcome, not an execution of an EXPLAIN step with a clarification prompt template. The distinction matters: a clarification result is a terminal decision by the parent policy, not a "plan step" executed in the current lane.

---

## 10. Testing Strategy

### 10.1 Before Implementation (Stage 1)

**Architecture contract tests:**
- `test_parent_plan_schema_valid`: `ParentPlan` with `compatibility_mode=True` and one `PhasePlan` passes schema validation.
- `test_phase_plan_schema_valid`: `PhasePlan` with a docs-lane step list passes schema validation.
- `test_parent_plan_compatibility_mode_wraps_existing_plan`: `get_parent_plan` for a simple "add docstring" instruction returns a `ParentPlan` with `compatibility_mode=True` whose single phase's steps match `get_plan` output exactly.
- `test_run_hierarchical_compatibility_mode_matches_run_deterministic`: For any single-intent instruction, `run_hierarchical` and `run_deterministic` produce identical `AgentState` outputs.

### 10.2 During Stage 2 Rollout

**Phase validation tests:**
- `test_phase_goal_evaluation_uses_phase_subgoal`: `GoalEvaluator.evaluate_with_reason(phase_subgoal="find docs", state)` returns `True` when docs phase succeeded, even if full instruction is mixed.
- `test_phase_validation_contract_docs_phase_success`: A Phase 0 with `ranked_context` containing docs and a successful EXPLAIN step satisfies the docs phase `PhaseValidationContract`.
- `test_phase_validation_contract_docs_phase_failure_empty_context`: A Phase 0 with empty `ranked_context` fails the docs phase contract.

**Mixed-intent integration tests:**
- `test_two_phase_docs_then_code_end_to_end`: "Find architecture docs and explain replanner flow" → `ParentPlan` with two phases → Phase 0 executes docs lane → Phase 1 executes code lane → `ParentGoalState.all_required_phases_succeeded = True`.
- `test_two_phase_phase0_failure_stops_execution`: Phase 0 fails → parent policy STOP → Phase 1 does not execute → `ParentGoalState.any_phase_failed = True`.

**Backward compatibility tests:**
- All existing tests in `tests/test_execution_loop.py`, `tests/test_goal_evaluator*.py`, `tests/test_replanner.py`, `tests/test_plan_resolver_docs_intent.py`, `tests/test_general_platform_scenarios.py` must pass without modification.

### 10.3 Expected-Failure Tests for Future Capabilities

**Stage 1 xfail tests (document the gap, do not implement):**
- `test_desired_three_phase_search_edit_test`: "Search for validate_plan, edit it, then run its tests" → currently collapses to single-phase CODE_EDIT. Mark xfail until Stage 4.
- `test_desired_phase_parallel_execution`: Two independent phases execute concurrently. Mark xfail; parallel execution is explicitly deferred.

These tests serve as architectural contracts: they document what the current system cannot do and prevent those capabilities from being accidentally claimed as solved.

---

## 11. Final Recommendation

### Recommended Direction: Hierarchical Phased Orchestration (Design A Extended)

The platform must move toward hierarchical phased orchestration. This means: one instruction, structured decomposition into ordered phases, each phase executing in its own lane with independent validation and retry policy, and a parent orchestrator that applies deterministic policy between phases.

This is the right architecture for three reasons:

**First, it solves real failures without retrieval hacks.** The "find docs + explain code" failure class, the "locate → inspect → explain" multi-hop class, and the "search → edit → validate" class are all architectural mismatches between user intent and the current single-phase model. No retrieval improvement, prompt tuning, or heuristic expansion fixes them. They require structural change.

**Second, it is a staged, low-blast-radius migration.** Stage 1 introduces zero behavioral change. Stage 2 adds two-phase execution for a specific mixed-intent pattern. Stages 3 and 4 are gated on Stage 2 stability. The entire migration preserves the existing single-lane path as the compatibility mode. The risk profile is similar to the Phase 6A migration that introduced single-lane contracts: additive, observable, and rollback-safe.

**Third, it moves the platform toward the right class of agent behavior.** High-end coding agents — Devin-class, Cursor-class, and similar systems — all use some form of hierarchical planning: a high-level goal decomposed into sequential phases or subgoals, each handled by a bounded execution unit, with a parent orchestrator managing continuation. This is not academic architecture. It is the architecture that allows agents to handle "search the codebase, understand the pattern, edit three files consistently, validate the change" as a coherent planned unit rather than as a lucky outcome from a single flat plan.

### What Is Explicitly Rejected

**Design C (clarification as primary)** is rejected as the main direction. It is correct as a fallback — when decomposition genuinely fails, clarification is the right response. It is wrong as a strategy — it treats mixed intent as an error to report rather than a task to accomplish. The platform's job is to do the work, not to refuse work that requires thinking.

**Free-form LLM orchestration** (an LLM selecting phases and tools dynamically) is rejected. This violates the architecture rule that LLMs must not select tools directly. The parent orchestrator is deterministic. LLMs produce phase-scoped plans (subgoal → flat step list), as they do today. The orchestrator sequences them.

**Big-bang rewrite** of the planner, replanner, or execution loop is rejected. Every module in §7 is classified as keep-as-is, wrap, or extend. Nothing is replaced. The migration path is additive.

### The Platform This Becomes

At Stage 3 completion, AutoStudio handles:
- Single-intent tasks: exactly as today (compatibility path, zero regression).
- Mixed docs+code tasks: two-phase execution, docs context handed to code phase, independent validation per phase.
- Grounding failures: phase-local retry and replan, then parent-level retry, then clarification as last resort.

At Stage 4 completion (future, gated), AutoStudio handles:
- Three-phase "search → edit → test" patterns as first-class tasks.
- Richer subgoal decomposition aligned with the natural structure of coding agent workflows.

This is a coding agent platform that plans like a senior engineer: understand the task, break it into ordered steps at the right granularity, execute each step with its own success criteria, and know when to retry, replan, or ask for help.

---

*End of architecture plan.*
