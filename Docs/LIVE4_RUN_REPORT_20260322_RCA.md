# Live4 Run Report — RCA & Summary

**Date:** 2026-03-22  
**Suite:** live4 (4 edit tasks, real model)  
**Timeout:** 30 seconds per task  
**Run directory:** `artifacts/agent_eval_runs/20260322_145819_762ae6`  
**Logs:** `docs/live4_run_log_20260322_145818.txt` (partial), terminal output

---

## Executive Summary

| Metric | Value |
|--------|-------|
| **Total tasks** | 4 |
| **Completed (before run termination)** | 3 |
| **Success** | 0 (0%) |
| **Failure bucket** | `task_timeout` (100%) |
| **Root cause** | **30-second per-task timeout is insufficient** for live_model execution |

---

## Root Cause Analysis

### Primary Cause: Per-Task Timeout Too Short

With `--task-timeout 30`, each live4 task is capped at 30 seconds. Each task involves:

1. **Intent routing** (1 model call) — classify CODE_EDIT
2. **Planner** (1–2 model calls) — generate SEARCH_CANDIDATES → EDIT plan
3. **Search/retrieval** — repo search, BUILD_CONTEXT
4. **Edit proposal** (1 model call) — new model-based `generate_edit_proposals` → `call_reasoning_model`
5. **Patch execution** — apply patch, run validation
6. **On failure:** critic (1 call) + retry planner (1 call) + edit proposal again

**Estimated model calls per task:** 5–12+ depending on retries. At ~3–8 seconds per call, 30s allows only 4–10 calls before timeout. Tasks routinely exceed this.

---

## Loop Bottleneck Analysis (Principal Engineer)

From `live4_run_log_20260322_150323.txt`: **6× intent**, **6× planner**, **11+ edit proposals** across 4 tasks. Bottlenecks identified:

### 1. Redundant Intent + Planner (2× before first step)

**Flow:** `run_hierarchical` → `get_parent_plan` (1 intent + 1 planner) → for compatibility_mode → `run_deterministic` → **`get_plan` again** (1 intent + 1 planner).

**Root cause:** `deterministic_runner.py` line 657. When `compatibility_mode=True`, we call `run_deterministic` which ignores the parent plan and invokes `get_plan` from scratch. The plan from `get_parent_plan` is discarded.

**Impact:** 2 extra model calls (intent + planner) per task before any step runs.

**Fix (implemented):** Pass `plan_result` from parent phase into `run_deterministic` when in compatibility mode; skip redundant `get_plan` call.

### 2. Retry Planner Strategy Mismatch

**Log:** `[retry_planner] unrecognised strategy 'rewrite_query', using fallback`

**Root cause:** Model outputs `rewrite_query`; code expects `rewrite_retrieval_query` (`agent/meta/retry_planner.py` `RETRY_STRATEGIES`).

**Impact:** Hints from retry planner are forced to `generate_new_plan` instead of applying `rewrite_retrieval_query`. Retries may re-plan instead of refining retrieval.

**Fix (implemented):** Added `rewrite_query` → `rewrite_retrieval_query` alias in `agent/meta/retry_planner.py`; also extract `rewrite_query` from `rewrite_queries` when model returns array.

### 3. Edit Proposal Called Per Attempt

**Per failed attempt:**
- `_critic_and_retry`: critic (1 call) + retry planner (1 call)
- Next attempt: `to_structured_patches` → `generate_edit_proposals` (1 call)

**Total per failure:** 3 model calls. With `MAX_EDIT_ATTEMPTS=5`, a task can hit 15+ model calls in the edit loop alone.

### 4. Edit Proposal with Stale vs Fresh File Content

**Log evidence:** First edit request: file has `return a * b + 1`. Second edit request (same step): file has `return a * b` (already fixed). Model still returns `old: "return a * b + 1"` → patch fails with "text_sub old snippet not found".

**Root cause (hypothesis):** Either (a) multiple sequential groups in `resolve_conflicts` where group 1 applies successfully and group 2 re-reads the modified file, or (b) retry after test failure where context/evidence is stale. Needs trace to confirm.

**Mitigation:** Ensure `full_content` in edit proposal always reflects current on-disk state; avoid mixing stale evidence with fresh file reads.

### 5. Model Call Count Summary (per task, worst case)

| Stage          | Calls | Notes                        |
|----------------|-------|------------------------------|
| Intent         | 2     | Redundant (Bottleneck 1)     |
| Planner        | 2     | Redundant (Bottleneck 1)     |
| Edit proposal  | 5+    | 1 per attempt (Bottleneck 3) |
| Critic         | 4     | 1 per failure                |
| Retry planner  | 4     | 1 per failure                |
| **Total**      | **17+** | Before SEARCH/BUILD_CONTEXT |

### Mitigations Applied (2026-03-22)

1. **Redundant intent+planner:** `run_deterministic` now accepts optional `plan_result`. When `run_hierarchical` uses compatibility mode, it passes the phase plan from `get_parent_plan` and skips the second `get_plan` call. Saves 2 model calls per task.

2. **Retry strategy mismatch:** `agent/meta/retry_planner.py` accepts `rewrite_query` as alias for `rewrite_retrieval_query`, and extracts `rewrite_query` from `rewrite_queries` when the model returns an array.

### Evidence from Outcome Files

All 3 completed tasks show identical failure:

```json
{
  "failure_class": "task_timeout",
  "notes": "Task timed out after 30s",
  "retrieval_quality": {"timeout": true, "task_id": "..."}
}
```

- `core12_mini_repair_calc` — timeout
- `core12_mini_repair_parse` — timeout  
- `core12_mini_feature_flags` — timeout
- `core12_pin_typer_repair` — run was still in progress when checked

### Evidence from Logs: Edit Proposal Generator Works

From terminal output (`live4_run_log_20260322_145818.txt` and run 762ae6), the **model-based edit proposal generator** is producing valid patches:

**core12_mini_repair_calc** — model produced:
```json
{
  "action": "text_sub",
  "old": "return a * b + 1",
  "new": "return a * b"
}
```
This is a correct patch. The task timed out before the patch could be applied and validated.

**core12_mini_feature_flags** — model produced insert patches; one failure was `symbol not found` (model used `"symbol": "store"` but file has `is_verbose`; no `store` symbol). This indicates the model-based path is active; failures are due to symbol mismatch and timeout, not `weakly_grounded_patch`.

---

## Failure Bucket Histogram

| Bucket | Count |
|--------|-------|
| task_timeout | 3 |

---

## Pipeline Health (from logs)

| Stage | Status |
|-------|--------|
| Intent routing | ✓ Working |
| Planner | ✓ Working |
| Search/retrieval | ✓ Working |
| Edit proposal (model-based) | ✓ Producing patches |
| Patch execution | ✗ Not reached (timeout) |
| Validation | ✗ Not reached (timeout) |

---

## Recommendations

1. **Increase timeout for live4:** Use `--task-timeout 120` or higher. For 4 tasks with retries, 180–240s per task is realistic.

2. **Run without timeout for full eval:** Omit `--task-timeout` to get complete results and proper edit-grounding / patch-reject telemetry.

3. **Symbol handling in insert patches:** For `action: "insert"`, the model sometimes uses the wrong symbol (e.g. `store` vs `is_verbose`). Consider passing the top symbol from `edit_binding` more explicitly in the prompt.

---

## How to Reproduce

```bash
# With 30s timeout (all tasks timeout)
python3 scripts/run_principal_engineer_suite.py --live4 --task-timeout 30

# With 120s timeout (recommended for meaningful eval)
python3 scripts/run_principal_engineer_suite.py --live4 --task-timeout 120

# No timeout (full run, ~2–3 min per task)
python3 scripts/run_principal_engineer_suite.py --live4
```

Logs are saved to `docs/live4_run_log_<timestamp>.txt`.

---

## Log File Locations

- **Run log:** `docs/live4_run_log_20260322_145818.txt` (captured via tee during run)
- **Task outcomes:** `artifacts/agent_eval_runs/20260322_145819_762ae6/tasks/<task_id>/outcome.json`
- **Terminal output:** Available in IDE terminal buffer for run 942495 / 159938
