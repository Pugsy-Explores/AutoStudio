# Phase 11 — Langfuse observability (first-class)

**Scope:** This document is the authoritative Phase 11 specification. It integrates **Langfuse** (or API-compatible observability) as a **first-class layer** aligned with **trace → plan step → LLM → events** — not flat logging. This file is not executable.

---

## Principle

> If Langfuse is wired wrong → noise.  
> If wired right → the debugging brain.

**No new product features** — **observability integration** on top of Phases 1–10.

---

## Locked mapping (1:1 with architecture)

```text
Langfuse Trace        = 1 agent run
Langfuse Span         = 1 PlanStep execution (one logical step)
Langfuse Generation   = 1 LLM call (planner / argument generator / exploration)
Langfuse Event        = errors / retries / replan triggers
```

### Translation table

| System | Langfuse |
|--------|----------|
| Agent run | **trace** |
| `PlanStep` | **span** |
| `ExecutionResult` | **span** output (+ metadata) |
| Tool identity / timing | **span** metadata |
| LLM call | **generation** |
| Replan | **event** + continued hierarchy under same trace |

---

## Hierarchy (mandatory)

Preserve:

```text
trace → spans → generations → events
```

### Mistakes to avoid

```text
❌ Flat logs only
❌ One span for entire run
❌ No hierarchy
```

---

## Step 1 — Install + init (clean)

**Install:**

```bash
pip install langfuse
```

**Create:** `agent_v2/observability/langfuse_client.py`

**Illustrative:**

```python
import os
from langfuse import Langfuse

langfuse = Langfuse(
    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
    host=os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com"),
)
```

**Rules:**

```text
ONE process-wide client (singleton module pattern)
NO hardcoded secrets in source — env / secrets manager only
NO re-instantiation per request
```

**SDK note:** Langfuse Python SDK evolves — **verify** `trace` / `span` / `generation` / `event` method names and context propagation against the **installed version** (v2+ often uses `@observe` or `Langfuse().trace(...)` with different shapes).

---

## Step 2 — Trace = agent run

**Modify:** `agent_v2/runtime/runtime.py` (or the single entry that starts a run).

```python
from agent_v2.observability.langfuse_client import langfuse

# At start of run(instruction, mode):
lf_trace = langfuse.trace(
    name="agent_run",
    input={"instruction": instruction, "mode": mode},
)
```

**Attach to state** for downstream components:

```python
state.metadata["langfuse_trace"] = lf_trace  # avoid key "trace" if it collides with Pydantic Trace schema
```

Use a **dedicated key** (e.g. `langfuse_trace`) to avoid confusion with **`agent_v2.schemas.trace.Trace`**.

---

## Step 3 — Span = plan step

**Modify:** `agent_v2/runtime/plan_executor.py`

**Before** executing a step:

```python
lf_trace = state.metadata.get("langfuse_trace")
if lf_trace:
    span = lf_trace.span(
        name=f"step_{step.index}_{step.action}",
        input={
            "step_id": step.step_id,
            "goal": step.goal,
            "action": step.action,
        },
    )
    state.metadata["_current_langfuse_span"] = span  # or pass through a small context object
```

**Place** span creation **before** dispatcher / argument generation for that step (or nest generations **inside** the span — see Langfuse docs for parent-child rules).

---

## Step 4 — Record execution result

**After** dispatcher returns **`ExecutionResult`:**

```python
if span:
    span.end(
        output={
            "success": result.success,
            "summary": result.output.summary,  # attribute access if Pydantic
            "error": result.error.model_dump() if result.error else None,
        }
    )
```

Align field access with **`ExecutionResult`** (nested models).

---

## Step 5 — Tool metadata (critical)

**Inside** the same span (before `end`, or via `update`):

```python
span.update(
    metadata={
        "tool_name": result.metadata.tool_name,
        "duration_ms": result.metadata.duration_ms,
    }
)
```

Use **metadata** for tool name, duration, and non-primary output fields.

---

## Step 6 — LLM call tracking (most important)

**Must** wrap:

- **PlannerV2** — planning generation
- **Argument generator** — args-only LLM
- **ExplorationRunner** / **ActionGenerator** — exploration LLM steps

**Planner (illustrative):**

```python
generation = lf_trace.generation(
    name="planner",
    input={"prompt": prompt},
)
response = self.llm(prompt)
generation.end(output={"response": response})
```

**Argument generator:**

```python
generation = lf_trace.generation(
    name="argument_generation",
    input={"step_goal": step.goal, "action": step.action},
)
args = self.llm(prompt)
generation.end(output={"arguments": args})
```

**Exploration:**

```python
generation = lf_trace.generation(
    name="exploration_step",
    input={"instruction": instruction},
)
```

**Architecture note:** AutoStudio requires model calls via **model router** — instrument **at the router boundary** or inside approved clients so **every** LLM path gets a **generation** child under the correct **trace/span**.

**Parent linkage:** Generations should nest under the **active span** when the LLM call is for that step; exploration/planner may sit under **root trace** before spans exist — document parent IDs per Langfuse API.

---

## Step 7 — Retry events

**Inside** retry loop (`PlanExecutor` / `_run_with_retry`):

```python
if lf_trace:
    lf_trace.event(
        name="retry",
        metadata={
            "step_id": step.step_id,
            "attempt": step.execution.attempts,
            "error": result.error.model_dump() if result.error else None,
        },
    )
```

Use **`event`** (or SDK-equivalent) for **retry** signals.

---

## Step 8 — Replan events (critical)

**In** `Replanner` when replan is triggered:

```python
if lf_trace:
    lf_trace.event(
        name="replan_triggered",
        metadata={
            "failed_step_id": request.original_plan.failed_step_id,  # attribute per schema
            "reason": request.failure_context.error.type,
        },
    )
```

Use **structured fields** from **`ReplanRequest`** — not ad-hoc dicts if Pydantic.

---

## Step 9 — Final trace output

**At** end of successful or failed run:

```python
if lf_trace:
    lf_trace.update(
        output={
            "status": result.get("status"),
            "plan_id": plan.plan_id if plan else None,
        }
    )
    lf_trace.end()
```

**Do not** confuse **`len(trace.spans)`** with Langfuse internals — use **`agent_v2.schemas.trace.Trace`** or executor counters for **total_steps** if the UI needs it; Langfuse object may expose different APIs.

---

## Step 10 — Expected Langfuse UI shape

```text
TRACE: agent_run
│
├── GENERATION: planner
│
├── SPAN: step_0_search
│   └── output: success / summary
│
├── SPAN: step_1_open_file
│
├── EVENT: retry
│
├── EVENT: replan_triggered
│
└── SPAN: step_2_edit
```

**Why this matches the architecture**

- **`PlanStep` → span**
- **`ExecutionResult` → span output**
- **LLM → generation**
- **Retry / replan → event**

---

## Coexistence with Phase 9 (internal `Trace`)

| Artifact | Purpose |
|----------|---------|
| **`agent_v2.schemas.trace.Trace`** | Serializable **internal** execution graph, CLI, tests |
| **Langfuse trace** | **External** observability, team UI, retention |

**Option A:** Emit Langfuse from the same hooks as **TraceEmitter** (single place per step).  
**Option B:** Keep both — ensure **no duplicate** conflicting step counts.

---

## Common mistakes

```text
❌ Entire run in one span
❌ LLM calls not as generations
❌ Only raw tool dumps, no span structure
❌ No events on errors / retries / replan
```

---

## Principal verdict

```text
Flat logs ❌ → Observability system ✅
```

**Enables:** execution graph in UI, LLM visibility, failure reasoning, production debugging.

---

## Next steps (out of scope)

1. Graph UI (nodes + edges) fed by Langfuse + internal **Trace**
2. Deterministic replay (needs stable IDs + inputs)
3. Prompt tuning from trace cohorts
4. Automated failure-pattern detection

**Optional follow-ups**

- Langfuse + graph UI (Cursor/Devin-style)
- Multi-agent on top of this pipeline
- Prompt hardening from trace data
- Brutal audit: current code vs frozen architecture

---

## Phase 11 exit criteria

```text
✅ Single Langfuse client, secrets from env
✅ One Langfuse trace per agent run
✅ One span per PlanStep execution (with output + tool metadata)
✅ Generations for planner, argument gen, exploration LLMs
✅ Events for retry and replan
✅ Trace ended with final status / summary
✅ Hierarchy preserved (not flat-only)
✅ No collision with internal Trace schema naming
```

**Phase 11 done** when the above is **implemented** and a real run appears correctly in the Langfuse UI.
