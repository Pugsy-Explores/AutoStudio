# Live4 Failure — Root Cause Analysis

**Date:** 2026-03-22  
**Scope:** All 4 live4 tasks failed with `validation_regression`  
**Execution mode:** `live_model`

---

## Executive Summary

All four live4 tasks (CODE_EDIT / repair / feature) failed because **no patches were applied**. The agent executed only SEARCH steps, never EDIT. Root cause is a **prompt–guardrail mismatch**: the planner prompt instructs actions (SEARCH_CANDIDATES, BUILD_CONTEXT) that the safety guardrail rejects. On rejection, the planner falls back to a single-step SEARCH plan, which cannot satisfy edit-heavy tasks.

---

## Failure Chain

```
Planner call → Guardrail rejects valid plan → plan() catches exception
→ Returns single-step SEARCH fallback → Execution runs SEARCH only
→ Goal evaluator: validation fails (no edits) → validation_regression
```

---

## RCA by Failure Mode

### 1. Safety Policy vs. Planner Action Vocabulary Mismatch

**Observed:** `[model_client] guardrail failed — retrying with stricter params: planner: Response violates safety policy`

**Root cause:** The planner prompt (`agent/prompt_versions/planner/v1.yaml`) instructs:

> Allowed actions are only: **SEARCH_CANDIDATES, BUILD_CONTEXT**, EDIT, SEARCH, EXPLAIN, INFRA.

The default `SafetyPolicy` (`agent/prompt_system/guardrails/safety_policy.py`) defines:

```python
allowed_tools = ["SEARCH", "READ", "EDIT", "EXPLAIN", "INFRA", "RUN_TEST"]
```

`SEARCH_CANDIDATES` and `BUILD_CONTEXT` are **not** in `allowed_tools`. When the model follows the prompt and outputs those actions, `check_safety()` rejects the response. The constraint checker has no prompt-specific policy; it uses the default for all prompts.

**Evidence from logs:**

- Task `core12_mini_repair_calc`: Planner output included `SEARCH_CANDIDATES` and `EDIT`. Guardrail failed with "Response violates safety policy".
- Task `core12_mini_repair_parse`: Same pattern with `SEARCH_CANDIDATES`, `BUILD_CONTEXT`, `EDIT`.
- Task `core12_mini_feature_flags`: Same pattern.
- Task `core12_pin_typer_repair`: Additional JSON malformation (see §2).

---

### 2. JSON Parsing Failures (Truncation / Malformed Output)

**Observed:** `[model_client] guardrail failed — retrying with stricter params: planner: Response does not contain valid JSON`

**Root cause:** Some model outputs were incomplete or malformed:

- Missing closing `}` (e.g. response ends with `]` instead of `]}`).
- Possible streaming truncation or model cutoff.

`validate_output_schema` in `output_schema_guard.py` uses brace-matching to extract JSON. Incomplete JSON returns `None`, triggering "Response does not contain valid JSON".

**Evidence:** Task `core12_pin_typer_repair` showed this error repeatedly; the response body ended with `]` without a closing `}`.

---

### 3. Planner Fallback Is Insufficient for Edit Tasks

**Root cause:** When the planner raises any exception (including `GuardrailError`), `plan()` in `planner/planner.py` catches it and returns `_build_controlled_fallback_plan()`. For CODE_EDIT tasks (no docs lane):

```python
return {
    "steps": [
        {"id": 1, "action": "SEARCH", "description": "...", "query": query, "reason": reason}
    ],
    "error": error,
}
```

The fallback is **SEARCH-only**. There is no EDIT step. Execution therefore:

1. Runs SEARCH successfully.
2. Never reaches EDIT.
3. Leaves the codebase unchanged.
4. Validation fails because tests/assertions expect edits.

**Design implication:** The fallback is tuned for retrieval-heavy flows. For edit-heavy tasks, a SEARCH-only fallback cannot meet the goal.

---

### 4. No Recovery Path After Fallback

**Root cause:** Once the planner returns the fallback, the execution loop runs it as-is. There is no:

- Retry with a relaxed guardrail.
- Attempt to parse the raw (rejected) plan and repair it.
- Intent-aware fallback (e.g. CODE_EDIT → SEARCH + EDIT heuristic).

The system treats the fallback as final and does not attempt to recover toward an EDIT-capable plan.

---

## Recommendations (Generalistic)

### R1. Align Guardrail Policy with Prompt Action Vocabulary

**Approach:** Derive or configure `allowed_tools` per prompt instead of a single global default.

**Options:**

1. **Per-prompt safety policy:** Add `safety_policy` (or `allowed_tools`) to `PromptTemplate` / prompt YAML. For the planner, set:
   ```yaml
   allowed_tools: [SEARCH_CANDIDATES, BUILD_CONTEXT, EDIT, SEARCH, EXPLAIN, INFRA]
   ```
2. **Single source of truth:** Define the canonical action vocabulary in one place; both the planner prompt and the guardrail use it. Avoid listing actions in the prompt without updating the guardrail.
3. **Schema-driven:** If `output_schema` defines step actions, the guardrail could derive allowed actions from that schema.

**Principle:** Guardrails should validate against the same action space the prompt instructs. Mismatches cause correct model outputs to be rejected.

---

### R2. Resilient JSON Extraction for Structured Outputs

**Approach:** Make JSON extraction more tolerant of common model output issues.

**Options:**

1. **Repair common errors:** Before failing, try to fix trailing `]` without `}` or minor structural issues (e.g. add missing `}`).
2. **Partial extraction:** If the full object is invalid, try to extract and validate individual steps from an incomplete structure where possible.
3. **Stricter streaming handling:** Ensure streaming responses are fully assembled before validation; avoid validating mid-stream chunks.
4. **Log raw response on failure:** Log the full raw response when JSON extraction fails to diagnose truncation vs. malformation.

**Principle:** Transient model issues (truncation, minor formatting) should not force a complete fallback when the content is salvageable.

---

### R3. Intent-Aware Planner Fallback

**Approach:** Make the planner fallback reflect the routed intent, not just a generic SEARCH step.

**Options:**

1. **CODE_EDIT fallback shape:** For `INTENT_EDIT`, use a minimal locate-then-edit plan, e.g.:
   ```python
   [SEARCH, EDIT]  # or SEARCH_CANDIDATES → BUILD_CONTEXT → EDIT
   ```
   instead of `[SEARCH]` only.
2. **Pass intent to `_build_controlled_fallback_plan`:** The planner or plan resolver could pass the routed intent so the fallback can choose an appropriate structure.
3. **Edit-heuristic fallback:** When the planner fails for an edit task, generate a default plan: SEARCH (instruction-derived query) → EDIT (instruction-derived description). The dispatcher already knows how to run these steps.

**Principle:** Fallbacks should preserve the minimum structure needed to satisfy the intent. For edit tasks, that includes at least one EDIT step.

---

### R4. Guardrail Failure Observability and Recovery

**Approach:** Improve handling when guardrails reject a response.

**Options:**

1. **Structured guardrail telemetry:** Log `(prompt_name, failure_reason, rejection_details)` to distinguish policy vs. schema vs. JSON issues.
2. **Recovery strategies:** Before falling back, consider:
   - Retrying with temperature=0 or a different model.
   - Relaxing the guardrail once (e.g. for planner, accept SEARCH_CANDIDATES/BUILD_CONTEXT).
   - Attempting to parse and repair the response.
3. **Configurable strictness:** Allow `ENABLE_PROMPT_GUARDRAILS` or per-prompt overrides for eval/debug without changing production defaults.

**Principle:** Guardrail rejections should be observable and, where safe, recoverable. Silent fallback to an insufficient plan hides the real failure.

---

### R5. Validation of Prompt–Guardrail Consistency

**Approach:** Prevent future mismatches with automated checks.

**Options:**

1. **CI check:** Extract allowed actions from planner (and similar) prompts and compare with `SafetyPolicy.allowed_tools` (or prompt-specific policy). Fail CI on mismatch.
2. **Single source of truth:** Define `PLANNER_ACTIONS` (or similar) in one module; prompt templates and guardrails import from it.
3. **Integration test:** Run a minimal planner call, assert the model can return a plan with SEARCH_CANDIDATES/BUILD_CONTEXT without guardrail failure.

**Principle:** Guardrail configuration should stay in sync with prompt instructions via automation, not manual discipline.

---

## Summary Table

| Root Cause                     | Affected | Fix Strategy                           | Priority |
|--------------------------------|----------|----------------------------------------|----------|
| Safety policy missing actions  | 4/4      | Per-prompt policy or shared vocabulary | P0       |
| JSON truncation/malformation   | 1/4      | Resilient extraction, logging          | P1       |
| Edit-free fallback             | 4/4      | Intent-aware fallback                  | P0       |
| No recovery after rejection    | 4/4      | Telemetry + recovery strategies        | P2       |
| Drift prevention               | Future   | CI / single source of truth            | P2       |

---

## References

- `agent/prompt_system/guardrails/safety_policy.py` — default `allowed_tools`
- `agent/prompt_system/guardrails/constraint_checker.py` — validation flow
- `agent/prompt_versions/planner/v1.yaml` — planner allowed actions
- `planner/planner.py` — `_build_controlled_fallback_plan()`, exception handling
- `agent/models/model_client.py` — guardrail retry, `GuardrailError`
- Live4 run: `artifacts/agent_eval_runs/20260322_032602_f1b418`
