# Hierarchical Phased Orchestration — Implementation Task Breakdown

**Type:** Implementation-ready work plan  
**Status:** Actionable for Stage 1 and Stage 2  
**Source:** `Docs/HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md`  
**Refinement applied:** Phase 1 must receive a phase-scoped subgoal derived from the parent instruction — not the raw parent instruction verbatim. Each `PhasePlan` owns its subgoal text. Prior phase outputs travel via structured context handoff.  
**Date:** 2026-03-20

---

## 0. Pre-Flight Checklist

Before any coding starts, every engineer on this work must:

1. Read `Docs/HIERARCHICAL_PHASED_ORCHESTRATION_EXECUTION_ROADMAP.md` in full.
2. Run the full test suite and confirm it is green. Record the baseline pass count. Any pre-existing failure must be logged before starting — it cannot be attributed to this work.
3. Confirm which decisions in §4 (Interface Decisions) are locked. Do not start Stage 2 coding until all five decisions in §4 are answered.
4. Confirm `git status` is clean and there is a rollback tag before each task block.

---

## 1. Stage 1 Task List

### Task ordering and dependency graph

```
S1-A (new module: parent_plan.py)
  └── S1-B (schema unit tests)
        └── S1-C (get_parent_plan in plan_resolver.py)
              └── S1-D (run_hierarchical Stage 1 in deterministic_runner.py)
                    └── S1-E (compatibility integration tests)
                          └── S1-GATE (full regression run)
```

No task in Stage 1 may be merged until all preceding tasks in its dependency chain pass their tests.

---

### S1-A — Create `agent/orchestrator/parent_plan.py`

**What:** New module. Pure data definitions. Zero execution logic. Zero imports from execution_loop, step_dispatcher, replanner, or planner.

**Add:**
- `PhasePlan` typed dict (or frozen dataclass): fields `phase_id`, `phase_index`, `subgoal`, `lane`, `steps`, `plan_id`, `validation`, `retry_policy`
- `ParentPlan` typed dict: fields `parent_plan_id`, `instruction`, `decomposition_type`, `phases`, `compatibility_mode`
- `PhaseResult` typed dict: fields `phase_id`, `phase_index`, `success`, `failure_class`, `goal_met`, `goal_reason`, `completed_steps`, `context_output`, `attempt_count`, `loop_output`
- `PhaseValidationContract` typed dict: fields `require_ranked_context`, `require_explain_success`, `min_candidates`
- `PhaseRetryPolicy` typed dict: fields `max_parent_retries` (always 0 in Stage 1/2)
- `new_phase_id() -> str` — returns `"phase_" + uuid4().hex[:8]`
- `new_parent_plan_id() -> str` — returns `"pplan_" + uuid4().hex[:8]`
- `make_compatibility_parent_plan(flat_plan: dict, instruction: str) -> ParentPlan`
- `validate_parent_plan_schema(parent_plan: ParentPlan) -> bool`

**Permitted imports in this module:** `uuid`, `planner.planner_utils.is_explicit_docs_lane_by_structure` (for lane derivation in `make_compatibility_parent_plan`), `agent.orchestrator.plan_resolver.new_plan_id` (for plan_id extraction).

**Must NOT import:** `execution_loop`, `replanner`, `step_dispatcher`, `planner.planner`, `agent.models.*`. This module must be importable in a unit test environment with no LLM or tool infrastructure loaded.

**`make_compatibility_parent_plan` logic:**
```
lane = "docs" if is_explicit_docs_lane_by_structure(flat_plan) else "code"
phase = PhasePlan(
    phase_id = new_phase_id(),
    phase_index = 0,
    subgoal = instruction[:200],
    lane = lane,
    steps = flat_plan.get("steps", []),
    plan_id = flat_plan.get("plan_id", ""),
    validation = PhaseValidationContract(
        require_ranked_context=False,  # Stage 1: not enforced
        require_explain_success=False,
        min_candidates=0,
    ),
    retry_policy = PhaseRetryPolicy(max_parent_retries=0),
)
return ParentPlan(
    parent_plan_id = new_parent_plan_id(),
    instruction = instruction,
    decomposition_type = "compatibility",
    phases = [phase],
    compatibility_mode = True,
)
```

**`validate_parent_plan_schema` logic:**
- `phases` must be a non-empty list
- Each `PhasePlan` must have all required fields
- Each `PhasePlan.lane` must be `"docs"` or `"code"`
- Each `PhasePlan.steps` must be a list (may be empty for stub validation)
- Returns `False` for any violation; does NOT call `validate_plan()` on steps here (that is a construction-time check in Stage 2)

**Blast radius:** Zero. New file only. No existing file imported from it yet.

**Rollback point:** Delete `agent/orchestrator/parent_plan.py` — zero effect on system.

---

### S1-B — Schema unit tests: `tests/test_parent_plan_schema.py`

**What:** New test file. Tests S1-A only. No mocking of LLMs or tools needed.

**Tests to write:**

| Test | Asserts |
|---|---|
| `test_phase_plan_all_required_fields_present` | Instantiate `PhasePlan` with all required fields; no exception |
| `test_phase_plan_missing_lane_raises` | Missing `lane` field → TypeError or validation error |
| `test_parent_plan_all_required_fields_present` | Instantiate `ParentPlan`; no exception |
| `test_phase_result_all_required_fields_present` | Instantiate `PhaseResult`; no exception |
| `test_new_phase_id_format` | `new_phase_id()` returns string starting with `"phase_"`, length 13 |
| `test_new_parent_plan_id_format` | `new_parent_plan_id()` starts `"pplan_"`, length 14 |
| `test_make_compatibility_parent_plan_code_lane` | Code flat plan → `phases[0].lane == "code"`, `compatibility_mode == True` |
| `test_make_compatibility_parent_plan_docs_lane` | Docs flat plan (has SEARCH_CANDIDATES + artifact_mode=docs) → `phases[0].lane == "docs"` |
| `test_make_compatibility_parent_plan_single_phase` | Result has exactly one phase |
| `test_make_compatibility_parent_plan_preserves_steps` | `phases[0].steps == flat_plan["steps"]` |
| `test_make_compatibility_parent_plan_preserves_plan_id` | `phases[0].plan_id == flat_plan["plan_id"]` |
| `test_make_compatibility_parent_plan_instruction_stored` | `parent_plan.instruction == instruction` (unmodified) |
| `test_validate_parent_plan_schema_valid` | Well-formed plan → True |
| `test_validate_parent_plan_schema_rejects_empty_phases` | `phases=[]` → False |
| `test_validate_parent_plan_schema_rejects_invalid_lane` | `lane="mixed"` → False |

**Dependency:** S1-A must be complete.

**Blast radius:** Zero. New test file only.

---

### S1-C — Add `get_parent_plan()` to `agent/orchestrator/plan_resolver.py`

**What:** Add one new function. All existing functions untouched.

**Exact change:** Append at the bottom of `plan_resolver.py` (after `get_plan`):

```python
from agent.orchestrator.parent_plan import make_compatibility_parent_plan, ParentPlan

def get_parent_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
) -> "ParentPlan":
    """
    Stage 1: wraps get_plan() in a single-phase compatibility ParentPlan.
    Stage 2: adds mixed-intent detection before the compatibility fallback.
    Never raises; propagates get_plan() behavior on failure.
    """
    flat_plan = get_plan(
        instruction,
        trace_id=trace_id,
        log_event_fn=log_event_fn,
        retry_context=retry_context,
    )
    parent_plan = make_compatibility_parent_plan(flat_plan, instruction)
    if log_event_fn and trace_id:
        try:
            log_event_fn(trace_id, "parent_plan_created", {
                "parent_plan_id": parent_plan["parent_plan_id"],
                "decomposition_type": parent_plan["decomposition_type"],
                "compatibility_mode": parent_plan["compatibility_mode"],
                "phase_count": len(parent_plan["phases"]),
                "instruction_preview": (instruction or "")[:200],
            })
        except Exception:
            pass
    return parent_plan
```

**Must NOT change:**
- `get_plan()` — not touched, not renamed
- `_is_docs_artifact_intent()`, `_docs_seed_plan()`, all token lists, all router logic
- Function order of existing functions

**Blast radius:** One new function appended to plan_resolver.py. No existing call sites changed.

**Rollback:** Remove the function and import. No other change.

---

### S1-D — Add `run_hierarchical()` to `agent/orchestrator/deterministic_runner.py`

**What:** Add one new function. Existing `run_deterministic()` untouched.

**Exact change:** Append at the bottom of `deterministic_runner.py`:

```python
from agent.orchestrator.plan_resolver import get_parent_plan

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
    Hierarchical orchestrator. Stage 1: delegates to run_deterministic() for all
    compatibility-mode plans. Stage 2+: iterates phases for non-compatibility plans.

    Interface is identical to run_deterministic(). Drop-in replacement.
    """
    log_fn = log_event_fn or log_event
    parent_plan = get_parent_plan(
        instruction,
        trace_id=trace_id,
        log_event_fn=log_fn,
        retry_context=retry_context,
    )
    if trace_id:
        log_fn(trace_id, "run_hierarchical_start", {
            "parent_plan_id": parent_plan["parent_plan_id"],
            "compatibility_mode": parent_plan["compatibility_mode"],
            "phase_count": len(parent_plan["phases"]),
        })

    if parent_plan["compatibility_mode"]:
        # Stage 1: pure delegation. Must not add any logic here.
        # Invariant: output must be identical to run_deterministic() for same inputs.
        return run_deterministic(
            instruction,
            project_root,
            trace_id=trace_id,
            similar_tasks=similar_tasks,
            log_event_fn=log_fn,
            retry_context=retry_context,
            max_runtime_seconds=max_runtime_seconds,
        )

    # Stage 2+ multi-phase path.
    raise NotImplementedError(
        "Multi-phase execution not yet implemented. "
        "Non-compatibility ParentPlan requires Stage 2."
    )
```

**Must NOT change:**
- `run_deterministic()` — not touched, not renamed
- Existing imports
- The `AgentState` import already present

**Blast radius:** One new function plus one new import appended. No call site changes. `NotImplementedError` on non-compatibility plans is the safety guard — prevents accidental use of non-existent multi-phase logic.

**Rollback:** Remove the function and the new import. No other change.

---

### S1-E — Compatibility integration tests: `tests/test_run_hierarchical_compatibility.py`

**What:** New test file proving output identity between `run_hierarchical` and `run_deterministic` in compatibility mode. Uses mocked execution (mock `execution_loop`, mock `plan`) — no real LLM calls.

**Tests to write:**

| Test | Asserts |
|---|---|
| `test_run_hierarchical_emits_parent_plan_created_event` | Trace contains `"parent_plan_created"` event with `compatibility_mode=True` |
| `test_run_hierarchical_emits_run_hierarchical_start_event` | Trace contains `"run_hierarchical_start"` event |
| `test_run_hierarchical_compatibility_returns_same_type` | Returns `(AgentState, dict)` |
| `test_run_hierarchical_compatibility_completed_steps_matches` | `output_a["completed_steps"] == output_b["completed_steps"]` (mocked) |
| `test_run_hierarchical_compatibility_errors_encountered_matches` | `errors_encountered` identical |
| `test_run_hierarchical_compatibility_code_lane_instruction` | Code instruction → `compatibility_mode=True` in parent_plan_created event |
| `test_run_hierarchical_compatibility_docs_lane_instruction` | Docs instruction → `compatibility_mode=True`, `phase_count=1` |
| `test_run_hierarchical_notimplemented_on_noncompat` | If `make_compatibility_parent_plan` is patched to return `compatibility_mode=False` → raises `NotImplementedError` |

**Dependency:** S1-A, S1-B, S1-C, S1-D must be complete.

**Blast radius:** New test file only.

---

### S1-GATE — Full regression run

**What:** Run the complete existing test suite (`pytest tests/`). Zero regressions permitted.

**Required to pass:**
- `tests/test_execution_loop.py`
- `tests/test_execution_loop_stall_policy.py`
- `tests/test_execution_loop_validation_contract.py`
- `tests/test_goal_evaluator*.py` (all matching files)
- `tests/test_replanner.py`
- `tests/test_plan_resolver_docs_intent.py`
- `tests/test_general_platform_scenarios.py`
- `tests/test_phase7a_harness.py`
- `tests/test_intent_understanding_matrix.py`
- `tests/test_intent_understanding_expected_failures.py`
- `tests/test_parent_plan_schema.py` (new)
- `tests/test_run_hierarchical_compatibility.py` (new)

**Stage 1 is complete when this gate passes with zero failures on existing tests and all new tests passing.**

---

## 2. Stage 2 Task List

### Prerequisite: all §4 Interface Decisions must be answered before Stage 2 coding begins.

See §4. Every open question there must have an explicit written answer. Do not start S2-A until §4.5 (phase subgoal derivation decision) is resolved.

### Task ordering and dependency graph

```
S2-A (detection heuristic + tests)
  └── S2-B (phase subgoal derivation + tests)
        └── S2-C (build_two_phase_parent_plan + plan_resolver update + tests)
              └── S2-D (goal_evaluator extension + tests)
                    └── S2-E (run_hierarchical Stage 2 phase loop + handoff)
                          └── S2-F (two-phase integration tests)
                                └── S2-GATE (full regression run)
```

---

### S2-A — Detection heuristic: `_is_two_phase_docs_code_intent()`

**File:** `agent/orchestrator/plan_resolver.py`

**What:** Add one new private function. Nothing else changes.

**Firing logic (all three conditions must hold):**
1. `_is_docs_artifact_intent(instruction)` returns `False` — the instruction was NOT already handled as pure docs. This precondition prevents double-capture.
2. `instruction` (lowercased) contains at least one token from `_DOCS_DISCOVERY_VERBS` (already defined in file: `"where"`, `"locate"`, `"find"`, `"list"`, `"show"`)
3. `instruction` (lowercased) contains at least one token from `_DOCS_INTENT_TOKENS` (already defined: `"readme"`, `"docs"`, `"documentation"`, `"architecture docs"`, etc.)
4. `instruction` (lowercased) contains at least one of these specific code-intent markers from `_NON_DOCS_TOKENS`: `"explain"`, `"flow"`, `"implemented"`, `"implementation"`, `"function "`, `"method "`, `"class "`

**Do NOT introduce new token lists.** Reuse the three constants already in `plan_resolver.py`.

**Function position:** Add immediately above `get_parent_plan()` in the file.

**Test-first rule:** Write `tests/test_two_phase_execution.py::TestDetectionHeuristic` before writing the implementation. The heuristic logic must not be written until the test cases are defined and failing (TDD for this specific function).

**Tests for S2-A** (add to `tests/test_two_phase_execution.py`):

| Test | Input | Expected |
|---|---|---|
| `test_fires_on_find_docs_and_explain` | `"Find architecture docs and explain replanner flow"` | True |
| `test_fires_on_show_readme_and_explain` | `"Show me the README and explain how the planner works"` | True |
| `test_fires_on_locate_docs_and_flow` | `"Locate the architecture docs and describe the flow"` | True |
| `test_does_not_fire_on_pure_docs` | `"Find the architecture docs"` | False (docs override would handle) |
| `test_does_not_fire_on_pure_code` | `"Explain the replanner flow"` | False (no docs token) |
| `test_does_not_fire_on_edit` | `"Edit validate_plan to add a type hint"` | False |
| `test_does_not_fire_on_symbol_only` | `"validate_plan"` | False |
| `test_does_not_fire_on_docs_implemented` | `"find docs for the implemented approach"` | False — `"implemented"` is in `_NON_DOCS_TOKENS` AND `_is_docs_artifact_intent` would have returned False, but there is no discovery verb that makes docs intent TRUE; verify logic carefully |
| `test_does_not_fire_on_no_discovery_verb` | `"The docs explain the replanner flow"` | False — no discovery verb |

**Blast radius:** One private function added to `plan_resolver.py`. Not yet called by anything.

**Rollback:** Remove the function. No effect.

---

### S2-B — Phase subgoal derivation

**File:** `agent/orchestrator/plan_resolver.py`

**What:** Add `_derive_phase_subgoals(instruction: str) -> tuple[str, str]` returning `(phase_0_subgoal, phase_1_subgoal)`.

**Decision required first:** See §4.5. The derivation strategy must be locked before implementing this function. The decision affects what Phase 1 planner receives.

**Approved Stage 2 minimal approach (deterministic, no LLM):**

Phase 0 subgoal — docs phase:
- Pattern: `"Find documentation artifacts relevant to: " + instruction[:150]`
- This is always deterministic. It describes what Phase 0 is doing in plain English.

Phase 1 subgoal — code phase:
- Strategy: attempt to split the instruction on the first code-intent connector. Look for `" and explain "`, `" and describe "`, `" and show how "`, `" and summarize "` in the lowercased instruction. If found, take everything from after the connector to end of string and titlecase it as the Phase 1 subgoal.
- Fallback: if no connector is found, use the full instruction as Phase 1 subgoal. This preserves current behavior when splitting fails.
- Examples:
  - `"Find architecture docs and explain replanner flow"` → Phase 1 subgoal = `"Explain replanner flow"` (from `" and explain "` split)
  - `"Show me the README and explain how validate_plan works"` → Phase 1 subgoal = `"Explain how validate_plan works"`
  - `"find documentation and describe the planner"` → Phase 1 subgoal = `"Describe the planner"`

**Phase 1 planner input is the Phase 1 subgoal, not the parent instruction.** This is the refinement from the current roadmap §5.2. The planner receives `phase_1_subgoal` so it does not try to plan a docs-discovery phase on top of the code explanation phase.

**This changes the roadmap's §5.2 statement that "Phase 1 steps = `plan(instruction)`."** The corrected contract is: `Phase 1 steps = plan(phase_1_subgoal)`.

**Test for S2-B** (add to `tests/test_two_phase_execution.py`):

| Test | Input | Expected Phase 0 subgoal | Expected Phase 1 subgoal |
|---|---|---|---|
| `test_derive_subgoals_standard_pattern` | `"Find architecture docs and explain replanner flow"` | `"Find documentation artifacts relevant to: Find architecture docs and explain replanner flow"` (first 150 chars) | `"Explain replanner flow"` |
| `test_derive_subgoals_no_connector_fallback` | `"Find docs flow explain"` (no recognized connector) | standard pattern | full instruction |
| `test_derive_subgoals_describe_variant` | `"find docs and describe the planner"` | standard pattern | `"Describe the planner"` |
| `test_derive_subgoals_phase1_subgoal_not_empty` | any input | — | non-empty string |
| `test_derive_subgoals_phase0_subgoal_starts_with_find` | any input | starts with `"Find documentation"` | — |

**Blast radius:** One private function added. Not yet called by anything.

---

### S2-C — Build two-phase parent plan and wire into `get_parent_plan()`

**File:** `agent/orchestrator/plan_resolver.py`

**What:** Add `_build_two_phase_parent_plan()` and update `get_parent_plan()` to call the detection heuristic.

**`_build_two_phase_parent_plan(instruction, trace_id, log_event_fn)` logic:**

```
phase_0_subgoal, phase_1_subgoal = _derive_phase_subgoals(instruction)

# Phase 0 — docs lane
docs_flat = _docs_seed_plan(instruction)   # unchanged, existing function
validate docs_flat["steps"] against validate_plan({"steps": docs_flat["steps"]})
→ must return True; if False, raise ValueError("Phase 0 steps failed validate_plan")

phase_0 = PhasePlan(
    phase_id = new_phase_id(),
    phase_index = 0,
    subgoal = phase_0_subgoal,
    lane = "docs",
    steps = docs_flat["steps"],
    plan_id = docs_flat["plan_id"],
    validation = PhaseValidationContract(
        require_ranked_context=True,
        require_explain_success=True,
        min_candidates=1,
    ),
    retry_policy = PhaseRetryPolicy(max_parent_retries=0),
)

# Phase 1 — code lane
code_flat = plan(phase_1_subgoal)  # planner receives phase-scoped subgoal
validate code_flat["steps"] against validate_plan({"steps": code_flat["steps"]})
→ must return True; if False, fall back to make_compatibility_parent_plan(get_plan(instruction), instruction)
  and log a "two_phase_fallback" trace event

phase_1 = PhasePlan(
    phase_id = new_phase_id(),
    phase_index = 1,
    subgoal = phase_1_subgoal,
    lane = "code",
    steps = code_flat["steps"],
    plan_id = code_flat["plan_id"],
    validation = PhaseValidationContract(
        require_ranked_context=True,
        require_explain_success=is_explain_like_instruction(phase_1_subgoal),
        min_candidates=1,
    ),
    retry_policy = PhaseRetryPolicy(max_parent_retries=0),
)

return ParentPlan(
    parent_plan_id = new_parent_plan_id(),
    instruction = instruction,    # ORIGINAL instruction, not subgoal
    decomposition_type = "two_phase_docs_code",
    phases = [phase_0, phase_1],
    compatibility_mode = False,
)
```

**Important:** The `validate_plan()` call on Phase 1 steps is a safety check. If the planner emits steps that fail `validate_plan()` (e.g., a mixed-lane plan from a bad planner output), the two-phase path falls back to compatibility mode rather than crashing. Log the fallback. This prevents a bad planner response from breaking the two-phase path entirely.

**Update `get_parent_plan()`:**

Insert **before** the `flat_plan = get_plan(...)` call in Stage 1's `get_parent_plan`:

```
# Stage 2: mixed-intent detection
if _is_two_phase_docs_code_intent(instruction):
    try:
        parent_plan = _build_two_phase_parent_plan(instruction, trace_id, log_event_fn)
        # log parent_plan_created event
        return parent_plan
    except Exception as e:
        logger.warning("[get_parent_plan] two-phase build failed, falling back: %s", e)
        # fall through to compatibility path below

# compatibility fallback (existing Stage 1 logic)
flat_plan = get_plan(...)
...
```

**Tests for S2-C** (add to `tests/test_two_phase_execution.py`):

| Test | Asserts |
|---|---|
| `test_build_two_phase_structure` | Returns `ParentPlan` with 2 phases, `compatibility_mode=False`, `decomposition_type="two_phase_docs_code"` |
| `test_build_two_phase_phase0_lane_docs` | `phases[0].lane == "docs"` |
| `test_build_two_phase_phase1_lane_code` | `phases[1].lane == "code"` |
| `test_build_two_phase_phase0_validate_plan_passes` | `validate_plan({"steps": phases[0].steps})` returns True |
| `test_build_two_phase_phase1_validate_plan_passes` | `validate_plan({"steps": phases[1].steps})` returns True |
| `test_build_two_phase_phase0_subgoal_is_phase_scoped` | `phases[0].subgoal` starts with `"Find documentation"` |
| `test_build_two_phase_phase1_subgoal_is_phase_scoped` | `phases[1].subgoal` is NOT the raw parent instruction verbatim (contains extracted code part) |
| `test_build_two_phase_parent_instruction_preserved` | `parent_plan.instruction == original_instruction` (unmodified) |
| `test_get_parent_plan_mixed_fires_two_phase` | Mixed instruction → `compatibility_mode=False`, `phase_count=2` |
| `test_get_parent_plan_pure_code_stays_compat` | Pure code instruction → `compatibility_mode=True` |
| `test_get_parent_plan_pure_docs_stays_compat` | Pure docs instruction → `compatibility_mode=True` |
| `test_build_two_phase_fallback_on_bad_planner_output` | Patched `plan()` returns steps that fail `validate_plan()` → fallback to compat mode; no exception |

**Blast radius:** Two new private functions + 6-line conditional branch in `get_parent_plan()`. All single-intent instructions unchanged (heuristic does not fire).

**Rollback:** Remove the two new functions and remove the 6-line block from `get_parent_plan()`.

---

### S2-D — Extend `GoalEvaluator.evaluate_with_reason()`

**File:** `agent/orchestrator/goal_evaluator.py`

**What:** Add optional keyword parameter `phase_subgoal` to `evaluate_with_reason`. Backward-compatible.

**Exact change:**

```python
def evaluate_with_reason(
    self,
    instruction: str,
    state: AgentState,
    *,
    phase_subgoal: str | None = None,   # ADD THIS PARAMETER ONLY
) -> tuple[bool, str, dict]:
    # Change these two lines only:
    effective_instruction = phase_subgoal if phase_subgoal is not None else instruction
    instruction_lower = (effective_instruction or "").lower()
    # ... rest of function body is UNCHANGED ...
```

**Must NOT change:**
- `evaluate()` method — it calls `evaluate_with_reason` without `phase_subgoal`; remains unchanged
- `is_explain_like_instruction()` — not touched
- All existing evaluation logic below `instruction_lower` derivation
- Function signatures of any other method

**Tests for S2-D** (add to `tests/test_two_phase_execution.py`):

| Test | Asserts |
|---|---|
| `test_evaluate_with_reason_no_phase_subgoal_backward_compat` | Called without `phase_subgoal` → behavior identical to current; produces same output as before change |
| `test_evaluate_with_reason_phase_subgoal_used_for_explain_like` | `phase_subgoal="Explain replanner flow"` → `explain_like=True` even if `instruction` is non-explain-like |
| `test_evaluate_with_reason_phase_subgoal_none_uses_instruction` | `phase_subgoal=None` → uses `instruction` as before |
| `test_evaluate_phase_0_docs_lane_success` | Phase 0 state (docs lane, EXPLAIN succeeded) + `phase_subgoal="Find docs..."` → `goal_met=True`, `goal_reason="docs_lane_explain_succeeded"` |

**Blast radius:** Two-line change to one method. Backward-compatible by keyword-only default `None`.

**Rollback:** Remove the `phase_subgoal` parameter and revert those two lines.

---

### S2-E — Implement two-phase loop in `run_hierarchical()`

**File:** `agent/orchestrator/deterministic_runner.py`

**What:** Replace the `raise NotImplementedError(...)` block with a real phase iteration loop.

**Add these private helpers in `deterministic_runner.py`:**

1. `_build_phase_agent_state(phase_plan, project_root, instruction, trace_id, similar_tasks, context_handoff) -> AgentState`
   - Creates a fresh `AgentState` for the phase
   - Sets `state.instruction = phase_plan.subgoal` (NOT the parent instruction)
   - Sets `state.context["instruction"] = phase_plan.subgoal`
   - Sets `state.context["parent_instruction"] = instruction` (original, for reference)
   - Sets `state.context["dominant_artifact_mode"] = phase_plan.lane`
   - Injects `context_handoff` fields (see handoff rules below)
   - Sets all standard context fields (project_root, trace_id, etc.)
   - Sets `state.context["parent_plan_id"]`, `state.context["current_phase_index"]`

2. `_extract_phase_context_output(phase_state: AgentState) -> dict`
   - Reads from `phase_state.context`: `ranked_context`, `retrieved_symbols`, `retrieved_files`
   - Reads from `phase_state.step_results`: `files_modified` (aggregated), `patches_applied` (aggregated)
   - Returns `context_output` dict matching `PhaseResult.context_output` schema

3. `_build_phase_context_handoff(phase_result, phase_plan) -> dict`
   - Called after Phase 0 succeeds, before Phase 1 `AgentState` construction
   - Extracts from `phase_result.context_output`:
     - `prior_phase_ranked_context` = `phase_result.context_output["ranked_context"]`
     - `prior_phase_retrieved_symbols` = `phase_result.context_output["retrieved_symbols"]`
     - `prior_phase_files` = `phase_result.context_output["retrieved_files"]`
   - Applies pruning: if total estimated char count of `prior_phase_ranked_context` > `MAX_CONTEXT_CHARS // 2`, keep top-N items (simple list slice, not re-ranking)
   - Returns dict of handoff fields (keys as above)

4. `_apply_parent_stage2_policy(phase_result: PhaseResult, phase_index: int) -> str`
   - Returns `"CONTINUE"` only if `phase_result["success"] == True and phase_result["goal_met"] == True`
   - Returns `"STOP"` in all other cases
   - No exceptions. Defensively handles None or malformed phase_result by returning `"STOP"`

5. `_aggregate_parent_goal(phase_results: list) -> tuple[bool, str]`
   - `True, "all_phases_succeeded"` iff all results have `success=True` and `goal_met=True`
   - `False, "phase_{N}_failed"` if any result fails

6. `_build_hierarchical_loop_output(phase_results, start_time, parent_plan_id) -> dict`
   - Returns a `dict` matching the shape of `run_deterministic`'s `loop_output`:
     - `completed_steps` = sum of `r["completed_steps"]` across phases
     - `files_modified` = union of `r["context_output"]["files_modified"]` across phases
     - `patches_applied` = union of `r["context_output"]["patches_applied"]` across phases
     - `errors_encountered` = list of errors from each phase's `loop_output.errors_encountered`
     - `tool_calls` = sum of tool calls across phases
     - `plan_result` = parent plan id (not a flat plan)
     - `start_time` = start_time
     - `phase_results` = phase_results list (extra field; callers that only read standard fields are unaffected)
     - If any phase stopped early: add `"phase_{N}_goal_not_met"` to `errors_encountered`

**Phase loop structure in `run_hierarchical()` for non-compatibility mode:**

```
start_time = current time
phase_results = []
last_phase_state = None
context_handoff = {}

for phase_plan in parent_plan["phases"]:
    phase_state = _build_phase_agent_state(
        phase_plan, project_root, instruction, trace_id,
        similar_tasks, context_handoff
    )
    log "phase_started" event

    loop_result = execution_loop(
        phase_state,
        phase_plan["subgoal"],      # ← phase-scoped subgoal as the instruction arg
        trace_id=trace_id,
        log_event_fn=log_fn,
        mode=ExecutionLoopMode.DETERMINISTIC,
        max_runtime_seconds=max_runtime_seconds,
    )

    goal_evaluator = GoalEvaluator()
    goal_met, goal_reason, goal_signals = goal_evaluator.evaluate_with_reason(
        phase_plan["subgoal"],
        loop_result.state,
        phase_subgoal=phase_plan["subgoal"],
    )

    context_output = _extract_phase_context_output(loop_result.state)
    phase_success = goal_met and (loop_result.loop_output or {}).get("errors_encountered") is not None
    # More precisely: phase succeeded if goal_met=True and no FATAL errors
    phase_success = goal_met

    failure_class = None
    if not phase_success:
        # derive failure_class from loop_result.loop_output["errors_encountered"] or
        # loop_result.state.context for lane violations
        failure_class = _derive_phase_failure_class(loop_result)

    phase_result = PhaseResult(
        phase_id = phase_plan["phase_id"],
        phase_index = phase_plan["phase_index"],
        success = phase_success,
        failure_class = failure_class,
        goal_met = goal_met,
        goal_reason = goal_reason,
        completed_steps = len(loop_result.state.completed_steps),
        context_output = context_output,
        attempt_count = 1,
        loop_output = loop_result.loop_output or {},
    )
    phase_results.append(phase_result)
    last_phase_state = loop_result.state

    log "phase_completed" event

    decision = _apply_parent_stage2_policy(phase_result, phase_plan["phase_index"])
    log "parent_policy_decision" event with decision

    if decision == "STOP":
        break

    # Prepare handoff for next phase
    context_handoff = _build_phase_context_handoff(phase_result, phase_plan)
    log "phase_context_handoff" event

all_succeeded, agg_reason = _aggregate_parent_goal(phase_results)
log "parent_goal_aggregation" event

loop_output = _build_hierarchical_loop_output(phase_results, start_time, parent_plan["parent_plan_id"])

# Return: last executed phase's state + aggregated loop_output
assert last_phase_state is not None
return last_phase_state, loop_output
```

**Add `_derive_phase_failure_class(loop_result) -> str | None`:**
- Returns `None` if `goal_met=True`
- Checks `loop_result.state.context.get("lane_violations")` → `"lane_violation"` if non-empty
- Checks `loop_result.state.context.get("termination_reason")` → `"stall_detected"` if set
- Falls through to `"goal_not_satisfied"` as default failure class

**Must NOT change:**
- `run_deterministic()` — untouched
- `execution_loop()` — called, not modified
- The compatibility-mode delegation block

**Blast radius:** 6 new private functions added to `deterministic_runner.py`. The compatibility path is the single-line check `if parent_plan["compatibility_mode"]` that already existed in Stage 1. The multi-phase path is behind `else` (implicit, after the early return).

**Rollback:** Remove the 6 helper functions and replace the multi-phase block with `raise NotImplementedError(...)` from Stage 1. Compatibility path unaffected.

---

### S2-F — Two-phase integration tests: `tests/test_two_phase_execution.py`

**What:** Extend the test file from S2-A/B/C/D with full end-to-end integration tests. Use mocked `execution_loop`, mocked `plan()`, and mocked `_docs_seed_plan()` to avoid real LLM calls.

**Tests to write:**

| Test | Asserts |
|---|---|
| `test_run_hierarchical_two_phase_executes_phase0_then_phase1` | Two `execution_loop` calls in order; `phase_results` has 2 entries |
| `test_run_hierarchical_two_phase_phase0_uses_docs_lane` | `phase_state_0.context["dominant_artifact_mode"] == "docs"` |
| `test_run_hierarchical_two_phase_phase1_uses_code_lane` | `phase_state_1.context["dominant_artifact_mode"] == "code"` |
| `test_run_hierarchical_two_phase_execution_loop_receives_phase_subgoal` | `execution_loop` called with `phase_plan.subgoal`, not parent instruction |
| `test_run_hierarchical_two_phase_phase1_state_has_no_docs_lane` | `phase_state_1.context["dominant_artifact_mode"] != "docs"` (no leakage) |
| `test_run_hierarchical_two_phase_phase1_receives_handoff` | `phase_state_1.context` contains `"prior_phase_ranked_context"` from Phase 0 |
| `test_run_hierarchical_two_phase_handoff_not_present_in_phase0` | `phase_state_0.context` does not contain `"prior_phase_ranked_context"` |
| `test_run_hierarchical_two_phase_stop_on_phase0_failure` | Mocked Phase 0 goal_met=False → only 1 `execution_loop` call; `loop_output["errors_encountered"]` non-empty |
| `test_run_hierarchical_two_phase_phase1_not_called_on_phase0_fail` | `execution_loop` called exactly once (Phase 0 only) on stop |
| `test_run_hierarchical_two_phase_aggregated_output_both_succeed` | `loop_output["phase_results"]` has 2 entries; `all phases succeeded` in trace |
| `test_run_hierarchical_two_phase_goal_evaluator_called_with_phase_subgoal` | `GoalEvaluator.evaluate_with_reason` called with `phase_subgoal=phase_plan["subgoal"]` |
| `test_run_hierarchical_two_phase_returns_agentstate_and_dict` | Return type is `(AgentState, dict)` — same shape as `run_deterministic` |
| `test_run_hierarchical_two_phase_loop_output_has_completed_steps` | `loop_output["completed_steps"]` == sum of both phases' completed steps |
| `test_run_hierarchical_still_compat_for_single_intent` | Pure code instruction still takes compat path; no multi-phase behavior |
| `test_phase_context_handoff_pruned_when_large` | Large `ranked_context` from Phase 0 is pruned before injection |
| `test_phase_failure_class_derived_correctly` | Phase state with `lane_violations` → `failure_class="lane_violation"` |

**Dependency:** All of S2-A through S2-E must be complete.

---

### S2-GATE — Full regression run

Same requirements as S1-GATE, plus all Stage 2 new tests must pass.

**Additional scenario that must pass:**
- `"Find architecture docs and explain replanner flow"` (mocked execution) → returns `(AgentState, dict)` where `loop_output["phase_results"]` has 2 entries, Phase 0 docs lane, Phase 1 code lane, `errors_encountered` is empty on success.

---

## 3. Required File-by-File Change Map

### `agent/orchestrator/parent_plan.py` (NEW)

| Category | Detail |
|---|---|
| **Add** | `PhasePlan`, `ParentPlan`, `PhaseResult`, `PhaseValidationContract`, `PhaseRetryPolicy` typed dicts; `new_phase_id()`, `new_parent_plan_id()`, `make_compatibility_parent_plan()`, `validate_parent_plan_schema()` |
| **Must stay untouched** | n/a — new file; must not import from execution modules |
| **Tests that prove safety** | `tests/test_parent_plan_schema.py` — all 15 tests |

---

### `agent/orchestrator/plan_resolver.py` (EXTEND)

| Category | Detail |
|---|---|
| **Add (Stage 1)** | `get_parent_plan()` function; `from agent.orchestrator.parent_plan import ...` import |
| **Add (Stage 2)** | `_is_two_phase_docs_code_intent()`, `_derive_phase_subgoals()`, `_build_two_phase_parent_plan()`; 6-line conditional block at top of `get_parent_plan()` |
| **Must stay untouched** | `get_plan()`, `_is_docs_artifact_intent()`, `_docs_seed_plan()`, `_ensure_plan_id()`, `new_plan_id()`, `_confidence_allows_router_short_circuit()`, all token list constants |
| **Tests that prove safety** | `tests/test_plan_resolver_docs_intent.py` (all existing); `tests/test_intent_understanding_matrix.py` (all existing); `tests/test_two_phase_execution.py::TestDetectionHeuristic` |

---

### `agent/orchestrator/deterministic_runner.py` (EXTEND)

| Category | Detail |
|---|---|
| **Add (Stage 1)** | `run_hierarchical()` (compatibility delegation + NotImplementedError stub); `from agent.orchestrator.plan_resolver import get_parent_plan` import |
| **Add (Stage 2)** | `_build_phase_agent_state()`, `_extract_phase_context_output()`, `_build_phase_context_handoff()`, `_apply_parent_stage2_policy()`, `_aggregate_parent_goal()`, `_build_hierarchical_loop_output()`, `_derive_phase_failure_class()`; phase iteration loop replacing `NotImplementedError` block |
| **Must stay untouched** | `run_deterministic()` — entire function, not a single line |
| **Tests that prove safety** | `tests/test_run_hierarchical_compatibility.py` (all 8 Stage 1 tests); `tests/test_two_phase_execution.py` (all Stage 2 tests); existing scenario tests via full regression |

---

### `agent/orchestrator/goal_evaluator.py` (MINOR EXTEND)

| Category | Detail |
|---|---|
| **Add (Stage 2)** | `phase_subgoal: str \| None = None` keyword-only parameter to `evaluate_with_reason()`; two-line change to derive `effective_instruction` and `instruction_lower` |
| **Must stay untouched** | `evaluate()` method; `is_explain_like_instruction()`; all evaluation logic after `instruction_lower` derivation |
| **Tests that prove safety** | `tests/test_goal_evaluator*.py` (all existing — backward compat); `tests/test_two_phase_execution.py::test_evaluate_*` |

---

### Files that must NOT be modified at all

| File | Reason |
|---|---|
| `agent/orchestrator/execution_loop.py` | Phase-local by contract; receives phase-scoped state |
| `agent/orchestrator/replanner.py` | Phase-local by contract; lane lock within phase is correct |
| `agent/execution/step_dispatcher.py` | Reads lane from `state.context`; phase-scoped state makes it correct |
| `planner/planner.py` | Called per-phase; no schema changes needed |
| `planner/planner_utils.py` | `validate_plan()` is reused unchanged; `is_explicit_docs_lane_by_structure()` is called from `parent_plan.py` |
| `agent/routing/instruction_router.py` | No change to routing |
| All existing test files | Regression gate; not modified |

---

## 4. Interface Decisions That Must Be Locked Before Coding

The following five decisions must have an explicit, written answer before Stage 2 coding (S2-A onward) begins. Stage 1 coding (S1-A through S1-E) may proceed while these are being decided.

---

### Decision 4.1 — Schema representation: typed dict vs dataclass

**Question:** Should `PhasePlan`, `ParentPlan`, `PhaseResult` be `TypedDict` subclasses or `@dataclass` instances?

**Options:**
- A: `TypedDict` — dict-like, JSON-serializable, works with `d["field"]` access, no constructor enforcement
- B: `@dataclass` — attribute access `d.field`, constructor enforcement, IDE support

**Recommendation:** TypedDict. Reason: the existing codebase uses plain dicts throughout (`plan_result`, `loop_output`, `state.context`). TypedDict keeps the new schemas consistent with that pattern. All helper functions building these objects use dict constructors. Callers using `["field"]` access are consistent with the rest of the codebase.

**Must be decided by:** before S1-A coding begins.

**Impact of decision:** Affects how `make_compatibility_parent_plan`, `validate_parent_plan_schema`, and all helper functions are written. All of Stage 1 and 2 coding depends on this.

---

### Decision 4.2 — Phase 1 planner input: phase subgoal vs full parent instruction

**The roadmap (§5.2) originally said Phase 1 planner input = full parent instruction. The refinement changes this: Phase 1 planner input = phase 1 subgoal.**

**Decision: Phase 1 planner input is the phase-1 subgoal derived by `_derive_phase_subgoals()`.**

**Consequence:** `_build_two_phase_parent_plan` calls `plan(phase_1_subgoal)`, not `plan(instruction)`. The planner receives the code-specific subgoal ("Explain replanner flow"), not the mixed instruction ("Find architecture docs and explain replanner flow").

**Risk:** If `_derive_phase_subgoals` produces a bad Phase 1 subgoal (too short, wrong content), the planner may produce a weak plan. Mitigation: the fallback in `_build_two_phase_parent_plan` (§2, S2-C) catches validate_plan failures and falls back to compatibility mode.

**This decision is locked: Phase 1 planner receives the phase-1 subgoal.** No further deliberation needed.

---

### Decision 4.3 — `run_hierarchical()` return value when Phase 0 fails

**Question:** What does `run_hierarchical(instruction, ...)` return when Phase 0 fails and the parent policy is STOP?

**Locked answer:**
- Returns `(last_phase_state, partial_loop_output)` where `last_phase_state` is Phase 0's final `AgentState`.
- `partial_loop_output` contains:
  - `completed_steps` = Phase 0's completed steps
  - `files_modified` = `[]` (no edits happened)
  - `patches_applied` = `[]`
  - `errors_encountered` = `["phase_0_goal_not_met"]` or `["phase_0_failed:<failure_class>"]`
  - `phase_results` = `[phase_0_result]`
  - `start_time` = start time
- This is the same shape as a failed `run_deterministic()` return — callers that check `errors_encountered` will detect the failure.

**No exception is raised.** Failure is surfaced through the `loop_output` dict, consistent with how `run_deterministic()` surfaces failures.

---

### Decision 4.4 — Phase context handoff: what if `ranked_context` is empty?

**Question:** If Phase 0 succeeds (docs goal met, EXPLAIN ran) but `ranked_context` is empty, should Phase 1 receive an empty handoff or should Phase 0 be considered failed?

**Locked answer:**
- Phase 0 success is determined by `goal_evaluator.evaluate_with_reason()`, specifically the `docs_lane_explain_succeeded` signal (docs lane + EXPLAIN succeeded). `ranked_context` emptiness is not an additional gate in Stage 2.
- If `ranked_context` is empty, `_build_phase_context_handoff` produces an empty `prior_phase_ranked_context` list. Phase 1 proceeds with an empty handoff. This is acceptable for Stage 2 — the handoff is informational, not required.
- In Stage 3, `PhaseValidationContract.require_ranked_context=True` will be enforced and Phase 0 would fail if `ranked_context` is empty. That enforcement is not wired in Stage 2.

---

### Decision 4.5 — Phase subgoal derivation: connector detection strategy

**Question:** What is the exact algorithm for `_derive_phase_subgoals(instruction) -> tuple[str, str]`?

**Locked answer:**

```
connectors_to_try = [" and explain ", " and describe ", " and show how ", " and summarize ", " and walk through "]
instruction_lower = instruction.strip().lower()
for connector in connectors_to_try:
    pos = instruction_lower.find(connector)
    if pos != -1:
        raw_phase1 = instruction[pos + len(connector):].strip()
        phase_1_subgoal = raw_phase1[0].upper() + raw_phase1[1:] if raw_phase1 else instruction
        phase_0_subgoal = "Find documentation artifacts relevant to: " + instruction[:150]
        return phase_0_subgoal, phase_1_subgoal
# fallback: no connector found
return "Find documentation artifacts relevant to: " + instruction[:150], instruction
```

**Agreed constraints:**
- Detection is case-insensitive; subgoal output preserves original casing.
- If Phase 1 subgoal produced by splitting is shorter than 10 characters, use full instruction as fallback.
- No LLM calls.
- This list of connectors may be extended in future stages without breaking any existing tests — it is additive.

---

## 5. Open Questions Before Stage 2 Coding

The following questions are open but have recommended answers. Each one must be explicitly confirmed as "go with recommendation" or overridden before Stage 2 coding begins.

---

### Q1 — What does `execution_loop()` receive as its `instruction` argument when called for Phase 1?

**Context:** `execution_loop(state, instruction, ...)`. The `instruction` argument is used internally by `execution_loop` for goal evaluation, replanner calls, and trace logging.

**Recommended answer:** Pass `phase_plan["subgoal"]` — the phase-scoped subgoal — as the `instruction` argument to `execution_loop`. Not the parent instruction. This ensures: (a) the replanner's instruction truncation uses the right text, (b) goal evaluation inside the loop uses the right text, (c) trace events log the correct instruction.

**Consequence:** The replanner inside Phase 1's execution_loop will truncate `phase_plan["subgoal"]` at 1500 chars, not the parent instruction. This is correct.

**Must confirm or override before S2-E.**

---

### Q2 — Should `state.instruction` on a phase state be the phase subgoal or the parent instruction?

**Context:** `AgentState.instruction` is read by the replanner (`state.instruction[:1500]`), the goal evaluator, and possibly step_dispatcher.

**Recommended answer:** `state.instruction = phase_plan["subgoal"]`. The phase state is a self-contained execution context. Components inside the phase should see the phase's subgoal as the instruction, not the mixed parent instruction. The parent instruction is stored separately in `state.context["parent_instruction"]` for reference if needed.

**Consequence:** The replanner, if triggered, will try to recover the phase's subgoal, not the full mixed instruction. This is correct phase-local behavior.

**Must confirm or override before S2-E.**

---

### Q3 — What is the exact failure_class derivation for Phase 0 when `goal_met=False`?

**Context:** `PhaseResult.failure_class` must be one of the `FAILURE_CLASS_*` constants from `agent/contracts/error_codes.py`, or a string.

**Recommended answer:** Derive from `loop_result.state.context`:
- `lane_violations` list non-empty → `"lane_violation"`
- `termination_reason == "stall_detected"` → `"stall_detected"`
- `errors_encountered` contains `"max_task_runtime_exceeded"` → `"timeout"`
- `errors_encountered` contains `"max_steps"` or `"max_tool_calls"` → `"limit_exceeded"`
- `goal_met=False` with no other signal → `"goal_not_satisfied"` (the existing `FAILURE_CLASS_GOAL_NOT_SATISFIED` constant)

**Must confirm or override before S2-E.**

---

### Q4 — How is `PhaseResult.success` set — from `goal_met` directly or from `loop_output` signals?

**Context:** `goal_met` comes from `GoalEvaluator`. But `loop_output` may have `errors_encountered` even if `goal_met=True` (e.g., a replan happened but ultimately succeeded).

**Recommended answer:** `phase_result["success"] = phase_result["goal_met"]`. Success is determined entirely by goal evaluation. If `goal_met=True`, the phase succeeded regardless of intermediate errors. `errors_encountered` is informational. This is consistent with how `run_deterministic()` treats goal evaluation.

**Must confirm or override before S2-E.**

---

### Q5 — Should `_build_two_phase_parent_plan()` make a real planner call (LLM) for Phase 1?

**Context:** Building Phase 1 steps requires calling `plan(phase_1_subgoal)` which is an LLM call. This means `get_parent_plan()` now makes an extra LLM call for two-phase instructions (previously, the plan was made later in `run_deterministic`).

**Recommended answer:** Yes, make the real planner call in `_build_two_phase_parent_plan()`. The plan must be built before execution starts. The cost (one extra planner call) is acceptable for two-phase instructions. Caching is not required in Stage 2.

**Alternative:** Defer Phase 1 plan construction to inside `run_hierarchical()` just before Phase 1 execution. This avoids the extra call in `get_parent_plan()` but complicates the schema (Phase 1 `PhasePlan.steps` would be empty until populated at runtime). This alternative is fragile and not recommended.

**Must confirm or override before S2-C.**

---

## 6. Red-Line Rules

These are absolute prohibitions for Stage 1 and Stage 2 implementation. Violation of any of these rules is a blocking reason to reject a pull request.

**RL-1 — No `validate_plan()` relaxation.**  
`planner/planner_utils.py::validate_plan()` must not be modified. Mixed-lane plans within a single phase remain forbidden. Any attempt to relax single-lane enforcement inside a phase to "make things work" is a bug, not a fix.

**RL-2 — No `execution_loop()` modification.**  
`agent/orchestrator/execution_loop.py` must not be touched. The loop is phase-local by design — it receives a phase-scoped state and operates within it. No loop-level change is needed.

**RL-3 — No `replanner.py` modification.**  
`agent/orchestrator/replanner.py` must not be touched. The replanner is already phase-local — it reads `dominant_artifact_mode` from state and stays within the phase's lane. This is correct behavior.

**RL-4 — No `step_dispatcher.py` modification.**  
`agent/execution/step_dispatcher.py` must not be touched. Lane enforcement via `_enforce_runtime_lane_contract()` reads `dominant_artifact_mode` from `state.context`. Phase-scoped state with the correct lane makes it work correctly without any dispatcher changes.

**RL-5 — No `run_deterministic()` modification.**  
`agent/orchestrator/deterministic_runner.py::run_deterministic()` must not be touched. It is the compatibility fallback. If `run_hierarchical()` needs a capability that `run_deterministic()` provides, call `run_deterministic()` — do not copy its logic.

**RL-6 — No parallel phases.**  
All phases execute sequentially. No thread pools, no async, no concurrent `execution_loop()` calls. Phase N+1 does not begin until Phase N has returned.

**RL-7 — No 3+ phase support.**  
`_build_two_phase_parent_plan()` produces exactly 2 phases. `run_hierarchical()` in Stage 2 may only handle `len(phases) == 1` (compatibility) or `len(phases) == 2` (two-phase). For `len(phases) > 2`, raise `NotImplementedError`.

**RL-8 — No clarification response path.**  
No code in Stage 1 or Stage 2 may produce a response that says "your request is ambiguous, please clarify." If `_is_two_phase_docs_code_intent()` does not fire, the instruction falls through to the existing compatibility path. That is the correct Stage 2 behavior for unrecognized mixed intent.

**RL-9 — No changes to existing test files.**  
All files in `tests/` that existed before this work began must not be modified. If an existing test needs updating because of a signature change, that is a sign the implementation broke backward compatibility — fix the implementation, not the test.

**RL-10 — `parent_plan.py` must not trigger LLM or tool initialization on import.**  
No model client imports, no tool imports, no config imports that load heavy dependencies. This module must be importable in a pure Python unit test environment.

---

## 7. Final Recommended Coding Order

This is the sequence an engineer should follow. Each step has a clear done criterion.

```
Step 1.  git tag stage1-start (rollback point)

Step 2.  Write tests/test_parent_plan_schema.py (all 15 tests)
         → All tests FAIL (module doesn't exist yet)

Step 3.  Write agent/orchestrator/parent_plan.py (S1-A)
         → tests/test_parent_plan_schema.py goes GREEN

Step 4.  Add get_parent_plan() to plan_resolver.py (S1-C)
         → Confirm: existing plan_resolver tests still pass

Step 5.  Add run_hierarchical() (Stage 1 stub) to deterministic_runner.py (S1-D)
         → Confirm: existing deterministic_runner behavior unchanged

Step 6.  Write tests/test_run_hierarchical_compatibility.py (all 8 tests)
         → All 8 new tests GREEN
         → Full test suite GREEN
         → git tag stage1-complete

Step 7.  *** CONFIRM §4 DECISIONS 4.2–4.5 AND §5 QUESTIONS Q1–Q5 IN WRITING ***
         Do not proceed to Step 8 without explicit confirmation.

Step 8.  git tag stage2-start (rollback point)

Step 9.  Write Stage 2 test stubs (failing) in tests/test_two_phase_execution.py:
         - TestDetectionHeuristic (S2-A tests)
         - TestSubgoalDerivation (S2-B tests)
         - All tests FAIL

Step 10. Add _is_two_phase_docs_code_intent() to plan_resolver.py (S2-A)
         → TestDetectionHeuristic goes GREEN
         → Existing plan_resolver tests still pass

Step 11. Add _derive_phase_subgoals() to plan_resolver.py (S2-B)
         → TestSubgoalDerivation goes GREEN

Step 12. Add _build_two_phase_parent_plan() to plan_resolver.py +
         update get_parent_plan() (S2-C)
         → TestBuildTwoPhase tests go GREEN
         → Single-intent tests still pass (no regression)
         → Confirm validate_plan() called on both phase step lists

Step 13. Extend GoalEvaluator.evaluate_with_reason() (S2-D)
         → test_evaluate_* tests go GREEN
         → All existing goal_evaluator tests still pass (backward compat)

Step 14. Add all helper functions + phase loop to run_hierarchical() (S2-E)
         → test_run_hierarchical_two_phase_* tests go GREEN
         → test_run_hierarchical_still_compat_for_single_intent passes

Step 15. Full test suite run — zero regressions
         → git tag stage2-complete
```

**Why this order:**

- Tests before code (steps 2, 9) catches interface design mistakes early before they are baked into implementations.
- Schema and detection code before loop code (steps 3–12 before 14) because the loop depends on the schema and the plan construction — wrong schemas discovered late are expensive.
- Goal evaluator change (step 13) before the loop (step 14) because the loop calls the evaluator; if the evaluator interface is wrong, the loop tests will all fail for the wrong reason.
- The explicit decision gate at step 7 prevents Stage 2 coding from starting while any interface question is still open. An open question at step 7 is a planning failure that must be resolved in writing, not by making assumptions in code.

---

*End of task breakdown.*
