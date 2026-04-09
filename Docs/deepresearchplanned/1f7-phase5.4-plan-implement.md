# --cursor prompt --
You are a staff engineer implementing Phase 5.4: Memory Separation from @pugsy_ai/.cursor/plans/agentv2_memory_phase_5_6ebe61fb.plan.md 

This is a careful migration step.

Do NOT break existing behavior.
Do NOT redesign architecture.

---

## GOAL

Introduce `state.memory` as a structured memory namespace,
while keeping backward compatibility with `state.context`.

---

## STEP 1 — ADD MEMORY FIELD

File: agent_v2/state/agent_state.py

Add:

```python
memory: dict[str, Any] = field(default_factory=dict)
```

Do NOT remove or modify `context`.

---

## STEP 2 — DEFINE MEMORY KEYS

Standardize keys:

```python
MEMORY_WORKING = "working"
MEMORY_SESSION = "session"
```

Do NOT add semantic/episodic here yet.

---

## STEP 3 — UPDATE WORKING MEMORY ACCESS

File: agent_v2/memory/task_working_memory.py

Modify:

```python
task_working_memory_from_state(state)
```

New logic:

```python
if "working" in state.memory:
    return state.memory["working"]

# fallback
ctx = state.context
existing = ctx.get(TASK_WORKING_MEMORY_CONTEXT_KEY)

# migrate
state.memory["working"] = existing
return existing
```

Ensure:

* new writes go to `state.memory["working"]`

---

## STEP 4 — UPDATE SESSION MEMORY ACCESS

File: dag_executor.py (or wherever `_planner_session_memory_from_state` exists)

Change:

```python
state.context["planner_session_memory"]
```

To:

```python
if "session" in state.memory:
    return state.memory["session"]

# fallback
existing = state.context.get("planner_session_memory")

# migrate
state.memory["session"] = existing
return existing
```

---

## STEP 5 — CONVERSATION STORE (LIGHT TOUCH)

File: conversation_memory.py

Where store is retrieved:

* keep storing reference in:

  * state.context (for now)

Optional:

* also mirror into `state.memory["session"]` if clean

DO NOT refactor deeply

---

## STEP 6 — WRITE PATH CONSISTENCY

Ensure:

* all new writes go to `state.memory`
* `state.context` is only fallback / legacy

---

## STEP 7 — DO NOT TOUCH

* dispatcher
* tools
* episodic memory
* semantic memory
* planner interfaces

---

## STEP 8 — VALIDATION

Test:

1. Old flows still work (no memory in state.memory)
2. New flows populate state.memory
3. No duplication bugs
4. No missing memory

---

## OUTPUT

Provide:

1. Files modified
2. Migration behavior
3. Before/after memory access example

---

## RULES

* minimal changes only
* no refactor beyond access layer
* no breaking changes

Focus:
👉 introduce structure without disruption


# --implementation ---

Here is what was already in place and what was completed in this pass.

### Already present (Phase 5.4 core)

- `AgentState.memory` plus `MEMORY_WORKING` / `MEMORY_SESSION`, `ensure_agent_memory_dict()` in `agent_v2/state/agent_state.py`
- Pydantic `AgentState.memory` in `agent_v2/schemas/agent_state.py`
- `task_working_memory_from_state` / `reset_task_working_memory` preferring `state.memory[MEMORY_WORKING]` and mirroring `context["task_working_memory"]`
- `planner_session_memory_from_state` preferring `state.memory[MEMORY_SESSION]` and mirroring `context["planner_session_memory"]`
- `tests/test_agent_memory_namespace.py` for working + planner session migration

### Changes made now

1. **`agent_v2/memory/conversation_memory.py`** — `get_or_create_conversation_store` now:
   - uses `ensure_agent_memory_dict(state)`
   - prefers a **compatible** store in `state.memory["conversation_memory_store"]` (same key as context)
   - otherwise adopts **legacy** `context` store into `memory`
   - on create, sets **both** `memory` and `context` (aligned with “writes live in `state.memory`” while keeping context for older readers)

2. **`agent_v2/runtime/session_memory.py`** — same pattern as task working memory: **`dict`** values under `MEMORY_SESSION` or `planner_session_memory` are validated with `SessionMemory.model_validate` and replaced with a real `SessionMemory`, then mirrored.

3. **`tests/test_agent_memory_namespace.py`** — coverage for planner-session dict migration and conversation-store migration / memory-first behavior.

### Migration behavior (short)

| Slot | Prefer | Fallback | Mirroring |
|------|--------|----------|-----------|
| Task working | `memory["working"]` | `context["task_working_memory"]` | updates context when memory wins |
| Planner session | `memory["session"]` | `context["planner_session_memory"]` | syncs context |
| Conversation store | `memory["conversation_memory_store"]` | `context["conversation_memory_store"]` | syncs context |

Compatibility follows config (`FileConversationMemoryStore` vs `InMemoryConversationMemoryStore`); mismatched types are replaced by a new store on both sides.

### Before / after (conceptual)

**Before:** only `state.context["task_working_memory"]` (and similar) was guaranteed.

**After:** canonical handles live under `state.memory["working"]`, `state.memory["session"]`, and `state.memory["conversation_memory_store"]`, with `context` kept in sync so existing code that only reads `context` still works.

All **17** tests in `test_agent_memory_namespace.py` and `test_conversation_memory.py` pass.