# -- cursor prompt --
You are a principal engineer implementing Phase 5.5b: Semantic Memory Injection into the planner from @AutoStudio/Docs/deepresearchplanned/phase5-5-planner-memory-integration-audit-plan.md 

Follow Phase 5.5a patterns exactly.
Keep it minimal, safe, and consistent.

ONLY implement semantic fact injection.
Do NOT modify episodic logic.
Do NOT implement session enhancements.

---

## GOAL

Inject relevant semantic facts (project knowledge) into planner prompt
to improve planning decisions.

---

## STEP 1 — FETCH FACTS (RUNTIME)

File: agent_v2/runtime/planner_task_runtime.py

Add:

```python
from agent_v2.memory.semantic_memory import SemanticMemory
from agent_v2.config import get_semantic_memory_dir
```

Then:

```python
def _get_relevant_facts(state, planner_context) -> list[dict]:
    base_dir = get_semantic_memory_dir()
    if not base_dir:
        return []

    mem = SemanticMemory(base_dir)

    # PRIORITY: active file > instruction
    query_parts = []

    session = getattr(planner_context, "session", None)
    if session and getattr(session, "active_file", None):
        query_parts.append(str(session.active_file))

    if hasattr(state, "instruction"):
        query_parts.append(str(state.instruction)[:100])

    query = " ".join(query_parts).strip()
    if not query:
        return []

    return mem.query(query, limit=3)
```

---

## STEP 2 — ATTACH TO CONTEXT

Same pattern as episodic:

```python
facts = _get_relevant_facts(state, planner_context)
planner_context.__dict__["semantic_facts"] = facts
```

Guard with config:

```python
if enable_semantic_injection():
    planner_context.__dict__["semantic_facts"] = facts
```

---

## STEP 3 — FORMAT BLOCK

File: agent_v2/planner/planner_v2.py

Add:

```python
@staticmethod
def _format_semantic_facts_block(facts: list[dict]) -> str:
    if not facts:
        return ""

    lines = []
    for f in facts[:3]:
        key = (f.get("key") or "fact")[:20]
        text = (f.get("text") or "")[:60]
        lines.append(f"{key}:{text}")

    recap = " ∙ ".join(lines)

    return (
        "\n--------------------------------\n"
        "PROJECT FACTS (advisory):\n"
        f"{recap}\n"
        "If conflicts with exploration, trust exploration.\n"
    )
```

---

## STEP 4 — INJECT INTO PROMPT

Inside BOTH:

* `_compose_exploration_context_block`
* `_compose_replan_context_block`

After episodic block:

```python
facts = getattr(planner_context, "semantic_facts", [])
semantic_block = self._format_semantic_facts_block(facts)
```

Inject:

```python
{session_block}
{episodic_block}
{semantic_block}
```

---

## STEP 5 — CONFIG FLAG

File: agent_v2/config.py

Add:

```python
def enable_semantic_injection() -> bool:
    return False  # default OFF (important)
```

---

## STEP 6 — VALIDATION

Write tests:

1. Formatting test
2. Integration test (mock SemanticMemory)
3. Disabled mode test
4. Combined test (episodic + semantic together)

---

## RULES

* max 3 facts
* no scoring
* no embeddings
* no schema changes
* no planner redesign

Keep it:
👉 simple, advisory, bounded

---

## OUTPUT

Provide:

1. Files modified
2. Injection location
3. Example prompt snippet

---

Focus:
👉 improve planning with minimal project knowledge

# --implement --

Here’s what was implemented for **Phase 5.5b** (semantic-only; episodic code paths unchanged aside from sharing the same `session_segment` assembly).

### 1. Files modified

| File | Change |
|------|--------|
| `agent_v2/config.py` | `enable_semantic_injection()` — `_bool_env("AGENT_V2_ENABLE_SEMANTIC_INJECTION", False)` (off unless env enables). |
| `agent_v2/runtime/planner_task_runtime.py` | `_get_relevant_facts`, `attach_semantic_facts_if_enabled`; imports `SemanticMemory`, `get_semantic_memory_dir`, `enable_semantic_injection`. All `call_planner_with_context` calls pass `state=state` where `state` exists. |
| `agent_v2/runtime/exploration_planning_input.py` | Optional `state`; after episodic attach, `attach_semantic_facts_if_enabled(ctx, instruction=instruction, state=state)`. |
| `agent_v2/runtime/replanner.py` | After episodic attach, `attach_semantic_facts_if_enabled(pc, instruction=request.instruction, state=None)`. |
| `agent_v2/planner/planner_v2.py` | `_format_semantic_facts_block`; in both context composers, `session_segment = session_block.strip() + episodic_block + semantic_block`. |
| `tests/test_semantic_planner_injection.py` | Format, integration (mock `_get_relevant_facts`), disabled, episodic+semantic ordering. |

**Note:** Facts are stored with `object.__setattr__(..., "semantic_facts", ...)` like episodic (Pydantic-safe). Your spec mentioned `__dict__[...]`; behavior matches 5.5a.

**Query text:** `active_file` first (if `session.active_file`), then `state.instruction[:100]` when `state` is passed, else `instruction[:100]`.

### 2. Injection location

- **Attach:** `call_planner_with_context` (after `model_copy` / session) and `Replanner.replan`, immediately after `attach_episodic_failures_if_enabled`.
- **Prompt:** `PlannerV2._compose_exploration_context_block` and `_compose_replan_context_block` — same `session_segment` tail as 5.5a, extended with **semantic after episodic**.

### 3. Example prompt snippet

```text

--------------------------------
RECENT FAILURES (advisory; avoid repeating):
search:timeout ∙ open_file:tool_error
If conflicts with exploration, trust exploration.

--------------------------------
PROJECT FACTS (advisory):
routing:Use dispatcher for all tool calls ∙ policy:No edits in plan_safe mode
If conflicts with exploration, trust exploration.

```

(With episodic off / empty log, the `RECENT FAILURES` block is omitted; with semantic off, `PROJECT FACTS` is omitted.)

**Enable semantic injection:** set `AGENT_V2_ENABLE_SEMANTIC_INJECTION=1` (default remains off).

**Tests:** `pytest tests/test_semantic_planner_injection.py tests/test_episodic_planner_injection.py tests/test_planner_v2.py` — 55 passed.

--- updates --
