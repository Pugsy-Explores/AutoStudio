# Agent v2 — schema alignment audit (canonical vs implementation)

Staff audit: compare **current code** to **locked canonical contracts**, classify gaps, and record **locked decisions** for enforcement and observability.

**Corrections (locked):** An earlier draft under-classified two gaps. **Edit validation at the planner** and **`tool_execution` logging** are **CRITICAL** (not merely “important”): without them, contract and observability fail in production.

**Companion:** `Docs/agent_v2_planner_tool_executor_mapping.md` (planner tool ↔ PlanStep ↔ ReAct).

---

## 1. Schema audit table (implementation facts)

| Component | Status | Issue |
|-----------|--------|--------|
| **PlannerEngineOutput** | Partial | Matches `decision`, `reason`, `query`, `step` + `metadata`. **`tool` includes `none`** for `stop`/`replan` (intentional). **`step` optional** until pairing. |
| **Tool field presence** | Match | Empty `tool` coerces to `"none"` (`plan.py`). |
| **step.metadata** | Match | Dict, default `{}`, normalized in Pydantic. |
| **Act tool inputs (planner)** | Fixed in code | **PlannerV2._validate_act_tool_inputs** enforces: `search_code`→query, `open_file`→path, `run_shell`→command, `edit`→path+instruction; `run_tests` optional. |
| **Act tool inputs (executor)** | Assumes planner | Executor does not duplicate planner field rules; returns structured `ExecutionResult` / errors without assuming invalid planner output. |
| **Session memory** | Match | `intent_anchor`, `last_user_instruction`, `last_decision`, `last_tool`, `explore_streak`, `recent_steps` (cap 5). Extras: `session_id`, `current_task`, `active_file`, `active_symbols`, `updated_at`. |
| **planner_telemetry** | Match | Structured JSON in `planner_v2.py`. |
| **tool_execution** | Match (post-fix) | Structured JSON in `PlanExecutor._execute_step` — see §6. |

---

## 2. Gap classification (LOCKED severities)

| Gap | Level |
|-----|--------|
| Missing **edit** path/instruction validation at planner | **CRITICAL** |
| Missing **`tool_execution`** application log | **CRITICAL** |
| `tool: "none"` vs prose canonical omitting it | **OPTIONAL** — **keep `none`** |
| `last_tool` vs `last_tool_used` | **OPTIONAL** — **no rename** |

**Why edit is CRITICAL:** Planner may emit `edit` without path → executor gets ambiguous args → unpredictable behavior. Contract must hold at **planner boundary**.

**Why tool_execution is CRITICAL:** Without a single grep-friendly structured line per dispatch, failures, latency, and routing bugs are opaque in production logs.

---

## 3. Enforcement decisions (LOCKED)

| Area | Decision |
|------|----------|
| **Planner** | **Enforce now** — all act-tool required fields in `PlannerV2._validate_act_tool_inputs` only. |
| **Executor** | **Assume valid plan** — no duplicate required-field validation; must not crash; structured errors from tools/dispatcher. |
| **tool: none** | **Do not change.** |
| **Memory naming** | **Do not change code**; document only if needed. |

---

## 4. Enforcement rules (what “enforced” means)

| Tool | Required at planner |
|------|---------------------|
| `search_code` | Non-empty query (`step.input` or `metadata.query`) |
| `open_file` | Non-empty path (`input` or `metadata.path`) |
| `run_shell` | Non-empty command (`input` or `metadata.command`) + plan-mode policy (`tool_policy.py`) |
| `edit` | Non-empty **path** and **instruction**: `metadata.path` + (`step.input` or `metadata.instruction`), *or* `step.input` as path when `metadata.instruction` is set (same rules in `_validate_act_tool_inputs` and `_step_spec_to_plan_step`) |
| `run_tests` | No required fields |

**Single owner:** `PlannerV2._validate_act_tool_inputs` (no second copy in `PlanValidator` for the same fields).

---

## 5. Implementation plan (executed)

1. **`agent_v2/planner/planner_v2.py`** — extend `_validate_act_tool_inputs` for `edit`; document `run_tests` as explicitly optional.
2. **`agent_v2/runtime/plan_executor.py`** — one **`tool_execution`** log per `_execute_step` call (includes retries = multiple logs per logical step — each dispatch attempt is observable).
3. **`agent_v2/runtime/phase1_tool_exposure.py`** — `PLAN_STEP_ACTION_TO_PLANNER_TOOL` for log `tool` field.
4. **Tests** — edit missing path/instruction fail; executor logs once per `_execute_step` with required keys.
5. **Docs** — `Docs/agent_v2_planner_tool_executor_mapping.md`.

---

## 6. Logging — `tool_execution` contract (LOCKED)

**Where:** `PlanExecutor._execute_step` only (no duplicate `tool_execution` in `agent_v2/runtime/dispatcher.py` for the same dispatch).

**Shape (required keys):**

```json
{
  "component": "tool_execution",
  "tool": "<planner_tool_id e.g. search_code>",
  "action": "<PlanStep.action e.g. search>",
  "step_id": "<string>",
  "success": true,
  "latency_ms": 123,
  "error": null,
  "input_summary": "<truncated JSON of merged args; uses same sanitization as Langfuse arg view>",
  "mode": "plan"
}
```

- **`input_summary`:** always present (may be `""` if args unavailable); capped (~512 chars) after `_tool_args_for_langfuse_input` per-field caps.  
- **`mode`:** optional; `plan` / `act` from planner `ToolPolicy` when `PlannerTaskRuntime` has stamped `state.metadata["tool_policy_mode"]`.

On failure: `success: false`, `error` string (exception message or `ExecutionError` message).

**Planner:** existing `planner_telemetry` unchanged.

---

## 7. Test plan

- Planner: `edit` without path → `PlanValidationError`
- Planner: `edit` without instruction → `PlanValidationError`
- Planner: `edit` with path in metadata + instruction in input → pass
- Executor: patch logger — exactly **one** `tool_execution` log per `_execute_step` invocation; payload contains `component`, `tool`, `action`, `success`, `latency_ms`, `error`

---

## 8. Clarifications vs earlier audit draft

- **Prioritization:** `edit` validation and `tool_execution` logging are **CRITICAL**, not “important only.”
- **Planner validates all act tools** listed above; executor does not re-validate required fields.
- **Tool naming:** PlanStep.action **`open_file`** is not the literal string `read`; legacy ReAct uses **`READ`** — see mapping doc.

---

## 9. Verification checklist (code vs this doc)

| Requirement | Status |
|-------------|--------|
| `_validate_act_tool_inputs`: all act tools (`search_code`, `open_file`, `run_shell`, `edit`, `run_tests`) | Done — `planner_v2.py` |
| `edit` path + instruction per §4 (including path-in-input when `metadata.instruction` set) | Done — validation + synthesis aligned |
| `tool_execution` log shape §6 in `_execute_step` (incl. `input_summary`, optional `mode`) | Done — `plan_executor.py` + `planner_task_runtime._sync_tool_policy_mode_to_state` |
| `PLAN_STEP_ACTION_TO_PLANNER_TOOL` for log `tool` field | Done — `phase1_tool_exposure.py` |
| No duplicate `tool_execution` in `dispatcher.py` | Verified — grep |
| Tests §7 | Done — `test_planner_v2.py`, `test_plan_executor.py`, `test_tool_policy.py` |
| Mapping doc | Done — `Docs/agent_v2_planner_tool_executor_mapping.md` |
| `tool: "none"` unchanged | Done — no code change |
| `last_tool` rename | N/A — explicitly not done |
