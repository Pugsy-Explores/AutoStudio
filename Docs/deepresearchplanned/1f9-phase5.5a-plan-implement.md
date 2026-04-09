# --cursor prompt --
You are a principal engineer implementing Phase 5.5a: Episodic Memory Injection into the planner from @AutoStudio/Docs/deepresearchplanned/phase5-5-planner-memory-integration-audit-plan.md 

Follow the approved plan.
Keep implementation minimal and safe.

ONLY implement episodic failure injection.
Do NOT implement semantic or session enhancements.

---

## GOAL

Inject recent execution failures into planner prompt
to help avoid repeated mistakes.

---

## STEP 1 — FETCH FAILURES (RUNTIME)

File: agent_v2/runtime/planner_task_runtime.py

Before calling planner:

Add:

```python
from agent_v2.memory.episodic_query import EpisodicQuery
from agent_v2.config import get_agent_v2_episodic_log_dir
from pathlib import Path
```

Then:

```python
def _get_recent_failures() -> list[dict]:
    log_dir = get_agent_v2_episodic_log_dir()
    if not log_dir:
        return []
    q = EpisodicQuery(Path(log_dir))
    return q.query(success=False, limit=3)  # STRICT limit
```

---

## STEP 2 — ATTACH TO CONTEXT (SIMPLE, DIRECT)

Before calling planner:

```python
failures = _get_recent_failures()
planner_context.episodic_failures = failures
```

DO NOT:

* use metadata
* use model_extra
* modify schema

---

## STEP 3 — FORMAT BLOCK (PLANNER)

File: agent_v2/planner/planner_v2.py

Add:

```python
@staticmethod
def _format_episodic_failure_block(failures: list[dict]) -> str:
    if not failures:
        return ""

    lines = []
    for f in failures[:3]:
        tool = (f.get("tool") or "unknown")[:16]
        err = (f.get("error_type") or "error")[:32]
        ts = (f.get("timestamp") or "")[:10]
        lines.append(f"{tool}:{err}")

    recap = " ∙ ".join(lines)

    return (
        "\n--------------------------------\n"
        "RECENT FAILURES (advisory; avoid repeating):\n"
        f"{recap}\n"
        "If conflicts with exploration, trust exploration.\n"
    )
```

---

## STEP 4 — INJECT INTO PROMPT

Inside BOTH:

* `_compose_exploration_context_block`
* `_compose_replan_context_block`

After session_block:

```python
failures = getattr(planner_context, "episodic_failures", [])
episodic_block = self._format_episodic_failure_block(failures)
```

Inject:

```python
{session_block}
{episodic_block}
```

---

## STEP 5 — CONFIG FLAG (REQUIRED)

File: agent_v2/config.py

Add:

```python
def enable_episodic_injection() -> bool:
    return True  # or env-based
```

Guard in runtime:

```python
if enable_episodic_injection():
    planner_context.episodic_failures = _get_recent_failures()
```

---

## STEP 6 — VALIDATION

Write tests:

1. Formatting test

   * given failures → correct string

2. Integration test

   * mock EpisodicQuery
   * verify planner prompt contains failures

3. Disabled mode

   * config off → no injection

---

## RULES

* max 3 failures
* no token counting logic
* no schema changes
* no semantic memory
* no conversation memory

Keep it:
👉 simple, explicit, debuggable

---

## OUTPUT

Provide:

1. Files modified
2. Injection location
3. Example final prompt snippet

---

Focus:
👉 help planner avoid repeating failures, nothing more


# --implement --

## Summary

### 1. Files modified

| File | Change |
|------|--------|
| `agent_v2/config.py` | `enable_episodic_injection()` — env `AGENT_V2_ENABLE_EPISODIC_FAILURE_INJECTION` (default on). |
| `agent_v2/runtime/planner_task_runtime.py` | `_get_recent_failures()`, `attach_episodic_failures_if_enabled()` (uses `EpisodicQuery(..., limit=3)`). |
| `agent_v2/runtime/exploration_planning_input.py` | After `model_copy` for `session`, calls `attach_episodic_failures_if_enabled(ctx)` before `planner.plan`. |
| `agent_v2/runtime/replanner.py` | Builds `PlannerPlanContext`, calls `attach_episodic_failures_if_enabled(pc)`, then `planner.plan` (path that bypasses `call_planner_with_context`). |
| `agent_v2/planner/planner_v2.py` | `_format_episodic_failure_block()`; exploration + replan context blocks append episodic text **immediately after** the session block via `session_segment = session_block.strip() + episodic_block`. |
| `tests/test_episodic_planner_injection.py` | Formatting, integration (mocked `_get_recent_failures` + enabled flag), disabled mode. |

### 2. Injection location

- **Data load:** `attach_episodic_failures_if_enabled` runs in `call_planner_with_context` (after session `model_copy`) and in `Replanner.replan` before `planner.plan`.
- **Prompt text:** `PlannerV2._compose_exploration_context_block` and `_compose_replan_context_block` — after `session_block`, merged into `session_segment` so order is … exploration (and optional validation) … **session + RECENT FAILURES**.

`PlannerPlanContext` does not allow arbitrary attributes, so failures are stored with  
`object.__setattr__(planner_context, "episodic_failures", failures)` (same effect as assignment, no new Pydantic fields).

### 3. Example prompt snippet

After a session block (or with an empty session), the model can see:

```text

--------------------------------
RECENT FAILURES (advisory; avoid repeating):
open_file:tool_error ∙ search:timeout ∙ shell:policy_denied
If conflicts with exploration, trust exploration.

```

(Up to **3** failures; each line is `tool[:16]:error_type[:32]`.)

### Config

- **On (default):** injection runs when `get_agent_v2_episodic_log_dir()` is set and logs exist.  
- **Off:** `AGENT_V2_ENABLE_EPISODIC_FAILURE_INJECTION=0` (or `false` / `off`).

Tests: `49` planner tests + `6` episodic memory tests + new file — all passing.