# Stage 3 closeout report

## Scope completed

Hierarchical phased orchestration **Stage 3** is closed out: runtime phase validation enforcement, observability, defensive behavior, parent-retry **reporting**, and phase-validation metadata consolidation—all **without** implementing real parent retries or Stage 4 execution.

## Production code touched in Stage 3

- `agent/orchestrator/deterministic_runner.py` — single orchestration surface for hierarchical runs (validation, metadata, aggregation, trace payloads, compat delegation).

## Tests used as proof

- `tests/test_parent_plan_schema.py`
- `tests/test_run_hierarchical_compatibility.py`
- `tests/test_two_phase_execution.py`

## Proof command

```bash
python3 -m pytest tests/test_parent_plan_schema.py tests/test_run_hierarchical_compatibility.py tests/test_two_phase_execution.py -q
```

Run this after checkout to confirm the tree matches the recorded proof count below.

## Proof result (recorded at Stage 3 closeout)

**141 passed** (full proof command above). Immediately before the final closeout slice (documentation + lock tests only), the same command reported **133 passed**.

## Terminology: `phase_count` (hierarchical `loop_output`)

In the **tested hierarchical contract**, `loop_output["phase_count"]` (and the `phase_count` field inside the top-level `phase_validation` summary object) reflects **executed** phases — i.e. `len(phase_results)` — not the parent plan’s planned phase total when the run stops early.

Other surfaces (e.g. trace payloads such as `run_hierarchical_start`) may carry a **planned** phase count from the parent plan; that is separate from `loop_output["phase_count"]`. Do not assume `loop_output["phase_count"]` always equals the number of phases in the parent plan.

## Compatibility path (unchanged)

When `compatibility_mode` is true, `run_hierarchical` **only** delegates to `run_deterministic` and returns its `(AgentState, loop_output)` tuple. The returned `loop_output` dict must **not** gain hierarchical-only keys (`phase_results`, `phase_validation`, `parent_retry`, `parent_plan_id`, `phase_count`, parent goal fields, parent retry scalars, etc.). Stage 3 lock tests assert this.

## Stage 3 capabilities delivered

- Runtime **phase validation** enforcement (`PhaseValidationContract`-style checks in the orchestrator path).
- **Phase-level and parent-level observability** (trace events and structured outputs).
- **Defensive** aggregation and context handoff behavior for hierarchical runs.
- **Parent retry** metadata and eligibility signaling (**reporting only**; `parent_retry` object + scalars — no retry execution).
- **Phase validation** metadata and summary (`phase_validation` per phase + top-level summary).

## Explicit non-goals (Stage 3)

- No actual parent retries or replans driven by retry policy.
- No Stage 4 retry execution or planner/orchestrator contract expansion beyond reporting.
- No change to compatibility-path behavior: compat mode remains pure delegation to `run_deterministic` with no hierarchical metadata on the returned `loop_output`.

## Next work (branch recommendation)

Continue Stage 4 from branch **`next/stage3-from-stage2-v1`** (or the current line that contains this Stage 3 work) unless a release process requires a differently named integration branch.
