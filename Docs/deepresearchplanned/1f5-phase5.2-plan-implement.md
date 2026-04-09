# ---cursor prompt ---
You are a staff engineer implementing Phase 5.2: Session Memory Persistence from @pugsy_ai/.cursor/plans/agentv2_memory_phase_5_6ebe61fb.plan.md 

Follow the existing Phase 5 plan.
ONLY implement Phase 5.2.

Do NOT modify episodic memory (Phase 5.1).
Do NOT touch semantic memory or planner integration.

---

## GOAL

Persist conversation/session memory across process restarts.

Keep implementation minimal and backward compatible.

---

## STEP 1 — FILE-BASED MEMORY STORE

File: agent_v2/memory/conversation_memory.py

Add new class:

```python
class FileConversationMemoryStore(ConversationMemoryStore):
```

Behavior:

* One file per session_id
* Path:
  .agent_memory/sessions/<session_id>.json

---

## STEP 2 — IMPLEMENT METHODS

Implement:

* load(session_id)
* append_turn(session_id, role, text_summary)
* get_state(session_id)

Persistence:

* Load file if exists
* Else create new ConversationState
* After append → write full JSON

Use:

```python
state.model_dump_json()
ConversationState.model_validate_json(...)
```

---

## STEP 3 — CONFIG SWITCH

File: agent_v2/config.py

Add:

```python
def use_file_conversation_memory() -> bool:
    return True  # or env-driven
```

---

## STEP 4 — STORE SELECTION

Modify:

```python
get_or_create_in_memory_store(state)
```

Replace with:

```python
get_or_create_conversation_store(state)
```

Logic:

```python
if use_file_conversation_memory():
    return FileConversationMemoryStore(...)
else:
    return InMemoryConversationMemoryStore()
```

---

## STEP 5 — SESSION ID HANDLING

Ensure:

* session_id is stable
* use existing:
  SESSION_ID_METADATA_KEY

Fallback:
"default"

---

## STEP 6 — VALIDATION

Write tests:

1. Persistence test:

   * write turns
   * reload store
   * verify data

2. Multiple sessions:

   * two session_ids
   * isolated files

3. Default behavior:

   * no session_id → uses "default"

---

## STEP 7 — RULES

* no DB
* no caching layer
* no async
* no redesign

Keep it:
👉 simple JSON file persistence

---

## OUTPUT

Provide:

1. New class implementation
2. Integration points updated
3. File structure
4. Example usage

---

Focus:
👉 make session memory survive restarts


# ---- implementation ---

Phase 5.2 is implemented in **AutoStudio** (paths match `agent_v2/...`). Summary:

## 1. New class: `FileConversationMemoryStore`

- **Location:** `agent_v2/memory/conversation_memory.py`
- **Paths:** `{sessions_dir}/<safe_session_stem>.json` — default `sessions_dir` is **`<cwd>/.agent_memory/sessions`** (same tree as episodic logs; already in `.gitignore`).
- **Semantics:** Each mutating call does **read → modify → atomic write** (`*.tmp` then `replace`), so a **new** store instance still sees prior data (restart-safe).
- **`ConversationState` / `ConversationTurn`:** Switched to **Pydantic** `BaseModel` so persistence uses `model_dump_json()` / `model_validate_json()` as requested.
- **Protocol:** `get_state(session_id)` added; both stores implement it as an alias of `load`.
- **Invalid JSON:** Treated as empty session (new `ConversationState` for that id).

## 2. Integration

- **`agent_v2/config.py`:**  
  - `use_file_conversation_memory()` — default **True**; set `AGENT_V2_USE_FILE_CONVERSATION_MEMORY=0` (or `false`/`off`) for in-memory only.  
  - `get_conversation_sessions_dir()` — default `.agent_memory/sessions`; override with `AGENT_V2_CONVERSATION_SESSIONS_DIR`.
- **`get_or_create_conversation_store(state)`** — picks `FileConversationMemoryStore` vs `InMemoryConversationMemoryStore` from `use_file_conversation_memory()`, reuses `state.context["conversation_memory_store"]` when the type matches.
- **`get_or_create_in_memory_store`** — kept as a **thin alias** of `get_or_create_conversation_store` (return type `ConversationMemoryStore`).
- **`agent_v2/runtime/planner_task_runtime.py`:** All former `get_or_create_in_memory_store` call sites now use **`get_or_create_conversation_store`**.
- **`agent_v2/memory/__init__.py`:** Exports `FileConversationMemoryStore`, `get_or_create_conversation_store`.

## 3. Session id

- Unchanged: **`SESSION_ID_METADATA_KEY = "chat_session_id"`**; **`get_session_id_from_state`** still returns **`"default"`** when missing/empty.

## 4. Tests

- `tests/test_conversation_memory.py`: persistence round-trip, two isolated sessions, default session id + `get_or_create` + `default.json`, plus autouse env **`AGENT_V2_USE_FILE_CONVERSATION_MEMORY=0`** so this module’s tests don’t write under the real cwd unless a test opts in.

```bash
python3 -m pytest tests/test_conversation_memory.py -q  # 6 passed
```

## 5. Example usage

```python
from agent_v2.memory.conversation_memory import (
    FileConversationMemoryStore,
    get_session_id_from_state,
    get_or_create_conversation_store,
)

# Direct file API (tests or tools)
store = FileConversationMemoryStore()  # or FileConversationMemoryStore(sessions_dir="/tmp/my_sessions")
store.append_turn("my-session", "user", "Remember this")
st = store.get_state("my-session")  # or .load(...)

# Runtime (PlannerTaskRuntime path): same store lives on state.context
store = get_or_create_conversation_store(state)
sid = get_session_id_from_state(state)  # "default" if metadata has no chat_session_id
store.append_turn(sid, "user", "...")
```

**Disable persistence (old behavior):** `export AGENT_V2_USE_FILE_CONVERSATION_MEMORY=0`