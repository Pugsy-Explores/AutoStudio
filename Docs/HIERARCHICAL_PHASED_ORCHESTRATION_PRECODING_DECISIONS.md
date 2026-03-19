# Hierarchical Phased Orchestration — Pre-Coding Decision Lock

**Status:** Final. All decisions locked. Implementation must conform.  
**Scope:** Stage 1 and Stage 2 only.  
**Date:** 2026-03-20  
**Source:** `HIERARCHICAL_PHASED_ORCHESTRATION_TASK_BREAKDOWN.md`

---

## 1. Locked Decisions

**Schema representation**  
Use `typing.TypedDict` for `ParentPlan`, `PhasePlan`, `PhaseResult`, `PhaseValidationContract`, and `PhaseRetryPolicy`. Runtime instances are plain `dict`s that satisfy the TypedDict shape. There is no constructor enforcement. Runtime validation is performed by `validate_parent_plan_schema()`.

**Phase 1 planner input**  
`plan()` receives `phase_1_subgoal`, the second return value of `_derive_phase_subgoals(parent_instruction)`. The full parent instruction string lives only on `ParentPlan["instruction"]`. It is never passed to `plan()` for Phase 1.

**Phase-state `AgentState.instruction` and context**  
For every phase, `AgentState.instruction` is set to `PhasePlan["subgoal"]`. `state.context["instruction"]` is set to the same value. `state.context["parent_instruction"]` is set to `ParentPlan["instruction"]` on every phase's state. `execution_loop` receives `phase_plan["subgoal"]` as its `instruction` argument, not the parent string.

**`PhaseResult["success"]` derivation**  
`success` equals `goal_met` — the `bool` returned by `GoalEvaluator.evaluate_with_reason(..., phase_subgoal=phase_plan["subgoal"])` for that phase. No other signal overrides this.

**Partial failure return shape from `run_hierarchical`**  
When the parent policy is STOP after any phase, `run_hierarchical` returns `(last_executed_phase_state, aggregated_loop_output)` with no exception. `aggregated_loop_output["errors_encountered"]` contains `"phase_<index>_goal_not_met"` or `"phase_<index>_failed:<failure_class>"`. `aggregated_loop_output["phase_results"]` lists only the phases that ran.

**Compatibility-mode double `get_plan` call in Stage 1**  
Stage 1 calls `get_plan` twice for every `run_hierarchical` call in compat mode: once inside `get_parent_plan`, once inside `run_deterministic`. No caching. No skipping `get_parent_plan`. The PR description documents this explicitly.

**`PhaseValidationContract` behavior in Stage 2**  
Every `PhasePlan["validation"]` is populated at plan-construction time. Phase 0: `require_ranked_context=True`, `require_explain_success=True`, `min_candidates=1`. Phase 1: `require_ranked_context=True`, `require_explain_success=is_explain_like_instruction(phase_1_subgoal)`, `min_candidates=1`. These fields are metadata for trace and future enforcement. Stage 2 does not add a runtime gate that fails Phase 0 because `ranked_context` is empty after a successful docs-lane EXPLAIN. Phase 0 success is determined entirely by `GoalEvaluator`.

**Fallback when two-phase build fails**  
If `_is_two_phase_docs_code_intent` returns `True` but `_build_two_phase_parent_plan` raises, or Phase 0's or Phase 1's `steps` fail `validate_plan({"steps": steps})`, or `plan(phase_1_subgoal)` returns steps that fail validation: `get_parent_plan` falls through to the compat path (`get_plan` + `make_compatibility_parent_plan`). A `two_phase_fallback` trace event is emitted with a short reason string. The caller receives `compatibility_mode=True` and `len(phases)==1`. No exception propagates.

**Allowed phase counts in Stage 2**  
`len(phases)` is `1` when `compatibility_mode` is `True`. `len(phases)` is `2` when `compatibility_mode` is `False`. Any other count causes `run_hierarchical` to raise `NotImplementedError`.

**`loop_output["plan_result"]` for multi-phase**  
Set to `last_executed_phase_state.current_plan` — the same value `execution_loop` stores on `state.current_plan` at completion. After a STOP following Phase 0, that is Phase 0's plan. After both phases complete, that is Phase 1's plan. The compat path returns `run_deterministic`'s `loop_output["plan_result"]` unchanged.

---

## 2. Rejected Alternatives

| Alternative | Decision |
|---|---|
| Full parent instruction as Phase 1 planner input | Rejected |
| `@dataclass` or frozen dataclass for schema types | Rejected |
| Relaxing or bypassing `validate_plan` for a phase | Rejected |
| Modifying `execution_loop` internals | Rejected |
| Modifying `replanner` internals | Rejected |
| Modifying `step_dispatcher` lane enforcement | Rejected |
| Clarification-first or "please split your request" as primary mixed-intent path | Rejected |
| Three or more phases in Stage 2 | Rejected |
| Parallel phase execution | Rejected |
| Caching the flat plan between `get_parent_plan` and `run_deterministic` in Stage 1 | Rejected |
| Skipping `get_parent_plan` in the compat path to avoid the double call | Rejected |

---

## 3. Final Interface Contracts

### `ParentPlan` — TypedDict

```
parent_plan_id: str           # "pplan_" + 8 lowercase hex chars
instruction: str              # original user text; never a phase subgoal
decomposition_type: str       # "compatibility" | "two_phase_docs_code"
phases: list[PhasePlan]       # non-empty, ordered, len==1 iff compat, len==2 iff two-phase
compatibility_mode: bool      # True iff single-phase wrapping get_plan output
```

### `PhasePlan` — TypedDict

```
phase_id: str                 # "phase_" + 8 lowercase hex chars
phase_index: int              # 0-based
subgoal: str                  # non-empty; used for AgentState.instruction, execution_loop
                              # instruction arg, and plan() input for Phase 1
lane: str                     # "docs" | "code"
steps: list[dict]             # flat plan step shape; same as today's plan["steps"]
plan_id: str                  # from _ensure_plan_id / planner output for this phase
validation: PhaseValidationContract
retry_policy: PhaseRetryPolicy
```

### `PhaseValidationContract` — TypedDict

```
require_ranked_context: bool
require_explain_success: bool
min_candidates: int
```

### `PhaseRetryPolicy` — TypedDict

```
max_parent_retries: int       # always 0 in Stages 1–2
```

### `PhaseResult` — TypedDict

```
phase_id: str
phase_index: int
success: bool                 # equals goal_met
failure_class: str | None     # None on success; derived from state signals on failure
goal_met: bool
goal_reason: str
completed_steps: int
context_output: dict          # keys: ranked_context, retrieved_symbols, retrieved_files,
                              #       files_modified, patches_applied (each a list)
attempt_count: int            # 1 in Stage 2
loop_output: dict             # execution_loop's loop_output for this phase; {} if None
```

### `get_parent_plan`

```python
def get_parent_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn: Callable | None = None,
    retry_context: dict | None = None,
) -> ParentPlan:
```

Parameters match `get_plan` exactly. Returns `ParentPlan`, not `dict`. Does not raise beyond what `get_plan` raises. Emits trace event `parent_plan_created` when `log_event_fn` and `trace_id` are provided.

### `run_hierarchical`

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
```

Signature is identical to `run_deterministic`. The returned `dict` contains at minimum: `completed_steps` (int), `patches_applied` (int), `files_modified` (list), `errors_encountered` (list), `tool_calls` (int), `plan_result` (per §1 lock), `start_time` (float), `phase_results` (list of `PhaseResult`). In compat mode the returned `dict` is exactly `run_deterministic`'s `loop_output` for the same inputs, with `phase_results` added.

### `_derive_phase_subgoals`

```python
def _derive_phase_subgoals(instruction: str) -> tuple[str, str]:
```

**Phase 0 subgoal:** `"Find documentation artifacts relevant to: " + instruction.strip()[:150]`

**Phase 1 subgoal:** Build `lower = instruction.lower()`. Iterate these substrings in order: `" and explain "`, `" and describe "`, `" and show how "`, `" and summarize "`, `" and walk through "`. For the first match at position `pos` in `lower`, let `start = pos + len(matched_substring)`. Let `raw = instruction[start:].strip()`. If `len(raw) >= 10`, Phase 1 subgoal is `raw[0].upper() + raw[1:]`. Otherwise Phase 1 subgoal is `instruction.strip()`. If no substring matches, Phase 1 subgoal is `instruction.strip()`.

### Phase context handoff keys

Injected into Phase 1's initial `AgentState.context` only. Not injected into Phase 0.

```
prior_phase_ranked_context: list
prior_phase_retrieved_symbols: list
prior_phase_files: list
```

Pruning: if the total serialized character estimate of `prior_phase_ranked_context` exceeds `MAX_CONTEXT_CHARS // 2` (from `config.agent_config`), truncate the list to its leading items until under budget. No reranking.

### Parent goal aggregation

```python
def _aggregate_parent_goal(phase_results: list) -> tuple[bool, str]:
```

Returns `(True, "all_phases_succeeded")` iff every entry has `goal_met == True`. Otherwise returns `(False, "phase_<i>_failed")` where `<i>` is the index of the first entry with `goal_met == False`.

---

## 4. Testing Implications

**TypedDict and runtime validation**  
TypedDict provides static type hints only. Tests do not assert constructor-level exceptions. Tests assert that `validate_parent_plan_schema` returns `False` for inputs with empty `phases`, an invalid `lane` value, or a missing required key.

**Compatibility-mode identity assertions**  
`tests/test_run_hierarchical_compatibility.py` asserts strict equality between `run_deterministic` and `run_hierarchical` outputs on the same mocked inputs for: `loop_output["completed_steps"]`, `loop_output["files_modified"]`, `loop_output["patches_applied"]`, `loop_output["errors_encountered"]`, `state.instruction`, and `state.step_results`. Differences in trace events (`parent_plan_created`, `run_hierarchical_start`) are not asserted and not a failure.

**Two-phase fallback test**  
When `plan()` is patched to return steps that fail `validate_plan`, `get_parent_plan` returns a `ParentPlan` with `compatibility_mode=True` and `len(phases)==1`. The test passes a `log_event_fn` mock and asserts it was called with event name `"two_phase_fallback"`.

**`GoalEvaluator.evaluate_with_reason` backward compat**  
Existing tests call `evaluate_with_reason(instruction, state)` without `phase_subgoal`. For every such call, the return tuple `(bool, str, dict)` must be bit-identical to the pre-change return for the same inputs. New tests call `evaluate_with_reason(instruction, state, phase_subgoal="Explain replanner flow")` and assert that `is_explain_like_instruction` was evaluated against `"Explain replanner flow"`, not against `instruction`.

---

## 5. PR Sequencing

| PR | Scope | Files changed | Gate |
|---|---|---|---|
| **PR1** | Stage 1 schemas | `agent/orchestrator/parent_plan.py` (new), `tests/test_parent_plan_schema.py` (new) | Schema tests green; no other file touched |
| **PR2** | Stage 1 wrappers | `agent/orchestrator/plan_resolver.py` (`get_parent_plan` added), `agent/orchestrator/deterministic_runner.py` (`run_hierarchical` compat stub + `NotImplementedError`), `tests/test_run_hierarchical_compatibility.py` (new) | Full `pytest tests/` green; compat identity tests green |
| **PR3** | Stage 2 detection + subgoals | `agent/orchestrator/plan_resolver.py` (`_is_two_phase_docs_code_intent`, `_derive_phase_subgoals` added), `tests/test_two_phase_execution.py` (new; detection + subgoal classes only) | Detection and subgoal tests green; all prior tests still pass |
| **PR4** | Stage 2 plan construction + evaluator | `agent/orchestrator/plan_resolver.py` (`_build_two_phase_parent_plan`, branch in `get_parent_plan`), `agent/orchestrator/goal_evaluator.py` (`phase_subgoal` kwarg on `evaluate_with_reason`), `tests/test_two_phase_execution.py` (plan build, fallback, goal evaluator classes) | Plan-build and evaluator tests green; existing goal evaluator tests pass unchanged |
| **PR5** | Stage 2 phase loop | `agent/orchestrator/deterministic_runner.py` (phase loop replaces `NotImplementedError`; private helpers added), `tests/test_two_phase_execution.py` (integration + mocked-loop classes) | Full `pytest tests/` green; tag `stage2-complete` |

Each PR merges only after CI passes. Rollback: PR1 and PR2 are reverted by deleting `parent_plan.py`, deleting the two new test files, and reverting the added functions in `plan_resolver.py` and `deterministic_runner.py`. PR3–PR5 are reverted in reverse order by reverting the specific hunks in those files.

---

*End of decision lock.*
