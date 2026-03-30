# Phase 10 — Hardening + production readiness

**Scope:** This document is the authoritative Phase 10 specification. It is the **final phase of the core control-plane rollout** (Phases 1–10): **no new control-plane features**, **no new architecture** in this layer — only **enforcement, guardrails, cleanup, and quality gates**. **Phases 11–12** (Langfuse, execution graph/UI) are **separate product-layer** phases; see **`PHASED_IMPLEMENTATION_PLAN.md`**. This file is not executable.

---

## Objective (non-negotiable)

Make the system:

```text
predictable
safe
observable
maintainable
extensible
```

---

## What this phase is not

```text
❌ No new architecture
❌ No new features
❌ No broad refactors unless required for safety or correctness
```

---

## What this phase is

```text
✅ Enforce contracts at boundaries
✅ Eliminate silent edge-case failures
✅ Lock behavior under load and failure
```

---

## Step 1 — Contract enforcement (critical)

**Add validation at boundaries** (assertions in dev; in production prefer **raise ValidationError** / typed errors so behavior is configurable).

| Location | Check |
|----------|--------|
| **Dispatcher** | `isinstance(result, ExecutionResult)` |
| **PlanExecutor** | `isinstance(step, PlanStep)` |
| **Before execution** | `step.action` present / valid literal |
| **After tool → execution mapping** | `result.output.summary` present (per **ExecutionResult** schema) |
| **Planner output** | `isinstance(plan, PlanDocument)` |

**Why:** Silent contract breaks → the hardest bugs.

**Implementation note:** If **`output`** is a nested Pydantic model, validate **`result.output.summary`** via attributes, not `result.output["summary"]`, unless the model supports dict coercion.

---

## Step 2 — Fail fast (remove silent fallbacks)

**Audit:**

- `try/except` that **swallows** exceptions without logging / re-raise
- **Default dict** patterns that hide missing structure: `.get(..., {})` everywhere

**Prefer:**

- Explicit validation or **raise** with context
- Logged failures at **ERROR** level

**Example:**

| Bad | Good |
|-----|------|
| `data = result.get("data", {})` | Validate schema or required keys; fail if missing |

---

## Step 3 — Strict enum validation

Ensure **literals** / **Enums** are enforced for:

- `action` (plan / execution)
- `failure_type` / error `type`
- `step.type` (**PlanStep**)
- `status` fields (**execution**, **trace**, etc.)

**Reject** unknown values at parse boundaries (Pydantic, **Literal**, or **Enum**).

---

## Step 4 — Remove dead paths (critical)

**Audit:**

```bash
grep -R "next_action" .
grep -R "react_loop" .
grep -R "execution_loop" .
```

**Ensure:**

```text
❌ No legacy dynamic loop as primary execution path
❌ No hidden legacy dispatch that bypasses PlanExecutor / dispatcher contracts
```

Align with **Phase 8** (no **`AgentLoop.run`** on ACT).

---

## Step 5 — Lock tool interface

**In tool layer:**

- `assert isinstance(tool_result, ToolResult)` (or equivalent validation) at handler exit
- **All** handlers return **`ToolResult`** — **no** raw dicts

---

## Step 6 — Plan validation hardening

**Do not** duplicate planner validation logic here. **Add** cross-field rules to **`agent_v2/validation/plan_validator.py`** (see **`VALIDATION_REGISTRY.md`**) so **one module** enforces:

| Rule | |
|------|--|
| **Unique** `step_id` | |
| **No circular** dependencies | |
| **Dependencies** refer only to prior steps (indices / ordering) | |
| **`len(steps) <= 8`** (or configured max) | |
| **`finish` exists** and is **last** (if product requires) | |

**Phase 10** wires stricter checks into the **same** `PlanValidator` (or imports it and calls it) — **no** second validator class with overlapping rules.

**Note:** “Finish last” is a **product** rule — if frozen **SCHEMAS.md** allows otherwise, **document** the chosen rule in **`PlanValidator`** only.

---

## Step 7 — State consistency checks

**Invariants** (examples — adjust to **`AgentState`** fields):

1. `len(history) == len(step_results)` (if both track per-step observations)
2. `step.execution.attempts <= step.execution.max_attempts`
3. `replan_attempt <= max_replans` (**policy**)
4. **`current_plan`** present when executing plan steps

---

## Step 8 — Timeout + safety guards

**Use** **`ExecutionPolicy`** / **`FailurePolicy`** (**PHASE_1**) + config:

| Guard | Example |
|-------|---------|
| **Global step cap** | `max_steps` (e.g. 20) — abort if exceeded |
| **Wall-clock** | `max_runtime_seconds` (e.g. 60) — abort in `PlanExecutor` loop |

**Enforce in** **`PlanExecutor`** (or outer runtime):

- `if steps_executed > max_steps: abort`
- `if elapsed > max_runtime_seconds: abort`

---

## Step 9 — Trace completeness (**Phase 9**)

**Ensure** **`Trace`** includes:

- All executed steps (including **failed** steps, per policy)
- **Final** `status`
- **Total** duration
- **`plan_id`**

**Optional assertion** (dev): `len(trace.steps) > 0` for non-empty runs — or allow empty trace when plan is empty (document).

---

## Step 10 — CLI output stability

**Stable shape** for CLI / API consumers:

```python
{
    "status": "...",
    "trace": ...,
    "result": ...,  # or final_output / execution_summary per schema
}
```

**Avoid:** Mixed return types (sometimes `dict`, sometimes raw object) without a **single** adapter in **`runtime.run`**.

---

## Step 11 — Test suite (minimum)

| # | Scenario |
|---|----------|
| 1 | **Happy path:** explore → plan → execute → success |
| 2 | **Retry:** fail → retry → success |
| 3 | **Replan:** fail → retries exhausted → replan → success |
| 4 | **Replan limit:** fail → replan → fail → abort at limit |
| 5 | **Invalid plan:** planner / parser returns invalid schema → **reject** with clear error |

---

## Step 12 — Logging (not prints)

**Replace** stray **`print`** with **`logging`**:

- **INFO** — step milestones
- **WARNING** — retries, policy soft limits
- **ERROR** — failures, aborts

**Do not** mix **structured trace** (**TRACE** / **Phase 9**) with **log lines** for the same semantic event — trace is for **replay**; logs are for **operators**.

---

## Step 13 — Clean boundaries (critical)

| Layer | May emit |
|-------|----------|
| **Tools** | **`ToolResult`** only |
| **Dispatcher** | **`ExecutionResult`** only |
| **PlanExecutor** | Updates **`PlanStep`** / **`AgentState`**; **no** raw tool dict mutation |
| **Planner** | **`PlanDocument`** only — **no** `ExecutionResult`, **no** tools |

**Leaks to avoid:**

- Planner importing execution results
- Tools aware of **`PlanStep`**
- Executor rewriting tool payloads instead of **`ExecutionResult`**

---

## Step 14 — Config centralization

**Create** `agent_v2/config.py` (or merge with existing project config if AutoStudio already has one — **avoid duplicate sources of truth**).

**Examples:**

```python
MAX_STEPS = 20
MAX_RETRIES = 2
MAX_REPLANS = 2
EXPLORATION_STEPS = 5
```

Wire **`ExecutionPolicy`**, **`PlanExecutor`**, **`ExplorationRunner`**, and **`Replanner`** from **one** place (env overrides optional).

---

## Step 15 — Final smoke test

```bash
python -m agent_v2 "Explain AgentLoop"
python -m agent_v2 --mode=plan "Explain AgentLoop"
python -m agent_v2 "Modify non-existent file"
```

**Expect:**

```text
✅ Correct flow
✅ Retries where designed
✅ Replans when applicable
✅ Clean trace
```

---

## Final red flags (system broken if present)

```text
LLM choosing actions outside argument-only executor
Random tool calls
Missing finish step when plan requires it
No trace on ACT path
Silent swallowed exceptions
```

---

## Final principal verdict

**End-to-end pipeline:**

```text
Exploration → Planning → Execution → Retry → Replan → Trace
```

With **contracts**, **observability**, and **guardrails**, this is a **production-oriented** general-purpose coding-agent **architecture** (implementation quality must still be proven in code review and tests).

---

## What you can do next (out of scope for Phase 10)

1. **UI** — Langfuse / graph visualization
2. **Multi-agent** — roles (Explorer / Planner / Executor)
3. **Memory / retrieval** — extend retrieval pipeline per frozen rules
4. **IDE** — VS Code plugin

**Optional follow-ups:**

- **Langfuse integration** (proper, not surface-level)
- **Multi-agent extension design**
- **VS Code plugin architecture**
- **Brutal audit of current code vs frozen architecture**

---

## Phase 10 exit criteria (strict)

```text
✅ Boundaries enforced (types + validation)
✅ No silent swallow of errors (policy-driven handling OK)
✅ Enums / literals enforced at edges
✅ Dead legacy paths removed or unreachable
✅ ToolResult / ExecutionResult / PlanDocument invariants
✅ Plan validation hardened
✅ State + policy invariants checked
✅ Timeouts / caps enforced
✅ Trace complete per Phase 9
✅ Stable CLI / runtime output shape
✅ Minimum test suite green
✅ Logging instead of print for operational noise
✅ Config centralized
```

---

**Phase 10 done** — when the above are **implemented** and **smoke tests pass**.
