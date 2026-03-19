# Hierarchical Phased Orchestration — Execution Roadmap

**Type:** Implementation Execution Roadmap  
**Status:** Approved for implementation. Stages 1–2 are actionable.  
**Date:** 2026-03-20  
**Source plan:** `Docs/HIERARCHICAL_PHASED_ORCHESTRATION_PLAN.md`  
**Scope:** Stage 1 and Stage 2 are fully specified. Stages 3–4 are outlined as gates, not implementation specs.

---

## 1. Executive Summary

### 1.1 Approved Direction in Implementation Terms

The approved direction is hierarchical phased orchestration: one instruction decomposes into an ordered sequence of `PhasePlan` objects, each executed independently by the existing `execution_loop()`, with a deterministic parent orchestrator managing sequencing and policy between phases.

In implementation terms this means:

- Two new modules: a schema module defining `ParentPlan`, `PhasePlan`, `PhaseResult`; and a parent orchestrator function `run_hierarchical()`.
- One new function in `plan_resolver.py`: `get_parent_plan()`.
- A backward-compatible extension to `goal_evaluator.py` for phase-scoped evaluation.
- Zero changes to `execution_loop()`, `step_dispatcher.py`, `planner.py`, `planner_utils.py`, `replanner.py`, or `validate_plan()`.

### 1.2 Stage 1 Is Zero-Regression, Compatibility-First

Stage 1 introduces the schema and the wrapper infrastructure. For every instruction that enters the system today, the behavior is **exactly identical** to the current `run_deterministic()` path. The parent plan wraps the existing plan in a single-phase `ParentPlan` with `compatibility_mode=True`. `run_hierarchical()` detects this flag and delegates directly to `run_deterministic()`. No production code path changes. No test regressions.

Stage 1 is complete when: new schema tests pass, the compatibility round-trip test passes, and all existing scenario tests pass unchanged.

### 1.3 Stage 2 Is the First Real Mixed-Lane Capability

Stage 2 introduces the ability to execute a two-phase `ParentPlan` where Phase 0 runs in the docs lane and Phase 1 runs in the code lane. This is the first time the platform will successfully handle "find architecture docs and explain replanner flow" as two sequential, independently validated subgoals.

Stage 2 changes are additive: a new detection branch in `plan_resolver.py`, a new phase iteration loop in `run_hierarchical()`, and a backward-compatible `phase_subgoal` parameter in `goal_evaluator.py`. Nothing else changes.

### 1.4 Clarification Is Fallback, Not Strategy

Design C (explicit clarification and rejection of mixed intent) is not part of Stage 1 or Stage 2. It is permitted only as a parent policy outcome in Stage 3 when all retry and replan budgets are exhausted. Any engineer who proposes adding a clarification path before Stage 3 is out of scope for this roadmap.

---

## 2. Target Rollout Shape

### Stage 1 — Parent Wrapper + Compatibility Mode

**In scope:**
- `ParentPlan`, `PhasePlan`, `PhaseResult` data schemas
- `get_parent_plan()` that wraps `get_plan()` in compatibility mode
- `run_hierarchical()` that delegates to `run_deterministic()` in compatibility mode
- Schema validation tests
- Compatibility round-trip tests

**Out of scope:**
- Two-phase execution
- Mixed-intent detection
- Phase context handoff
- Per-phase goal evaluation
- Any change to `execution_loop`, `replanner`, `step_dispatcher`, `planner`, `validate_plan`

---

### Stage 2 — Two-Phase Sequential Mixed-Lane Support

**In scope:**
- `_is_two_phase_docs_code_intent()` detection heuristic in `plan_resolver.py`
- `_build_two_phase_parent_plan()` in `plan_resolver.py`
- Phase iteration loop in `run_hierarchical()`
- Per-phase `AgentState` construction with phase-scoped `dominant_artifact_mode`
- Phase 0 → Phase 1 context handoff (ranked_context pass-through)
- Per-phase goal evaluation via `GoalEvaluator` with optional `phase_subgoal`
- STOP on Phase 0 failure (Phase 1 does not start if Phase 0 fails)
- Mixed-intent integration tests
- Parent goal aggregation (all phases succeeded → task complete)

**Out of scope:**
- Parent retry budget (phase-level retry beyond what execution_loop already does)
- Clarification as a parent policy outcome
- Three or more phases
- Phase-level replanning at parent scope
- Any change to `execution_loop`, `replanner`, `step_dispatcher`, `planner`, `validate_plan`
- Nested phases, parallel phases, dynamic phase injection

---

### Stage 3 — Parent Policy and Escalation (Gate)

Not actionable until Stage 2 is stable and tested in production. Requires a separate implementation spec. Summary:
- Parent retry budget per phase (distinct from execution_loop's internal retry)
- Full parent policy function: CONTINUE / RETRY / REPLAN / REQUEST_CLARIFICATION / STOP
- Clarification as a structured parent-level outcome (not an EXPLAIN step)
- Phase-level retry re-execution with failure context as `retry_context`

---

### Stage 4 — Broader Decomposition Patterns (Gate)

Not actionable until Stage 3 is stable. Requires explicit re-approval as a separate architecture decision. Summary:
- Three-phase support (SEARCH → EDIT → TEST pattern)
- Richer subgoal decomposition for N > 2 phases
- More decomposition templates beyond docs+code

---

## 3. Concrete Code-Change Plan by Stage

### 3.1 Stage 1 — Files and Changes

#### Files That Must Change

**NEW: `agent/orchestrator/parent_plan.py`** (new module, pure data)

Purpose: define all Stage 1/2 schemas as dataclasses or typed dicts. No execution logic.

Add:
- `PhasePlan` dataclass / typed dict
- `ParentPlan` dataclass / typed dict
- `PhaseResult` dataclass / typed dict
- `PhaseValidationContract` dataclass / typed dict (minimal, used in Stage 2)
- `PhaseRetryPolicy` dataclass / typed dict (minimal stub, used in Stage 3)
- Helper: `make_compatibility_parent_plan(flat_plan: dict, instruction: str) -> ParentPlan`
- Helper: `validate_parent_plan_schema(parent_plan: ParentPlan) -> bool`

**EXTEND: `agent/orchestrator/plan_resolver.py`**

Add:
- `get_parent_plan(instruction, trace_id, log_event_fn, retry_context) -> ParentPlan`
  - For Stage 1: calls `get_plan(...)` and wraps result in `make_compatibility_parent_plan(...)`
  - For Stage 2: adds mixed-intent branch before wrapping
- Import `make_compatibility_parent_plan` from `parent_plan`

Leave unchanged:
- `get_plan()` — not modified, not deprecated, not renamed
- `_is_docs_artifact_intent()`, `_docs_seed_plan()`, `_ensure_plan_id()`, `new_plan_id()`
- All existing router and planner delegation logic

**EXTEND: `agent/orchestrator/deterministic_runner.py`**

Add:
- `run_hierarchical(instruction, project_root, *, trace_id, similar_tasks, log_event_fn, retry_context, max_runtime_seconds) -> tuple[AgentState, dict]`
  - For Stage 1: reads `parent_plan.compatibility_mode`; if True, calls `run_deterministic(...)` and returns its result directly
  - For Stage 2: iterates over phases in non-compatibility mode

Leave unchanged:
- `run_deterministic()` — not modified, not deprecated, not renamed

**NEW: `tests/test_parent_plan_schema.py`**

New test file: schema validation tests only (see §9).

#### Files That Must NOT Change in Stage 1

- `agent/orchestrator/execution_loop.py`
- `agent/orchestrator/replanner.py`
- `agent/orchestrator/goal_evaluator.py`
- `agent/execution/step_dispatcher.py`
- `planner/planner.py`
- `planner/planner_utils.py`
- `agent/routing/instruction_router.py`
- All existing tests

---

### 3.2 Stage 2 — Files and Changes

#### Files That Must Change

**EXTEND: `agent/orchestrator/plan_resolver.py`** (on top of Stage 1 additions)

Add:
- `_is_two_phase_docs_code_intent(instruction: str) -> bool`
  - Deterministic heuristic: True when instruction has docs-discovery verb AND docs token AND at least one of the non-docs code-intent markers
  - Must be narrower than the existing docs heuristic — must only fire on clear docs+code compound instructions
  - Must not fire when `_is_docs_artifact_intent()` would have returned True (pure docs case)
  - Reference: same token lists as `_is_docs_artifact_intent` (`_DOCS_DISCOVERY_VERBS`, `_DOCS_INTENT_TOKENS`, `_NON_DOCS_TOKENS`) already in `plan_resolver.py`
- `_build_two_phase_parent_plan(instruction: str, trace_id, log_event_fn) -> ParentPlan`
  - Phase 0: docs lane, derived from `_docs_seed_plan(instruction)` — this exact function, unchanged
  - Phase 1: code lane, derived from `plan(instruction)` — the existing planner, unchanged, called with the full parent instruction
  - Populates `PhaseValidationContract` for each phase
  - Returns a `ParentPlan` with `compatibility_mode=False`
- Update `get_parent_plan()` to call `_is_two_phase_docs_code_intent()` before the fallback to `make_compatibility_parent_plan()`

**EXTEND: `agent/orchestrator/deterministic_runner.py`** (on top of Stage 1)

Extend `run_hierarchical()` for non-compatibility mode:
- Add phase iteration loop: for each `PhasePlan` in `parent_plan.phases`:
  - Derive `dominant_artifact_mode` from the `PhasePlan.lane` field (not from `is_explicit_docs_lane_by_structure()`)
  - Construct a fresh `AgentState` for the phase (see §6 for exact fields)
  - Inject Phase 0 context handoff into Phase 1's initial state (see §5.5 for rules)
  - Call `execution_loop(phase_state, phase_subgoal, mode=DETERMINISTIC, ...)`
  - Collect `LoopResult`; build `PhaseResult`
  - Call `_apply_parent_stage2_policy(phase_result, phase_index)` for CONTINUE/STOP decision
  - If STOP: log parent policy event and return immediately with partial result
- Add `_apply_parent_stage2_policy(phase_result, phase_index) -> str` (Stage 2 only: "CONTINUE" or "STOP")
- Add `_aggregate_parent_goal(phase_results: list[PhaseResult]) -> tuple[bool, str]`
- Add `_build_phase_loop_output(phase_results, start_time) -> dict` for the returned loop_output dict

**EXTEND: `agent/orchestrator/goal_evaluator.py`**

Add optional parameter to `evaluate_with_reason`:
- `def evaluate_with_reason(self, instruction: str, state: AgentState, *, phase_subgoal: str | None = None) -> tuple[bool, str, dict]`
- When `phase_subgoal` is not None, use `phase_subgoal` instead of `instruction` for `is_explain_like_instruction()` and all instruction-derived signals
- When `phase_subgoal` is None, behavior is identical to today (backward compatible)

Leave unchanged:
- `evaluate()` — signature unchanged; it calls `evaluate_with_reason` without `phase_subgoal`
- `is_explain_like_instruction()` — not modified

**NEW: `tests/test_two_phase_execution.py`**

New integration test file for Stage 2 (see §9).

**EXTEND: `tests/test_parent_plan_schema.py`**

Add Stage 2 schema tests: two-phase `ParentPlan` schema validation.

#### Files That Must NOT Change in Stage 2

Same as Stage 1, plus:
- `agent/orchestrator/goal_evaluator.py` evaluate() method
- All execution_loop internals
- All replanner internals
- `planner/planner_utils.py` validate_plan() — must remain strict single-lane per phase
- All existing tests

---

## 4. Stage 1 Implementation Contract

### 4.1 Schema Shape

`PhasePlan` must contain at minimum:

```python
@dataclass  # or TypedDict
class PhasePlan:
    phase_id: str          # "phase_" + 8-char hex, generated
    phase_index: int       # 0-based
    subgoal: str           # human-readable description of what this phase accomplishes
    lane: str              # "docs" or "code" — phase-local, not task-global
    steps: list[dict]      # identical format to existing flat plan steps list
    plan_id: str           # the plan_id from the wrapped flat plan
    validation: dict       # PhaseValidationContract (Stage 1: minimal/empty; Stage 2: populated)
    retry_policy: dict     # PhaseRetryPolicy (Stage 1: minimal/empty; Stage 3: enforced)
```

`ParentPlan` must contain at minimum:

```python
@dataclass
class ParentPlan:
    parent_plan_id: str          # "pplan_" + 8-char hex, generated
    instruction: str             # original user instruction, unmodified
    decomposition_type: str      # "compatibility" | "two_phase_docs_code" | (future values)
    phases: list[PhasePlan]      # ordered; Stage 1 always has exactly one entry
    compatibility_mode: bool     # True = single-phase wrapping existing get_plan output
```

`PhaseResult` must contain at minimum:

```python
@dataclass
class PhaseResult:
    phase_id: str
    phase_index: int
    success: bool
    failure_class: str | None    # None on success; one of the FAILURE_CLASS_* constants otherwise
    goal_met: bool
    goal_reason: str
    completed_steps: int
    context_output: dict         # slice of AgentState.context to pass to next phase
    attempt_count: int           # how many execution_loop runs were needed (Stage 1: always 1)
    loop_output: dict            # the loop_output dict from execution_loop
```

`context_output` in `PhaseResult` must include at minimum:
- `ranked_context: list`
- `retrieved_symbols: list`
- `retrieved_files: list`
- `files_modified: list`
- `patches_applied: list`

### 4.2 `get_parent_plan()` in `plan_resolver.py`

**Stage 1 implementation:**

```python
def get_parent_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
) -> ParentPlan:
    """
    Stage 1: wraps get_plan() output in a single-phase compatibility ParentPlan.
    Stage 2+: adds mixed-intent detection before the compatibility fallback.
    """
    flat_plan = get_plan(
        instruction,
        trace_id=trace_id,
        log_event_fn=log_event_fn,
        retry_context=retry_context,
    )
    parent_plan = make_compatibility_parent_plan(flat_plan, instruction)
    if log_event_fn and trace_id:
        log_event_fn(trace_id, "parent_plan_created", {
            "parent_plan_id": parent_plan.parent_plan_id,
            "decomposition_type": parent_plan.decomposition_type,
            "compatibility_mode": parent_plan.compatibility_mode,
            "phase_count": len(parent_plan.phases),
        })
    return parent_plan
```

`make_compatibility_parent_plan(flat_plan, instruction)`:
- `lane` = `"docs"` if `is_explicit_docs_lane_by_structure(flat_plan)` else `"code"`
- `decomposition_type` = `"compatibility"`
- `compatibility_mode` = `True`
- Single `PhasePlan` with `phase_index=0`, `subgoal=instruction[:200]`, `steps=flat_plan["steps"]`, `plan_id=flat_plan["plan_id"]`

### 4.3 `run_hierarchical()` in `deterministic_runner.py`

**Stage 1 implementation:**

```python
def run_hierarchical(
    instruction: str,
    project_root: str,
    *,
    trace_id: str | None = None,
    similar_tasks: list[dict] | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
    max_runtime_seconds: int | None = None,
) -> tuple[AgentState, dict]:
    """
    Stage 1: compatibility delegation to run_deterministic().
    Stage 2+: iterates phases in non-compatibility mode.
    """
    parent_plan = get_parent_plan(
        instruction,
        trace_id=trace_id,
        log_event_fn=log_event_fn,
        retry_context=retry_context,
    )
    if parent_plan.compatibility_mode:
        # Stage 1: identical path to run_deterministic
        flat_plan = parent_plan.phases[0].steps  # not passed; run_deterministic re-calls get_plan
        return run_deterministic(
            instruction,
            project_root,
            trace_id=trace_id,
            similar_tasks=similar_tasks,
            log_event_fn=log_event_fn,
            retry_context=retry_context,
            max_runtime_seconds=max_runtime_seconds,
        )
    # Stage 2+ (not yet implemented in Stage 1; raises NotImplementedError for clarity)
    raise NotImplementedError("Multi-phase execution not yet implemented (Stage 2)")
```

**Note on the compatibility path:** In Stage 1, `run_hierarchical` calls `run_deterministic` for compatibility mode. This means `get_plan` is called twice (once in `get_parent_plan`, once inside `run_deterministic`). This is acceptable in Stage 1 because the call is idempotent. In Stage 2 the flat plan from `get_parent_plan` is used directly — the double-call is eliminated.

### 4.4 What "Behaviorally Identical" Means for Compatibility Mode

For any instruction `I` and project root `R`, the following must hold:

```python
state_a, output_a = run_deterministic(I, R, ...)
state_b, output_b = run_hierarchical(I, R, ...)

# All of these must be equal:
assert output_a["completed_steps"] == output_b["completed_steps"]
assert output_a["files_modified"] == output_b["files_modified"]
assert output_a["patches_applied"] == output_b["patches_applied"]
assert output_a["errors_encountered"] == output_b["errors_encountered"]
assert state_a.instruction == state_b.instruction
assert state_a.step_results == state_b.step_results
```

Trace events may differ (new parent_plan_created event from `run_hierarchical`; this is acceptable).

### 4.5 Tests Required Before Stage 1 Is Complete

See §9 for full test list. The minimum gate is:

1. `test_parent_plan_schema.py::test_phase_plan_schema_fields` — all required fields present
2. `test_parent_plan_schema.py::test_parent_plan_schema_fields` — all required fields present
3. `test_parent_plan_schema.py::test_phase_result_schema_fields` — all required fields present
4. `test_parent_plan_schema.py::test_make_compatibility_parent_plan_single_phase` — one phase, correct lane
5. `test_parent_plan_schema.py::test_make_compatibility_parent_plan_docs_lane` — docs plan → lane="docs"
6. `test_parent_plan_schema.py::test_make_compatibility_parent_plan_code_lane` — code plan → lane="code"
7. `test_parent_plan_schema.py::test_get_parent_plan_compatibility_mode_true` — simple instruction → `compatibility_mode=True`
8. `test_parent_plan_schema.py::test_run_hierarchical_compatibility_delegates_to_run_deterministic` — output identity
9. All existing tests in `tests/` must pass without modification

---

## 5. Stage 2 Implementation Contract

### 5.1 Detection of Two-Phase Mixed Docs+Code Instructions

`_is_two_phase_docs_code_intent(instruction: str) -> bool`

**Firing condition (all must be true):**
1. `_is_docs_artifact_intent(instruction)` is **False** (pure docs case is already handled)
2. Instruction contains at least one token from `_DOCS_DISCOVERY_VERBS` — `("where", "locate", "find", "list", "show")`
3. Instruction contains at least one token from `_DOCS_INTENT_TOKENS` — `("readme", "docs", "documentation", "architecture docs", ...)`
4. Instruction contains at least one token from `_NON_DOCS_TOKENS` that indicates code intent — specifically the code-intent subset: `("explain", "flow", "implemented", "implementation", "class ", "function ", "method ")`

**This function must NOT fire for:**
- Pure docs instructions ("where are the architecture docs") — already handled by `_is_docs_artifact_intent()`
- Pure code instructions ("explain the replanner flow") — no docs token
- Symbol-only or edit instructions without docs markers
- Ambiguous instructions where docs token is incidental

**Implementation note:** Reuse the existing token constants in `plan_resolver.py` directly. Do not duplicate them. Do not introduce new global token lists for Stage 2.

### 5.2 Construction of the Two-Phase Parent Plan

`_build_two_phase_parent_plan(instruction, trace_id, log_event_fn) -> ParentPlan`

**Phase 0 — Docs phase:**
- `lane = "docs"`
- `subgoal` = deterministically derived from instruction (e.g., extract the docs-seeking component — see §5.3)
- `steps` = `_docs_seed_plan(instruction)["steps"]` — the exact existing function, unchanged
- `plan_id` = from `_docs_seed_plan(instruction)["plan_id"]`
- `validation.require_ranked_context = True`
- `validation.require_explain_success = True`
- `validation.min_candidates = 1`

**Phase 1 — Code phase:**
- `lane = "code"`
- `subgoal` = the original `instruction` (full text; the code subgoal is the whole instruction minus the docs part)
- `steps` = `plan(instruction)["steps"]` — call existing planner with full instruction
- `plan_id` = from planner output
- `validation.require_ranked_context = True`
- `validation.require_explain_success` = depends on whether instruction is explain-like

**Why Phase 1 uses the full instruction:** The code explanation phase needs full context about what the user wants explained. Splitting the instruction into "docs part" and "code part" is fragile. The full instruction gives the planner correct context for the code lane subgoal. Phase 1 receives Phase 0's ranked_context as injected prior context (see §5.5).

### 5.3 Phase Subgoal Derivation (Stage 2 Minimal)

For Stage 2, subgoal derivation is minimal and deterministic:

- **Phase 0 subgoal:** `f"Find documentation artifacts relevant to: {instruction[:150]}"` — no splitting, no LLM
- **Phase 1 subgoal:** the original `instruction` unchanged

This is intentionally unsophisticated. It is sufficient for correct per-phase goal evaluation. More precise decomposition is a Stage 4 concern.

### 5.4 Per-Phase Lane Derivation

In `run_hierarchical()`, `dominant_artifact_mode` is derived from the `PhasePlan.lane` field directly:

```python
dominant_artifact_mode = phase_plan.lane  # "docs" or "code"
```

This replaces the `is_explicit_docs_lane_by_structure()` call that currently happens in `run_deterministic()`. The lane is known at plan-construction time for two-phase plans — it does not need to be inferred from plan structure.

For compatibility-mode plans, `run_deterministic()` continues to use `is_explicit_docs_lane_by_structure()` as today.

### 5.5 Phase Context Handoff Rules

After Phase 0 completes successfully, the following fields from Phase 0's `AgentState.context` are injected into Phase 1's initial `AgentState.context`:

**Injected (from Phase 0 → Phase 1):**
- `context["prior_phase_ranked_context"]` = Phase 0's `ranked_context` (full list, not pruned at handoff time)
- `context["prior_phase_retrieved_symbols"]` = Phase 0's `retrieved_symbols`
- `context["prior_phase_files"]` = Phase 0's `retrieved_files`

**Not injected:**
- `step_results` — Phase 1 starts with a fresh step result list
- `current_plan` — Phase 1 has its own plan
- `dominant_artifact_mode` — Phase 1 uses Phase 1's lane ("code"), not Phase 0's ("docs")
- `lane_violations` — reset to empty list for Phase 1
- `_recovery_last`, `termination_reason` — reset for Phase 1

**Usage in Phase 1:** Phase 1's execution can access `prior_phase_ranked_context` from `state.context`. The existing `execution_loop`, `step_dispatcher`, and `replanner` do not need to be modified to support this — they ignore unknown context keys. The prior phase context is available as additional signal for future retrieval improvements but is not required by any Phase 1 component today.

**Pruning:** If Phase 0's `ranked_context` exceeds `MAX_CONTEXT_CHARS // 2` characters (estimated), prune to top-N items before injection. Use `config.agent_config.MAX_CONTEXT_CHARS` as the reference constant.

### 5.6 Per-Phase Goal Evaluation

In `run_hierarchical()`, after each phase's `execution_loop` returns, call:

```python
goal_met, goal_reason, goal_signals = goal_evaluator.evaluate_with_reason(
    phase_plan.subgoal,      # not the full instruction
    phase_state,
    phase_subgoal=phase_plan.subgoal,   # new optional parameter
)
```

For Phase 0 (docs phase), `phase_plan.subgoal` is "Find documentation artifacts...". `is_explain_like_instruction` on this subgoal returns True (contains "find documentation", which maps to `explain_like` via `show where` pattern). The docs-lane path in `evaluate_with_reason` (lines 119–130 of `goal_evaluator.py`) will fire first since `dominant_artifact_mode == "docs"` in Phase 0's state — so Phase 0 goal success is: docs lane + EXPLAIN succeeded. This is already correct behavior without any modification.

For Phase 1 (code phase), `phase_plan.subgoal` = full instruction. `evaluate_with_reason` behavior is unchanged from today.

**The `phase_subgoal` parameter added to `evaluate_with_reason` is for cases where the stored `state.instruction` is the full parent instruction but goal evaluation should use the narrower phase subgoal.** For Stage 2 this is not strictly required because the full instruction works as a Phase 1 subgoal. But the parameter must be added now for correctness in Stage 3.

### 5.7 Stop/Continue Contract Between Phase 0 and Phase 1

`_apply_parent_stage2_policy(phase_result: PhaseResult, phase_index: int) -> str`

**Stage 2 policy (binary — CONTINUE or STOP):**

| Condition | Decision |
|---|---|
| `phase_result.success == True` and `phase_result.goal_met == True` | CONTINUE |
| `phase_result.success == False` | STOP |
| `phase_result.goal_met == False` (steps completed but goal not met) | STOP |
| `phase_result.failure_class == "lane_violation"` | STOP (configuration error) |

Stage 2 does not retry phases. That is Stage 3. STOP means: record partial result, log parent policy decision, return to caller with available `loop_output`. The caller receives a `loop_output` where `errors_encountered` includes `"phase_0_goal_not_met"` or similar.

### 5.8 Mixed-Intent Patterns Supported in Stage 2

**Supported:**
- `"Find [architecture/setup/install] docs and explain [symbol/flow/component]"`
- `"Show me the architecture docs and explain how [component] works"`
- `"Locate the README and explain [concept]"`
- Any instruction where `_is_two_phase_docs_code_intent()` fires

**Not yet supported in Stage 2:**
- Three-component instructions: "find docs, explain X, and edit Y"
- Code-first mixed: "explain [symbol] and update the docs"
- Instructions with no docs token but compound code intent: "search for validate_plan and edit all callers"
- Docs + edit: "find the README and update the installation instructions"
- Instructions where the docs and code subgoals are interdependent (the code explanation requires knowing what was in the docs first — this is actually supported by context handoff, but not validated explicitly)

---

## 6. Interfaces and Schemas

### 6.1 `get_parent_plan()` Signature

```python
def get_parent_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn: Callable | None = None,
    retry_context: dict | None = None,
) -> ParentPlan:
    ...
```

**Arguments:** identical to `get_plan()`. Callers that switch from `get_plan` to `get_parent_plan` require no argument changes.

**Returns:** `ParentPlan` (not `dict`). Callers that previously consumed a `dict` must be updated to use `parent_plan.phases[0].steps` for compatibility.

**Raises:** never raises; failures in `get_plan()` are propagated (same behavior as today).

### 6.2 `run_hierarchical()` Signature

```python
def run_hierarchical(
    instruction: str,
    project_root: str,
    *,
    trace_id: str | None = None,
    similar_tasks: list[dict] | None = None,
    log_event_fn: Callable | None = None,
    retry_context: dict | None = None,
    max_runtime_seconds: int | None = None,
) -> tuple[AgentState, dict]:
    ...
```

**Arguments and return type:** identical to `run_deterministic()`. Drop-in replacement. For Stage 1, the returned `(AgentState, dict)` is exactly the same as `run_deterministic()`.

### 6.3 `PhasePlan` Schema

```python
# Minimal required fields. All must be present.
phase_plan = {
    "phase_id": "phase_a1b2c3d4",          # str: "phase_" + 8-char hex
    "phase_index": 0,                       # int: 0-based
    "subgoal": "Find architecture docs...", # str: description of this phase's goal
    "lane": "docs",                         # str: "docs" | "code"
    "steps": [                              # list: identical to existing flat plan steps
        {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", ...},
        {"id": 2, "action": "BUILD_CONTEXT", "artifact_mode": "docs", ...},
        {"id": 3, "action": "EXPLAIN", "artifact_mode": "docs", ...},
    ],
    "plan_id": "plan_3f8b8a7d",            # str: from the wrapped flat plan
    "validation": {                         # dict: PhaseValidationContract
        "require_ranked_context": True,
        "require_explain_success": True,
        "min_candidates": 1,
    },
    "retry_policy": {                       # dict: PhaseRetryPolicy (stub in Stage 1/2)
        "max_parent_retries": 0,            # 0 = no parent retries in Stage 1/2
    },
}
```

### 6.4 `ParentPlan` Schema

```python
parent_plan = {
    "parent_plan_id": "pplan_3f8b8a7d",      # str: "pplan_" + 8-char hex
    "instruction": "Find arch docs and ...", # str: original instruction, unmodified
    "decomposition_type": "compatibility",   # str: "compatibility" | "two_phase_docs_code"
    "phases": [ ... ],                        # list[PhasePlan]: ordered
    "compatibility_mode": True,              # bool
}
```

### 6.5 `PhaseResult` Schema

```python
phase_result = {
    "phase_id": "phase_a1b2c3d4",
    "phase_index": 0,
    "success": True,
    "failure_class": None,            # None on success
    "goal_met": True,
    "goal_reason": "docs_lane_explain_succeeded",
    "completed_steps": 3,
    "context_output": {
        "ranked_context": [...],
        "retrieved_symbols": [...],
        "retrieved_files": [...],
        "files_modified": [],
        "patches_applied": [],
    },
    "attempt_count": 1,
    "loop_output": { ... },           # full loop_output dict from execution_loop
}
```

### 6.6 Parent Execution Context Additions to `AgentState.context`

Added by `run_hierarchical()` to `state.context` when running in multi-phase (non-compatibility) mode:

```python
state.context["parent_plan_id"] = "pplan_3f8b8a7d"
state.context["current_phase_index"] = 1
state.context["phase_results"] = [phase_result_0]          # results of completed phases
state.context["parent_policy_history"] = [                 # decisions made so far
    {"phase_index": 0, "decision": "CONTINUE", "reason": "phase_succeeded"},
]
# Phase 1 only (context handoff from Phase 0):
state.context["prior_phase_ranked_context"] = [...]
state.context["prior_phase_retrieved_symbols"] = [...]
state.context["prior_phase_files"] = [...]
```

These fields are **not present** in compatibility mode. Downstream components that do not know about parent plans (execution_loop, dispatcher, replanner) ignore unknown context keys safely.

### 6.7 Parent Goal Aggregation

`_aggregate_parent_goal(phase_results: list[PhaseResult]) -> tuple[bool, str]`

```python
def _aggregate_parent_goal(phase_results):
    """
    True iff all phases succeeded and met their goal.
    Returns (all_succeeded, reason).
    """
    if not phase_results:
        return False, "no_phases_executed"
    for r in phase_results:
        if not r["success"] or not r["goal_met"]:
            return False, f"phase_{r['phase_index']}_failed"
    return True, "all_phases_succeeded"
```

### 6.8 Extended `GoalEvaluator.evaluate_with_reason()` Signature

```python
def evaluate_with_reason(
    self,
    instruction: str,
    state: AgentState,
    *,
    phase_subgoal: str | None = None,    # NEW: optional, backward-compatible
) -> tuple[bool, str, dict]:
    """
    When phase_subgoal is provided, use it instead of instruction for
    is_explain_like_instruction() and instruction_lower derivation.
    All other logic (lane check, patches, stall, etc.) is unchanged.
    """
    effective_instruction = phase_subgoal if phase_subgoal is not None else instruction
    instruction_lower = (effective_instruction or "").lower()
    ...  # rest is identical
```

---

## 7. Invariants and Safety Rules

These are hard rules. No engineer should violate them during Stage 1 or Stage 2 implementation.

**I1 — A phase is single-lane.**  
Every `PhasePlan` has exactly one `lane` field: `"docs"` or `"code"`. No `PhasePlan` can contain steps from both lanes. `validate_plan()` is called on each phase's step list and must pass. A `PhasePlan` with steps that fail `validate_plan()` must be rejected at construction time.

**I2 — `validate_plan()` is reused unchanged.**  
`planner/planner_utils.py::validate_plan()` must not be modified in Stage 1 or Stage 2. It is called on each phase's step list exactly as it is called today on the full plan. Any relaxation of `validate_plan()` is explicitly forbidden.

**I3 — `run_deterministic()` is not modified in Stage 1 or Stage 2.**  
It is the compatibility fallback. It must remain callable, testable, and behaviorally identical to its current implementation throughout Stage 1 and Stage 2.

**I4 — `execution_loop()` is phase-local.**  
`execution_loop()` receives a phase-scoped `AgentState` and executes within it. It does not know it is inside a phase. It does not receive any parent plan information. It does not produce `PhaseResult` — that is assembled by `run_hierarchical()` from the returned `LoopResult`.

**I5 — The parent orchestrator never allows mixed-lane inside one phase.**  
The parent orchestrator in Stage 2 produces exactly two `PhasePlan` objects, each single-lane. If `_build_two_phase_parent_plan()` produces a `PhasePlan` whose step list fails `validate_plan()`, it is a bug in plan construction — it must be caught and surfaced as an error, not silently corrected.

**I6 — `dominant_artifact_mode` is phase-local in Stage 2.**  
When `run_hierarchical()` constructs the `AgentState` for a phase, it sets `dominant_artifact_mode` from `phase_plan.lane`. This field must not "leak" across phases. Phase 1's `AgentState.context["dominant_artifact_mode"]` must be `"code"`, even if Phase 0 ran with `"docs"`.

**I7 — Phase 1 does not start if Phase 0 did not succeed.**  
This is a hard stop rule in `_apply_parent_stage2_policy()`. No partial continuation. No "try Phase 1 anyway." STOP is the only valid Stage 2 policy outcome for a failed phase.

**I8 — Clarification is not emitted in Stage 1 or Stage 2.**  
No component in Stage 1 or Stage 2 may produce a clarification response. Clarification is a Stage 3 parent policy outcome. If a mixed-intent instruction is not caught by `_is_two_phase_docs_code_intent()`, it falls through to the existing compatibility path and is handled as a single-phase code task (current behavior). That is acceptable — it is not a regression.

**I9 — The `run_hierarchical()` interface is identical to `run_deterministic()`.**  
Same argument names and types. Same return type. Any caller that currently calls `run_deterministic()` can be switched to `run_hierarchical()` without changing call sites.

**I10 — New schemas must not depend on existing execution modules.**  
`parent_plan.py` must not import from `execution_loop`, `step_dispatcher`, `replanner`, or `planner`. It may import from `plan_resolver` for helper calls like `new_plan_id()` and from `planner_utils` for `is_explicit_docs_lane_by_structure()`. It must be importable without triggering model loading or tool initialization.

---

## 8. Trace and Observability Plan

### 8.1 New Trace Events (Stage 1)

**`parent_plan_created`**  
Emitted by `get_parent_plan()` after the `ParentPlan` is constructed.  
Fields: `parent_plan_id`, `decomposition_type`, `compatibility_mode`, `phase_count`, `instruction_preview` (first 200 chars).

**`run_hierarchical_start`**  
Emitted by `run_hierarchical()` at entry.  
Fields: `parent_plan_id`, `compatibility_mode`, `phase_count`.

### 8.2 New Trace Events (Stage 2)

**`phase_started`**  
Emitted by `run_hierarchical()` before calling `execution_loop()` for a phase.  
Fields: `parent_plan_id`, `phase_id`, `phase_index`, `lane`, `step_count`, `subgoal_preview` (first 150 chars).

**`phase_completed`**  
Emitted by `run_hierarchical()` after `execution_loop()` returns for a phase, before policy decision.  
Fields: `parent_plan_id`, `phase_id`, `phase_index`, `success`, `goal_met`, `goal_reason`, `failure_class`, `completed_steps`, `attempt_count`.

**`phase_context_handoff`**  
Emitted when Phase 0 context is injected into Phase 1's state.  
Fields: `parent_plan_id`, `from_phase_index`: 0, `to_phase_index`: 1, `ranked_context_count`, `retrieved_symbols_count`, `pruned`: bool.

**`parent_policy_decision`**  
Emitted after `_apply_parent_stage2_policy()` returns.  
Fields: `parent_plan_id`, `phase_index`, `decision` ("CONTINUE" | "STOP"), `reason`.

**`parent_goal_aggregation`**  
Emitted at the end of `run_hierarchical()` in multi-phase mode.  
Fields: `parent_plan_id`, `all_phases_succeeded`, `reason`, `phase_count`, `successful_phases`.

### 8.3 Existing Trace Events That Must Remain Unchanged

The following trace events are emitted by unchanged components and must continue to be emitted with their existing field shapes in all paths (including compatibility mode):

- `planner_decision` — emitted by `run_deterministic`
- `dominant_artifact_mode` — emitted by `run_deterministic`
- `execution_limits` — emitted by `execution_loop`
- `goal_evaluation` — emitted by `execution_loop`
- `goal_completed` — emitted by `execution_loop`
- `replan_decision` — emitted by `replanner`
- `lane_violation` — emitted by `step_dispatcher`
- `stall_detected` — emitted by `execution_loop`
- `docs_intent_override` — emitted by `plan_resolver`
- `instruction_router` — emitted by `plan_resolver`

Compatibility-mode paths through `run_hierarchical` must produce all of the above events identically to `run_deterministic`. They do, because compatibility mode delegates to `run_deterministic`.

In multi-phase (Stage 2) paths, each phase produces the subset of the above events that apply to that phase's execution. The phase-scoped events are grouped by `phase_id` via the new `phase_started` event preceding them in the trace.

### 8.4 Observability Principle for Debugging

When debugging a two-phase task, the trace must allow an engineer to answer:
1. Was this task handled as two-phase or compatibility-mode? → `parent_plan_created.decomposition_type`
2. Which phase failed? → `phase_completed.success = False` for the specific `phase_id`
3. What was the parent decision? → `parent_policy_decision.decision`
4. Did Phase 0 context reach Phase 1? → `phase_context_handoff.ranked_context_count > 0`
5. What did Phase 1's goal evaluation produce? → `goal_evaluation` events scoped between `phase_started[index=1]` and `phase_completed[index=1]`

---

## 9. Test Roadmap

### 9.1 Schema / Contract Tests — `tests/test_parent_plan_schema.py` (new, Stage 1)

| Test name | What it asserts |
|---|---|
| `test_phase_plan_schema_fields` | `PhasePlan` with all required fields instantiates without error; missing required field raises |
| `test_parent_plan_schema_fields` | `ParentPlan` with all required fields instantiates without error |
| `test_phase_result_schema_fields` | `PhaseResult` with all required fields instantiates without error |
| `test_make_compatibility_parent_plan_single_phase` | One phase, `compatibility_mode=True`, `phases[0].steps` matches input flat plan steps |
| `test_make_compatibility_parent_plan_code_lane` | Code-lane flat plan → `phases[0].lane == "code"` |
| `test_make_compatibility_parent_plan_docs_lane` | Docs-lane flat plan → `phases[0].lane == "docs"` |
| `test_make_compatibility_parent_plan_preserves_plan_id` | `phases[0].plan_id == flat_plan["plan_id"]` |
| `test_validate_parent_plan_schema_valid` | `validate_parent_plan_schema` returns True for well-formed plan |
| `test_validate_parent_plan_schema_rejects_empty_phases` | Returns False when `phases` is empty |
| `test_validate_parent_plan_schema_rejects_mixed_lane_single_phase` | Raises or returns False when a `PhasePlan` step list fails `validate_plan()` |
| `test_get_parent_plan_returns_parent_plan_type` | `get_parent_plan("add type hint to get_plan")` returns a `ParentPlan` with `compatibility_mode=True` |
| `test_get_parent_plan_compatibility_mode_true` | Various single-intent instructions → always `compatibility_mode=True` in Stage 1 |

### 9.2 Compatibility Tests — `tests/test_parent_plan_schema.py` and `tests/test_run_hierarchical_compatibility.py` (new, Stage 1)

| Test name | What it asserts |
|---|---|
| `test_run_hierarchical_code_lane_output_matches_run_deterministic` | Mocked execution: `run_hierarchical` output identical to `run_deterministic` for code-lane instruction |
| `test_run_hierarchical_docs_lane_output_matches_run_deterministic` | Mocked execution: `run_hierarchical` output identical to `run_deterministic` for docs instruction |
| `test_run_hierarchical_general_output_matches_run_deterministic` | Mocked execution: output identical for GENERAL/CODE_EDIT instruction |
| `test_run_hierarchical_emits_parent_plan_created_trace` | Trace includes `parent_plan_created` event; no new events beyond that in compatibility mode |

**Existing tests that must pass without modification:**

- `tests/test_execution_loop.py` — all tests
- `tests/test_execution_loop_stall_policy.py` — all tests
- `tests/test_execution_loop_validation_contract.py` — all tests
- `tests/test_goal_evaluator*.py` — all tests
- `tests/test_replanner.py` — all tests
- `tests/test_plan_resolver_docs_intent.py` — all tests
- `tests/test_general_platform_scenarios.py` — all scenarios
- `tests/test_phase7a_harness.py` — all tests
- `tests/test_retrieval_pipeline.py` — all tests
- `tests/test_intent_understanding_matrix.py` — all tests

### 9.3 Stage 2 Mixed-Intent Integration Tests — `tests/test_two_phase_execution.py` (new, Stage 2)

| Test name | What it asserts |
|---|---|
| `test_is_two_phase_docs_code_intent_fires_on_mixed` | `_is_two_phase_docs_code_intent("Find architecture docs and explain replanner flow")` is True |
| `test_is_two_phase_docs_code_intent_does_not_fire_on_pure_docs` | Pure docs instruction → False |
| `test_is_two_phase_docs_code_intent_does_not_fire_on_pure_code` | Pure code instruction → False |
| `test_build_two_phase_parent_plan_structure` | Produces `ParentPlan` with 2 phases, `phases[0].lane == "docs"`, `phases[1].lane == "code"` |
| `test_build_two_phase_parent_plan_phase0_uses_docs_seed` | Phase 0 steps match `_docs_seed_plan(instruction)["steps"]` exactly |
| `test_build_two_phase_parent_plan_phase0_validate_plan_passes` | `validate_plan(phase0_steps_dict)` returns True |
| `test_build_two_phase_parent_plan_phase1_validate_plan_passes` | `validate_plan(phase1_steps_dict)` returns True |
| `test_get_parent_plan_mixed_intent_returns_two_phase` | Mixed instruction → `ParentPlan` with `compatibility_mode=False`, 2 phases |
| `test_run_hierarchical_two_phase_executes_both_phases` | Mocked execution: both phases execute in order; `phase_results` has 2 entries |
| `test_run_hierarchical_two_phase_stop_on_phase0_failure` | Phase 0 fails → `parent_policy_decision.decision == "STOP"` → Phase 1 not executed |
| `test_run_hierarchical_two_phase_phase1_receives_phase0_context` | Phase 1 state contains `prior_phase_ranked_context` from Phase 0 |
| `test_run_hierarchical_two_phase_goal_aggregation_all_success` | Both phases succeed → `all_phases_succeeded == True` in trace |
| `test_run_hierarchical_two_phase_goal_aggregation_phase1_fail` | Phase 1 fails → `all_phases_succeeded == False` |
| `test_goal_evaluator_phase_subgoal_parameter_backward_compat` | `evaluate_with_reason(instruction, state)` (no `phase_subgoal`) behavior unchanged |
| `test_goal_evaluator_phase_subgoal_uses_subgoal_for_explain_like` | `evaluate_with_reason("...", state, phase_subgoal="Find docs")` uses subgoal for explain_like |

### 9.4 Parent Policy Tests — For Stage 3 (future, do not implement yet)

These test stubs should be created as `xfail` in Stage 2 to document the gap:

- `test_desired_parent_retry_on_phase0_insufficient_grounding` — xfail: parent retry budget not yet implemented
- `test_desired_clarification_after_phase_retry_exhausted` — xfail: clarification outcome not yet implemented
- `test_desired_three_phase_search_edit_test` — xfail: 3-phase decomposition not yet implemented

### 9.5 Expected-Failure Tests That Should Remain xfail

From existing test suite, these remain xfail after Stage 2 (they require Stage 3 or Stage 4):

- `test_desired_validate_plan_accepts_interleaved_docs_then_code_steps` — by design; `validate_plan()` still rejects mixed-lane single plans
- Any test asserting that a code+docs edit instruction runs as two independent phases — not yet in Stage 2 scope
- Any test asserting parallel phase execution

---

## 10. Risk Register

### Risk 1 — Compatibility Drift

**Description:** After Stage 1, a change to `run_deterministic()` or `get_plan()` alters behavior, and `run_hierarchical()` compatibility mode (which delegates to `run_deterministic()`) silently picks up the change. A future engineer might not realize `run_hierarchical` ≡ `run_deterministic` in compatibility mode and add logic between them.

**Why it matters:** The compatibility guarantee is the entire foundation of Stage 1. If it drifts, every existing scenario is at risk.

**Mitigation:** The `test_run_hierarchical_compatibility_delegates_to_run_deterministic` test uses output identity assertions (see §4.4). Add a code comment in `run_hierarchical()` on the compatibility delegation block: `# Stage 1 compatibility mode: must delegate to run_deterministic verbatim; no logic between.`

**Detection in tests/traces:** Compatibility output identity tests in `test_run_hierarchical_compatibility.py`. Any output divergence surfaces immediately.

---

### Risk 2 — Phase Context Leakage

**Description:** Phase 0 context fields (e.g., `ranked_context`, `dominant_artifact_mode = "docs"`, `lane_violations`) accidentally persist into Phase 1's `AgentState`, causing Phase 1 to run as a docs-lane task or to receive corrupted context.

**Why it matters:** Phase 1 must run as code lane. If `dominant_artifact_mode = "docs"` leaks from Phase 0 into Phase 1's state, the lane enforcer in `step_dispatcher` will reject code-lane actions as lane violations.

**Mitigation:** `run_hierarchical()` constructs a fresh `AgentState` for each phase using only the explicitly whitelisted handoff fields (§5.5). Do not copy `state.context` from Phase 0 to Phase 1 wholesale. Explicitly list every field injected.

**Detection in tests/traces:** `test_run_hierarchical_two_phase_executes_both_phases` asserts Phase 1 state `dominant_artifact_mode == "code"`. `test_run_hierarchical_two_phase_phase1_receives_phase0_context` asserts only whitelisted fields are present. No unexpected `"docs"` lane in Phase 1 trace.

---

### Risk 3 — Accidental Modification of Single-Lane Semantics

**Description:** During Stage 2 implementation, an engineer modifies `validate_plan()`, `_enforce_runtime_lane_contract()`, or `is_explicit_docs_lane_by_structure()` to "make room" for two-phase plans. This is the most dangerous risk — it can silently weaken the lane contract for all existing tasks.

**Why it matters:** The single-lane contract is a correctness guarantee that prevents mixed-lane execution from producing incoherent retrieval. It must remain strict. Two-phase plans achieve multi-lane execution by running phases sequentially, not by relaxing the per-phase lane contract.

**Mitigation:** Invariant I2, I1, I5 (§7). These functions are listed as "must not change" in §3. Review gate: any PR that touches `planner_utils.py`, `step_dispatcher.py` lane enforcement, or `replanner.py` lane contract during Stage 1/2 must be rejected unless it is fixing a bug unrelated to this work.

**Detection in tests/traces:** Existing lane enforcement tests in `test_execution_loop_validation_contract.py` and `test_step_dispatcher_edit.py`. These must pass unchanged.

---

### Risk 4 — `_is_two_phase_docs_code_intent()` False Positives

**Description:** The mixed-intent detection heuristic fires on instructions that are not genuinely two-phase mixed, sending single-intent instructions into the two-phase path where Phase 0 (docs) runs unnecessarily, wastes time, and may confuse Phase 1.

**Why it matters:** A pure code instruction that accidentally contains a docs-related word ("find the implementation of docs_seed_plan") would get a spurious docs phase. This is a regression for users who write precise code instructions.

**Mitigation:** The detection heuristic (§5.1) requires all three conditions: discovery verb, docs token, AND code-intent non-docs token. This is narrower than the existing `_is_docs_artifact_intent()` heuristic. Token lists are reused from existing constants. The "not fire when `_is_docs_artifact_intent()` would return True" precondition ensures pure docs cases are not captured. Add false-positive tests (§9.3: `test_is_two_phase_docs_code_intent_does_not_fire_on_pure_code`).

**Detection in tests/traces:** The matrix of `_is_two_phase_docs_code_intent` against the existing `test_intent_understanding_matrix.py` test cases. All existing matrix passing tests must remain passing.

---

### Risk 5 — Goal Evaluator Confusion in Phase Context

**Description:** `GoalEvaluator.evaluate_with_reason()` is called with `phase_subgoal` in Stage 2 but the `state.instruction` still contains the full parent instruction. If `phase_subgoal` handling is wrong (off-by-one, None fallback triggered incorrectly), the goal evaluator produces incorrect `goal_met` results.

**Why it matters:** Incorrect phase goal evaluation → Phase 0 reports failure when it succeeded → parent policy STOP → Phase 1 never runs → the two-phase feature never works end-to-end.

**Mitigation:** The `phase_subgoal` parameter is a single-point change in `evaluate_with_reason()`: replace the first `instruction_lower` derivation with `effective_instruction`. Every other signal (lane check, stall, patches, files, EXPLAIN step) reads from `state`, not from `instruction` — so the `phase_subgoal` parameter only affects `is_explain_like_instruction()` output. Backward-compat test: call without `phase_subgoal` → identical output.

**Detection in tests/traces:** `test_goal_evaluator_phase_subgoal_parameter_backward_compat` (§9.3). The `goal_evaluation` trace event from Phase 0 must show `goal_met: True` for a docs instruction when EXPLAIN succeeded in docs lane.

---

## 11. Definition of Done by Stage

### Stage 1 — Done Criteria

**Code conditions:**
- `agent/orchestrator/parent_plan.py` exists with `PhasePlan`, `ParentPlan`, `PhaseResult`, `make_compatibility_parent_plan`, `validate_parent_plan_schema`
- `get_parent_plan()` exists in `plan_resolver.py` alongside `get_plan()` (not replacing it)
- `run_hierarchical()` exists in `deterministic_runner.py` alongside `run_deterministic()` (not replacing it)
- `run_hierarchical()` raises `NotImplementedError` for non-compatibility-mode plans
- No other file is modified

**Tests:**
- All tests in `tests/test_parent_plan_schema.py` pass (at minimum the 12 tests listed in §9.1)
- All tests in `tests/test_run_hierarchical_compatibility.py` pass (4 tests in §9.2)
- All existing tests pass without modification — zero regressions

**No-regression expectations:**
- `run_deterministic()` produces identical output to today on all existing scenario test inputs
- `run_hierarchical()` produces identical output to `run_deterministic()` for all existing scenario test inputs
- No new imports from Stage 1 modules in `execution_loop.py`, `step_dispatcher.py`, `replanner.py`, `planner.py`, `planner_utils.py`

**Scenarios that must pass:**
- All scenarios in `tests/test_general_platform_scenarios.py`
- All scenarios in `tests/test_phase7a_harness.py`
- `test_execution_loop_validation_contract.py` all cases
- `test_plan_resolver_docs_intent.py` all cases

---

### Stage 2 — Done Criteria

**Code conditions:**
- `_is_two_phase_docs_code_intent()` exists in `plan_resolver.py`
- `_build_two_phase_parent_plan()` exists in `plan_resolver.py`
- `get_parent_plan()` calls `_is_two_phase_docs_code_intent()` before compatibility fallback
- `run_hierarchical()` iterates phases in non-compatibility mode
- `_apply_parent_stage2_policy()` exists and returns "CONTINUE" or "STOP"
- `_aggregate_parent_goal()` exists
- `GoalEvaluator.evaluate_with_reason()` accepts optional `phase_subgoal` parameter
- `run_hierarchical()` no longer raises `NotImplementedError` for two-phase plans
- `run_hierarchical()` still raises `NotImplementedError` for plans with `len(phases) > 2` (not yet supported)

**Tests:**
- All tests in `tests/test_two_phase_execution.py` pass (14 tests listed in §9.3)
- Schema tests from Stage 1 still pass
- Compatibility tests from Stage 1 still pass
- All existing tests still pass — zero regressions

**No-regression expectations:**
- Single-intent instructions continue to route through compatibility mode
- `run_deterministic()` behavior unchanged for all existing callers
- `validate_plan()` unchanged
- `_enforce_runtime_lane_contract()` unchanged
- `execution_loop()` unchanged

**Scenarios that must pass:**
- All scenarios that passed at Stage 1 gate (no regressions)
- **New:** `"Find architecture docs and explain replanner flow"` → two-phase execution → Phase 0 docs succeeds → Phase 1 code explain succeeds → `all_phases_succeeded == True`
- **New:** Phase 0 forced to fail → parent STOP → Phase 1 not executed → `phase_results` has one entry

---

## 12. Explicit Non-Goals for Stages 1 and 2

The following are explicitly out of scope. Any engineer proposing to include these in Stage 1 or Stage 2 is out of scope for this roadmap. Each requires a separate decision.

| Non-goal | Why it's deferred |
|---|---|
| Relaxing `validate_plan()` to accept mixed-lane single plans | Architectural safety boundary; stage 3+ concern |
| Modifying `planner.py` to emit phase-aware step structures | Planner runs per-phase on subgoal; no schema changes needed |
| Modifying `replanner.py` to understand phases | Replanner is phase-local; it does not need to know about parent plans |
| Parallel phase execution | Phases are always sequential; parallel adds concurrency complexity without Stage 2 evidence of need |
| Three-or-more-phase decomposition | Stage 4, gated on Stage 3 stability |
| Docs + edit mixed tasks ("find README and update installation") | Requires a third docs-edit phase pattern; out of Stage 2 scope |
| Code-first mixed tasks ("explain X and update the docs") | Reverse phase order; not captured by current two-phase heuristic |
| Broad prompt retuning of planner or replanner | Not required; planner runs per-phase with full instruction in Stage 2 |
| Retrieval redesign framed as phase orchestration | Retrieval is unchanged; phase context handoff is the only inter-phase retrieval mechanism |
| Clarification response path | Stage 3 parent policy concern only |
| Parent retry budget per phase | Stage 3 only; Stage 2 uses single execution_loop attempt per phase |
| Autonomous/LLM-driven phase selection | Forbidden by architecture rules; parent orchestrator is always deterministic |
| Changes to `AgentState` dataclass structure | Context additions via dict keys only; no dataclass field changes |

---

## 13. Final Recommendation

### Recommended Implementation Order

**Week 1: Stage 1 — Schemas and Compatibility Wrapper**

1. Create `agent/orchestrator/parent_plan.py` with all schema definitions and helpers. Write `tests/test_parent_plan_schema.py` in parallel. Do not commit schema code until tests pass.
2. Add `get_parent_plan()` to `plan_resolver.py`. This function is ~20 lines. The test `test_get_parent_plan_compatibility_mode_true` must pass before moving on.
3. Add `run_hierarchical()` to `deterministic_runner.py`. This function is ~30 lines in Stage 1 (all compatibility delegation). Write `tests/test_run_hierarchical_compatibility.py`. Run full existing test suite.
4. Gate: all new tests pass; zero regressions on existing tests.

**Week 2: Stage 2 — Two-Phase Execution**

1. Add `_is_two_phase_docs_code_intent()` to `plan_resolver.py`. Write its unit tests first (detection matrix — 5–10 test cases). Do not add to `get_parent_plan()` until unit tests pass.
2. Add `_build_two_phase_parent_plan()` to `plan_resolver.py`. Verify `validate_plan()` passes on both phase step lists.
3. Update `get_parent_plan()` to call the detection heuristic.
4. Add the optional `phase_subgoal` parameter to `GoalEvaluator.evaluate_with_reason()`. Write backward-compat test first.
5. Extend `run_hierarchical()` with phase iteration loop. Write `tests/test_two_phase_execution.py`. Start with structure tests, then mocked-execution tests, then integration tests.
6. Gate: all Stage 2 tests pass; all Stage 1 tests still pass; all existing tests still pass.

### Why This Order

**Schemas first, execution second.** The schemas define the contract everything else depends on. Writing tests for schemas before writing execution code prevents interface churn — you discover field naming and typing issues early, when fixing them is cheap.

**Compatibility wrapper before two-phase.** Stage 1 creates the `run_hierarchical` entry point that all future code will use, and proves it is a safe drop-in for `run_deterministic`. Without Stage 1, you cannot safely switch callers to `run_hierarchical` in Stage 2.

**Detection heuristic before plan construction.** The detection heuristic is the narrowest change and the one most likely to need iteration (false positive tuning). Isolating it with its own unit tests before wiring it into `get_parent_plan` means heuristic tuning doesn't disrupt plan construction work.

**Goal evaluator extension before phase iteration.** Phase iteration needs correct goal evaluation per phase. The `phase_subgoal` parameter is backward-compatible and low-risk — add it before writing the phase loop so the loop is correct from the start.

This order minimizes blast radius at every step: each increment is independently testable and the existing system remains fully functional throughout.

---

*End of execution roadmap.*
