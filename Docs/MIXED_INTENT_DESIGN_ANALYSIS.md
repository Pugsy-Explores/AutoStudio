# Mixed Intent Design Analysis — Single-Lane Constraint

**Status:** Design analysis only. No implementation.  
**Scope:** Handling mixed docs + code instructions (e.g. "Find architecture docs and explain replanner flow").  
**Constraint:** No prompt changes, retrieval, build_context, or diff changes.

---

## Section 1: Current Failure Path

### 1.1 Exact Failure Path (Code-Grounded)

```
instruction: "Find architecture docs and explain replanner flow"
    │
    ▼
plan_resolver.get_plan()
    │
    ├─► _is_docs_artifact_intent(instruction)
    │       │  has_discovery_verb: "find" ✓
    │       │  has_docs: "docs", "architecture" ✓
    │       │  has_non_docs: "explain" ∈ _NON_DOCS_TOKENS ✓  →  returns True
    │       └─► BLOCKED. Docs override does NOT fire.
    │
    ├─► route_instruction(instruction)
    │       │  _is_code_explain_behavior_intent: "how does" not in instruction → False
    │       │  Model/router returns CODE_EXPLAIN (or GENERAL → planner)
    │       └─► RouterDecision(CODE_EXPLAIN, 0.9)
    │
    ├─► plan_resolver short-circuit (lines 251–261)
    │       └─► plan = { steps: [ { action: "EXPLAIN", description: instruction } ] }
    │           No artifact_mode → code lane by default
    │
    ▼
run_deterministic()
    │  dominant_artifact_mode = "code"  (is_explicit_docs_lane_by_structure → False)
    │  state.context["dominant_artifact_mode"] = "code"
    ▼
execution_loop() → single EXPLAIN step (code lane)
    │  Retrieval uses code pipeline; no docs SEARCH_CANDIDATES.
    └─► User receives code-only explanation; docs discovery never runs.
```

**Outcome:** The instruction is collapsed into a single code-lane EXPLAIN. Docs discovery is never attempted.

### 1.2 Second Failure Path: Validation Rejection (Xfail Scenario)

The xfail `test_desired_validate_plan_accepts_interleaved_docs_then_code_steps` asserts a **hypothetical** plan that would represent mixed intent:

```python
plan = {
    "steps": [
        {"id": 1, "action": "SEARCH_CANDIDATES", "artifact_mode": "docs", ...},
        {"id": 2, "action": "SEARCH", "description": "replanner implementation", ...},  # code
    ]
}
normalize_actions(plan)
assert validate_plan(plan) is True  # FAILS today
```

**validate_plan rejection** (`planner_utils.py` 79–97):

- `has_any_docs_step` or `docs_by_structure` → True (step 1 has `artifact_mode="docs"`).
- Docs-lane branch: for each step, if `action in ("SEARCH", "EDIT")` → **return False**.
- Step 2 has `action="SEARCH"` → **validate_plan returns False**.

**Planner fallback** (`planner.py` 258–266): On validation failure, `_build_controlled_fallback_plan` runs. No docs lineage in parsed plan → code fallback → single SEARCH step. Mixed intent is discarded.

### 1.3 Summary: What Breaks

| Stage | Behavior | Consequence |
|-------|----------|-------------|
| Docs heuristic | `"explain"` in _NON_DOCS_TOKENS blocks docs override | Mixed phrasing never gets docs seed plan |
| Router | Returns CODE_EXPLAIN or GENERAL | Single-step or planner path; no mixed signal |
| Planner (if invoked) | Could emit docs + code steps | validate_plan rejects; fallback to single SEARCH |
| dominant_artifact_mode | Set once from plan structure | Single lane for entire task |
| Replanner | `_enforce_replan_lane_contract` | Cannot emit mixed plans; locked to dominant lane |
| Goal evaluator | Docs success = docs lane + EXPLAIN ok | Mixed task has no matching success rule |

---

## Section 2: Design Options

### Design A: Sequential Plans (Multi-Pass Execution)

**Idea:** Treat mixed intent as two separate tasks. Run docs phase to completion, then run code phase with a derived instruction.

**Flow:**
1. Detect mixed intent (e.g. `_is_mixed_docs_code_intent(instruction)`).
2. `get_plan` returns a **meta-plan** or **phase descriptor** (e.g. `{"phases": ["docs", "code"], "instruction": ...}`).
3. `run_deterministic` or a new orchestrator:
   - Phase 1: Call `get_plan` with docs-only derived instruction → docs seed plan → execution_loop.
   - Phase 2: Call `get_plan` with code-only derived instruction (or original) → code plan → execution_loop.
4. Each phase has its own `dominant_artifact_mode`; no mixed plan.

**Required code changes:**

| File | Function | Change |
|------|----------|--------|
| `plan_resolver.py` | New `_is_mixed_docs_code_intent` | Heuristic: docs tokens + code tokens (explain, flow, implemented, etc.) |
| `plan_resolver.py` | `get_plan` | When mixed: return `{"phases": ["docs", "code"], "instruction": ..., "phase_plan": None}` or similar |
| `deterministic_runner.py` | `run_deterministic` | If plan has phases: loop over phases, call `get_plan` per phase with phase-scoped instruction, run execution_loop per phase |
| `planner_utils.py` | `validate_plan` | No change (each phase plan is single-lane) |

**Impact:**

- **validate_plan:** Unchanged.
- **plan_resolver:** New mixed-intent branch; phase splitting logic.
- **execution_loop:** Invoked multiple times per task; state must be carried across phases (ranked_context, etc.).
- **replanner:** Unchanged per phase.
- **goal_evaluator:** Must consider multi-phase success (e.g. docs phase ok AND code phase ok).

**Risks:**

- **Determinism:** Phase derivation (splitting instruction into docs vs code) is heuristic; may mis-split.
- **Runtime:** Two full execution loops; 2× planner/router calls if not cached.
- **State handoff:** Phase 2 needs Phase 1 context (e.g. docs found) for coherent "explain replanner flow" — requires new state merge logic.
- **Regressions:** `run_deterministic` signature and behavior change; all callers affected.

**Compatibility:** Tests that assume single `get_plan` → single `execution_loop` must be updated. `test_desired_validate_plan_accepts_interleaved_docs_then_code_steps` would not directly apply (validation is per-phase; no mixed plan to validate).

---

### Design B: Multi-Phase Plan (Single Plan, Ordered Phases)

**Idea:** Extend the plan schema so a single plan can contain ordered phases. Each phase is a contiguous subsequence of steps with a single artifact_mode. Validation allows multiple phases if they are strictly ordered (docs phase, then code phase).

**Flow:**
1. New plan shape: `{"steps": [...], "phases": [{"start": 0, "end": 3, "artifact_mode": "docs"}, {"start": 3, "end": 5, "artifact_mode": "code"}]}` or steps carry `phase_id` / `artifact_mode` with phase boundaries.
2. `validate_plan` accepts plans where steps are grouped into non-overlapping phases; no mixing within a phase.
3. `deterministic_runner` sets `dominant_artifact_mode` per step from the step's phase (or from plan structure).
4. Execution loop: each step's `artifact_mode` is read from the step; `state.context["dominant_artifact_mode"]` becomes step-scoped or phase-scoped.

**Required code changes:**

| File | Function | Change |
|------|----------|--------|
| `planner_utils.py` | `validate_plan` | New rule: allow steps with different artifact_mode iff they form contiguous phases (docs then code). Reject interleaved docs/code. |
| `planner_utils.py` | `is_explicit_docs_lane_by_structure` | Generalize: plan may be "mixed" (multi-phase); return False for pure docs lane check, or new `get_artifact_mode_for_step(plan, step_index)`. |
| `plan_resolver.py` | `get_plan` | When mixed intent: call planner with mixed-intent hint, or emit fixed two-phase seed plan (docs phase steps + code phase steps). |
| `deterministic_runner.py` | `run_deterministic` | `dominant_artifact_mode` cannot be single value; must be per-step. Store `phase_artifact_modes` or derive from plan. |
| `step_dispatcher.py` | `dispatch` | Use step's `artifact_mode` (already on step) instead of `state.context["dominant_artifact_mode"]` when present. |
| `replanner.py` | `_enforce_replan_lane_contract`, `_dominant_lane` | Replan must preserve phase structure; `_dominant_lane` becomes step-contextual. |
| `agent/orchestrator/validator.py` | (if lane checks) | Step validation uses step's artifact_mode. |
| `goal_evaluator.py` | `evaluate_with_reason` | Success for mixed: both phases completed successfully. |

**Impact:**

- **validate_plan:** Major change. New "phase-ordered" rule. Must not break existing single-lane plans.
- **plan_resolver:** New mixed-intent branch; emit or request two-phase plan.
- **execution_loop:** Minimal if step carries artifact_mode; loop already passes step to dispatch.
- **replanner:** Must not emit interleaved steps; fallbacks must be phase-consistent.
- **goal_evaluator:** New success path for mixed tasks.

**Risks:**

- **Determinism:** Phase boundary detection in validate_plan must be exact.
- **Planner output:** Planner must emit phase boundaries; prompt changes may be needed (user said no prompt changes — so we need deterministic phase injection in plan_resolver).
- **Replanner:** On failure in phase 2, replan must not inject docs steps into code phase.
- **Regressions:** `dominant_artifact_mode` is used in many places; changing to per-step is a broad refactor.

**Compatibility:** `test_desired_validate_plan_accepts_interleaved_docs_then_code_steps` would pass if validate_plan accepts docs-then-code ordered phases. Many tests assume single `dominant_artifact_mode`; would need updates.

---

### Design C: Explicit Rejection + Clarification

**Idea:** Detect mixed intent and **reject** the plan with a structured error. Return a plan that contains a single EXPLAIN (or similar) step whose purpose is to ask the user to clarify: "I can either find architecture docs or explain replanner flow — which would you prefer?" Optionally, surface a clarification message without executing.

**Flow:**
1. `_is_mixed_docs_code_intent(instruction)` → True.
2. `get_plan` returns a **clarification plan**: e.g. one EXPLAIN step with `description` = "Clarification needed: your request combines docs discovery and code explanation. Please choose one: (1) Find architecture docs, or (2) Explain replanner flow."
3. Execution runs that EXPLAIN; the model produces a clarification message. No docs retrieval, no code retrieval.
4. Alternatively: `get_plan` returns `{"error": "mixed_intent", "clarification": "...", "steps": []}` and execution_loop short-circuits with a user-facing message.

**Required code changes:**

| File | Function | Change |
|------|----------|--------|
| `plan_resolver.py` | New `_is_mixed_docs_code_intent` | Heuristic: has docs tokens + has code/non-docs tokens |
| `plan_resolver.py` | `get_plan` | When mixed: return plan with single EXPLAIN step (clarification) or error plan. Do NOT call planner for mixed. |
| `execution_loop.py` | (optional) | If plan has `error="mixed_intent"`, skip execution and return early with clarification. |
| `planner_utils.py` | `validate_plan` | No change. Clarification plan is single EXPLAIN (code lane). |

**Impact:**

- **validate_plan:** No change.
- **plan_resolver:** New mixed-intent branch; returns clarification plan.
- **execution_loop:** Optional early exit for error plan.
- **replanner:** No change.
- **goal_evaluator:** Clarification EXPLAIN succeeds → goal "met" in a narrow sense (user got a message). May want to treat `mixed_intent` as goal_not_satisfied so replan does not trigger.

**Risks:**

- **UX:** User must re-submit; extra round-trip.
- **Determinism:** Heuristic may false-positive (block valid single-intent) or false-negative (miss mixed).
- **Regressions:** Low. New branch only; existing paths unchanged.

**Compatibility:** `test_desired_validate_plan_accepts_interleaved_docs_then_code_steps` would **not** pass — we are not accepting mixed plans. We would add a new test: `test_mixed_intent_returns_clarification_plan` and keep the xfail as documenting "we do not support interleaved execution" (different contract).

---

## Section 3: Recommendation

**Recommendation: Design C (Explicit Rejection + Clarification)**

### Justification

1. **Minimal blast radius**
   - Only `plan_resolver.py` and a small heuristic change. No changes to `validate_plan`, `execution_loop` internals, `replanner` lane contract, or `goal_evaluator` success rules.
   - `deterministic_runner` and `step_dispatcher` unchanged.
   - New code is additive: a branch in `get_plan` when mixed intent is detected.

2. **Determinism**
   - Detection is heuristic (bounded token lists). No LLM for classification. Same pattern as `_is_docs_artifact_intent` and `_is_code_explain_behavior_intent`.
   - Clarification plan is a fixed structure: one EXPLAIN step with deterministic description template.

3. **Alignment with current architecture**
   - Preserves single-lane contract. No phase schema, no per-step artifact_mode override, no multi-pass execution.
   - `validate_plan` remains the single-lane enforcer; we never produce a mixed plan.
   - Replanner and goal evaluator unchanged.
   - Fits the existing "fast short-circuits" pattern: mixed intent → immediate structured response, no planner call.

4. **Pragmatic**
   - Design A requires state handoff and phase derivation — high complexity.
   - Design B requires a broad refactor of `dominant_artifact_mode` and phase-aware validation — high risk.
   - Design C delivers a clear user signal with minimal implementation and preserves the option to implement A or B later.

---

## Section 4: Minimal Implementation Slice

### 4.1 Exact Functions to Change

| File | Function | Change |
|------|----------|--------|
| `plan_resolver.py` | New `_is_mixed_docs_code_intent(instruction: str) -> bool` | Return True when: (1) has docs-discovery verb, (2) has docs token, (3) has non-docs token. Reuse `_DOCS_DISCOVERY_VERBS`, `_DOCS_INTENT_TOKENS`, `_NON_DOCS_TOKENS`. |
| `plan_resolver.py` | `get_plan` | After `_is_docs_artifact_intent` check, before router: if `_is_mixed_docs_code_intent(instruction)`, return `_mixed_intent_clarification_plan(instruction)`. |
| `plan_resolver.py` | New `_mixed_intent_clarification_plan(instruction: str) -> dict` | Return plan with single EXPLAIN step, `description` = template: "Your request combines documentation discovery and code explanation. Please submit separately: (1) Find architecture docs, or (2) Explain replanner flow." |

### 4.2 What NOT to Change

- `planner_utils.py` — validate_plan, normalize_actions, is_explicit_docs_lane_by_structure
- `planner/planner.py` — plan, _build_controlled_fallback_plan
- `agent/orchestrator/replanner.py` — replan, _enforce_replan_lane_contract
- `agent/orchestrator/execution_loop.py` — execution_loop
- `agent/orchestrator/deterministic_runner.py` — run_deterministic
- `agent/orchestrator/goal_evaluator.py` — evaluate_with_reason
- `agent/orchestrator/validator.py` — validate_step
- `agent/execution/step_dispatcher.py` — dispatch
- `agent/routing/instruction_router.py` — route_instruction
- Prompts, retrieval, build_context, diff/patch, scenario harnesses

### 4.3 Tests

**Xfail handling:**

- `test_desired_validate_plan_accepts_interleaved_docs_then_code_steps` — **Remains xfail**. Design C does not accept interleaved plans. The test documents the "no mixed execution" contract. The xfail reason can be updated to: "Design C: mixed intent returns clarification plan; validate_plan still rejects mixed plans."

**New tests to add:**

- `test_mixed_intent_returns_clarification_plan`: For "Find architecture docs and explain replanner flow", `get_plan` returns a plan with one EXPLAIN step; router not called; plan has no docs artifact_mode.
- `test_mixed_intent_docs_override_not_used`: When mixed, docs override does not fire (mixed check runs after docs check, but docs check would have been blocked by _NON_DOCS_TOKENS anyway — so we need to ensure mixed branch runs before router for mixed instructions that would otherwise reach planner).

**Order of checks in get_plan:**

1. `_is_docs_artifact_intent` (existing)
2. `_is_mixed_docs_code_intent` (new) → clarification plan
3. `route_instruction` (existing)

This order ensures mixed intent is caught before router. For "Find architecture docs and explain replanner flow", docs intent is blocked by _NON_DOCS_TOKENS, so we fall through. Then mixed check: has docs verb + docs token + non-docs token → True → clarification plan.

### 4.4 Edge Cases

- **"Find architecture docs"** (docs only): `_is_docs_artifact_intent` → True (no non-docs). Docs override. Mixed → False. OK.
- **"Explain replanner flow"** (code only): Docs intent → False. Mixed → False (no docs token in "explain replanner flow"? "docs" not in instruction). OK.
- **"Find architecture docs and explain replanner flow"**: Docs intent → False (explain in _NON_DOCS_TOKENS). Mixed → True. Clarification plan. OK.

**Mixed heuristic:** `_is_mixed_docs_code_intent` = has_discovery_verb AND has_docs AND has_non_docs. Uses same token lists. Narrow: only fires when we would have blocked docs override due to non-docs token. Safe.

---

*End of design analysis.*
