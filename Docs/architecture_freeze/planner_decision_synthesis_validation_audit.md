# Planner: Decision → Synthesis → Validation (audit & minimal extension plan)

Staff-level audit of AutoStudio `agent_v2` as of the decision to add an explicit **validation** stage. This document is the single reference for implementation; it incorporates two **non-optional** coupling and loop-guard rules (§4.1, §6.1).

---

## 1. Component mapping

| Stage | Location (primary) | Notes |
|--------|--------------------|--------|
| **Decision** | `agent_v2/planner/planner_v2.py` (`PlannerV2`) | LLM emits `PlanDocument` with `engine` (`explore` / `act` / `replan` / `stop` / `synthesize` / `plan`) |
| | `agent_v2/runtime/planner_decision_mapper.py` | `planner_decision_from_plan_document` → `PlannerDecision` |
| | `agent_v2/schemas/planner_decision.py` | Runtime control type |
| | `agent_v2/planning/task_planner.py` | `TaskPlannerService` / `RuleBasedTaskPlannerService` (authoritative/shadow) |
| | `agent_v2/planning/decision_snapshot.py` | `build_planner_decision_snapshot` |
| | `agent_v2/runtime/planner_task_runtime.py` | `_run_act_controller_loop` — interprets `PlannerDecision` |
| **Reasoning (synthesis)** | `agent_v2/exploration/answer_synthesizer.py` | `synthesize_answer`, `maybe_synthesize_to_state` |
| | `agent_v2/schemas/answer_synthesis.py` | `AnswerSynthesisInput`, `AnswerSynthesisResult`, `derive_answer_synthesis_coverage` |
| | `agent_v2/exploration/exploration_llm_synthesizer.py` | In-exploration LLM synthesis (distinct from final answer synthesis) |
| | `agent_v2/exploration/understanding_analyzer.py` | `UnderstandingAnalyzer` → `UnderstandingResult` |
| **Retrieval** | `agent_v2/runtime/exploration_runner.py` | Entry → `FinalExplorationSchema` |
| | `agent_v2/exploration/exploration_engine_v2.py` | Bounded exploration loop |
| | `graph_expander.py`, `fetcher.py`, `exploration_scoper.py`, `candidate_selector.py`, … | Scoped retrieval |
| **Validation (answer/reasoning)** | **NONE today** | Plan shape only: `plan_validator.py`, `replan_result_validator.py` |

**Not** the proposed validation stage:

- `agent_v2/validation/plan_validator.py` — `PlanDocument` / tool policy only.
- `agent_v2/planning/exploration_outcome_policy.py` — heuristic gates on exploration + task memory.
- `derive_answer_synthesis_coverage` — deterministic input tagging for the synthesizer prompt, not a post-hoc answer check.

---

## 2. Gap analysis

### Decision

- **State-aware:** Yes (`PlannerPlanContext`, `SessionMemory`, metadata).
- **Loop-based:** Yes (`_run_act_controller_loop`, budgets).
- **Vocabulary gap:** Engine uses `explore` / `synthesize` / … not literally `retrieve` / `retry`; mapping is straightforward.

### Reasoning

- **Structured:** `AnswerSynthesisResult` (answer, explanation, citations, uncertainty, coverage, success).
- **Reusable as Stage 2** without mandatory changes.

### Validation (new)

- **Missing:** Any module that takes `(instruction, exploration context, synthesis output)` and returns structured completeness, issues, missing context, and confidence for **closing the loop**.

---

## 3. Reusable components

**As-is**

- `synthesize_answer` / `maybe_synthesize_to_state`, `AnswerSynthesis*` schemas.
- `ExplorationRunner` / `ExplorationEngineV2` and related retrieval.
- `PlannerDecision`, `planner_decision_from_plan_document`, `_run_act_controller_loop` (structure + budgets).

**Partial**

- Decision prompts / `PlannerV2` — extend for `validation_feedback` and hard synthesize guard (see §4–§6).
- `derive_answer_synthesis_coverage` — optional input to validator, not sole signal.

**Do not conflate**

- `PlanValidator` stays **plan** integrity only.

---

## 4. Validation module design

### 4.1 Validation → decision coupling (first-class, mandatory)

Validation output must **not** be an informal string blob or best-effort context append. If the planner can treat it as a weak hint, most of the benefit is lost (ignored → repeated useless synthesis).

**Concrete requirement**

1. **Schema:** `AnswerValidationResult` in e.g. `agent_v2/schemas/answer_validation.py`:

   - `is_complete: bool`
   - `issues: list[str]`
   - `missing_context: list[str]`
   - `confidence: Literal["low", "medium", "high"]`

2. **Planner input:** Add to `PlannerPlanContext` (`agent_v2/schemas/planner_plan_context.py`):

   ```text
   validation_feedback: Optional[AnswerValidationResult] = None
   ```

   Runtime **must** set this field after each validation run (not only log to metadata). Prompt construction in `PlannerV2` / context builders **must** inject this block when present.

3. **Prompt contract (enforce in YAML / instructions):**

   - If `validation_feedback` is present and `is_complete is False`:
     - **Prioritize retrieval** using `missing_context` (and `issues` for rationale).
     - **Do not** choose `synthesize` as the immediate next decision.
   - If `is_complete is True`: normal policy (may stop, act, or synthesize per product rules).

Without (2)–(3), the degenerate loop is: **synthesize → validate → synthesize again** with no forced retrieval.

### 4.2 Validator API

```python
def validate_answer(
    *,
    instruction: str,
    exploration: FinalExplorationSchema,
    synthesis: AnswerSynthesisResult,
    langfuse_parent: Any | None = None,
) -> AnswerValidationResult: ...
```

**Placement:** e.g. `agent_v2/validation/answer_validator.py` + prompt registry entry if LLM-backed.

**v1 implementation:** hybrid rules + optional LLM; bounded evidence in the prompt.

---

## 5. Minimal implementation plan

**Phase 1** — `AnswerValidationResult` schema; extend `PlannerPlanContext` with `validation_feedback`.

**Phase 2** — `validate_answer` module + tests; wire prompts to surface `validation_feedback` in the planner user/system context.

**Phase 3** — `PlannerTaskRuntime`: after `synthesize` + `maybe_synthesize_to_state`, run validation, set `state.context["answer_validation"]`, and **populate `validation_feedback` on the next `exploration_to_planner_context` (or equivalent)** so the following planner call sees it as first-class input.

**Phase 4** — **Code-enforced loop guard** (§6.1), not prompt-only.

**Phase 5** — Telemetry, max validation rounds per task (config).

---

## 6. Planner loop design

### 6.1 Hard rule: no immediate re-synthesize after validation failure

**Rule (mandatory in code):** If the last transition was **validation completed** with `is_complete is False`, the **next** resolved planner decision **MUST NOT** be `synthesize`. The allowed outcomes are at least **`explore`** (retrieve) or **`act`** (concrete tool step), until validation passes or a global budget aborts.

Enforcement options (pick one minimal approach):

- Set `state.metadata` e.g. `post_validation_synthesize_blocked: true` until an `explore` or successful `act` progress tick clears it; **or**
- In `_run_act_controller_loop`, if guard active, coerce `PlannerDecision(type="synthesize", …)` to `explore` with query derived from `missing_context` (last resort; prefer fixing planner output via prompt + assert in tests).

This guarantees the productive pattern:

```text
retrieve → synthesize → validate → retrieve → … → validate → complete
```

and forbids:

```text
synthesize → validate → synthesize  ✗
```

### 6.2 Pseudo-code (with coupling + guard)

```text
exploration = exploration_runner.run(instruction)
state.context ← exploration

while not done:
    plan_doc = planner_v2(PlannerPlanContext(
        exploration=...,
        validation_feedback=state.validation_feedback,  # first-class; None until after first validation
        ...
    ))
    decision = resolve_decision(plan_doc)

    if post_validation_synthesize_blocked and decision.type == "synthesize":
        decision = coerce_to_explore_or_act(decision, validation_feedback)  # hard guard

    if decision == stop:
        return state

    if decision == explore:
        exploration = exploration_runner.run(decision.query)
        clear_post_validation_synthesize_block_if_explore()  # guard lifecycle
        merge_plan(...)
        continue

    if decision == synthesize:
        maybe_synthesize_to_state(...)
        syn = AnswerSynthesisResult(**state.context["answer_synthesis"])
        v = validate_answer(instruction, exploration, syn)
        state.validation_feedback = v
        state.context["answer_validation"] = v.model_dump()

        if v.is_complete:
            clear_post_validation_synthesize_block()
            return state  # or emit stop on next tick

        set_post_validation_synthesize_block(True)  # HARD: next tick cannot be synthesize
        continue

    if decision == act:
        run_executor_step(...)
        clear_post_validation_synthesize_block_on_progress(...)  # product-defined
        ...
        continue

    ...
```

---

## 7. Files touch list (reference)

| Action | Path |
|--------|------|
| Create | `agent_v2/schemas/answer_validation.py` |
| Create | `agent_v2/validation/answer_validator.py` |
| Create | `agent/prompt_versions/answer_validation/...` (if LLM) |
| Modify | `agent_v2/schemas/planner_plan_context.py` — `validation_feedback` |
| Modify | `exploration_planning_input.py` / planner context builder — pass `validation_feedback` |
| Modify | `planner_v2.py` / prompt parts — document `validation_feedback` block |
| Modify | `planner_task_runtime.py` — validate after synthesize + guard |
| Modify | `agent/prompt_system/registry.py` (if new task) |
| Modify | `agent_v2/config.py` — limits/flags |

---

*End of document.*
