# -- chatGPT memory audit from internet ---

Good — this is the **right step before audit**. I’ll give you a **clean, definitive memory model (staff-level)** based on research + real systems.

---

# 🧠 Ground Truth (from research)

Across:

* Anthropic-style agents (implicit architecture)
* MemAgents / ICLR work
* Redis / production systems
* GLM-style memory engines

👉 All converge to **5–7 core memory types**

---

# 🧠 The Definitive Memory Taxonomy (2026)

## 1. 🟢 Working Memory (Short-term / Context)

What it is:

* Current task context
* tool outputs, intermediate state

From research:

* “maintains immediate context within the current interaction” ([Redis][1])

👉 In your system:

```plaintext
ExecutionTask + state + scheduler
```

---

## 2. 🔵 Episodic Memory (What happened)

What it is:

* time-based logs of runs
* past executions, failures, traces

From research:

* “episodic memory stores what happened” ([Chanl][2])

👉 Example:

```plaintext
“Task failed because patch applied twice”
```

---

## 3. 🟣 Semantic Memory (What is true)

What it is:

* facts, knowledge, preferences
* distilled from episodes

From research:

* “semantic memory stores factual knowledge and patterns” ([Chanl][2])

👉 Example:

```plaintext
“Project uses FastAPI”
```

---

## 4. 🟡 Procedural Memory (How to do things)

What it is:

* learned workflows
* sequences of actions

From systems like mengram:

* procedural memory = reusable workflows ([PyPI][3])

👉 Example:

```plaintext
“Fix bug → run tests → patch → verify”
```

---

## 5. 🟠 Session / Chat Memory

What it is:

* conversation history
* user intent within session

From research:

* context window = current conversation state ([Fello AI][4])

👉 This is separate from working memory:

* working = execution
* session = conversation

---

## 6. 🔴 Long-term / Archival Memory

What it is:

* persistent storage across sessions
* searchable memory store

From systems:

* “archival memory = long-term factual storage” ([Botmem][5])

👉 Backed by:

* vector DB / files / logs

---

## 7. ⚫ Persona / Profile Memory (important)

From AdaMem research:

* persona memory = stable user traits ([arXiv][6])

👉 Example:

```plaintext
“User prefers minimal responses”
```

---

# 🧠 Optional (advanced but real)

## 8. 🧩 Graph / Relational Memory

* entity relationships
* project structure

## 9. ⏳ Prospective Memory

* “things to do later”
* reminders / triggers

---

# 🧠 Clean Mapping (what YOU listed vs reality)

| Your term        | Actual category      |
| ---------------- | -------------------- |
| task memory      | working memory       |
| planner memory   | procedural / working |
| session memory   | session memory       |
| episodic memory  | episodic             |
| chat memory      | session memory       |
| long-term memory | semantic + archival  |
| project memory   | semantic + graph     |

👉 You were mixing layers — now it’s clean.

---

# 🧠 What matters MOST (critical insight)

From research:

> “type of memory matters more than amount” ([Chanl][2])

👉 Most systems fail because:

* they only implement **semantic (vector DB)**
* ignore episodic + procedural

---

# 🧠 What YOUR system likely has right now

Based on your phases:

* ✅ Working memory (ExecutionTask + state)
* ✅ Partial session memory
* ❌ No episodic memory (structured)
* ❌ No semantic memory layer
* ❌ No procedural learning

👉 So:

> You currently have **execution state, not memory system**

---

# 🧠 Final Staff-Level Definition

Your system should evolve into:

```plaintext
Working (runtime)
        +
Session (chat)
        +
Episodic (logs)
        +
Semantic (facts)
        +
Procedural (skills)
```

Everything else = optional extensions

---

# 🧠 Next step (important)

Now we know what to audit.

👉 Next:

I’ll give you a **Cursor audit prompt for memory layer**
→ mapping your code to these memory types

Just say:
**“give audit prompt”**

[1]: https://redis.io/blog/ai-agent-memory-stateful-systems/?utm_source=chatgpt.com "AI agent memory: types, architecture & implementation"
[2]: https://www.chanl.ai/es/blog/ai-agent-memory-episodic-semantic-iclr-2026?utm_source=chatgpt.com "Your Agent Remembers Everything Except What Matters | Chanl Blog | Chanl"
[3]: https://pypi.org/project/mengram/0.0.11/?utm_source=chatgpt.com "mengram · PyPI"
[4]: https://felloai.com/ja/how-ai-agents-work/?utm_source=chatgpt.com "How AI Agents Actually Work: The Complete Technical Guide | Fello AI"
[5]: https://botmem.dev/?utm_source=chatgpt.com "botmem — Persistent Memory for Your LLM"
[6]: https://arxiv.org/abs/2603.16496?utm_source=chatgpt.com "AdaMem: Adaptive User-Centric Memory for Long-Horizon Dialogue Agents"


# --cursor audit of current system ----
# AgentV2 Memory System Audit

## Memory Mapping

### 1. WORKING MEMORY ✅ (Present)

| Component | Implementation | Usage |
|-----------|---------------|-------|
| `AgentState` | Main runtime container | Flows through planner → executor → tools |
| `TaskWorkingMemory` | `agent_v2/memory/task_working_memory.py` | Per-top-level-instruction state, stored in `state.context` |
| `SessionMemory` | `agent_v2/runtime/session_memory.py` | Planner intent tracking, streaks, step history |
| `ExplorationWorkingMemory` | `agent_v2/exploration/exploration_working_memory.py` | Per-exploration evidence, gaps, relationships |

### 2. SESSION / CHAT MEMORY ✅ (Present)

| Component | Implementation | Persistence |
|-----------|---------------|-------------|
| `ConversationMemory` | `agent_v2/memory/conversation_memory.py` | ❌ In-memory only (no persistence) |

```29:43:agent_v2/memory/conversation_memory.py
class ConversationState:
    session_id: str = ""
    turns: list[ConversationTurn] = field(default_factory=list)
    rolling_summary: str = ""
    last_final_answer_summary: str = ""
```

**Issue**: `InMemoryConversationMemoryStore` loses all conversation data on process exit.

### 3. EPISODIC MEMORY ✅ (Present)

| Component | Implementation | Persistence |
|-----------|---------------|-------------|
| `TraceEmitter` | `agent_v2/runtime/trace_emitter.py` | ✅ File-based (JSONL) |
| Langfuse traces | `agent_v2/observability/langfuse_client.py` | ✅ External service |

```112:126:agent_v2/runtime/trace_emitter.py
def _persist_execution_log_entry(self, entry: ExecutionLogEntry) -> None:
    if self._execution_log_dir is None:
        return
    log_file = self._execution_log_dir / f"{entry.task_id}.jsonl"
    with open(log_file, "a") as f:
        f.write(entry.model_dump_json() + "\n")
```

### 4. SEMANTIC MEMORY ❌ (Missing)

**No implementation found**. No stored facts, extracted insights, or project knowledge repository.

### 5. PROCEDURAL MEMORY ❌ (Missing)

**No implementation found**. No learned patterns, reusable workflows, or cached successful approaches.

### 6. LONG-TERM / ARCHIVAL MEMORY ⚠️ (Partial)

| Component | Persistence Status |
|-----------|-------------------|
| Trace logs | ✅ Persistent (file-based) |
| Conversation memory | ❌ Lost on process exit |
| Working memory | ❌ Ephemeral per session |

---

## Data Flow Analysis

### Memory Flow Through Execution

```
┌─────────────────────────────────────────────────────────────┐
│                      AGENT STATE                              │
│  instruction, current_plan, exploration_results, step_results │
│  history, context, metadata, trace, context_window             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   STATE.CONTEXT (dict)                       │
│  ├─ task_working_memory (TaskWorkingMemory)                  │
│  ├─ conversation_memory_store (InMemoryConversationMemoryStore)│
│  ├─ shell, editor, browser (tool instances)                   │
│  └─ react_mode, plan_safe_execute (flags)                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    ┌─────────┴─────────┐
                    │                   │
                    ▼                   ▼
          ┌──────────────┐      ┌──────────────┐
          │  Planner     │      │  Executor    │
          │ (Session     │ ◄────┤  (Dispatcher)│
          │  Memory)     │      │              │
          └──────────────┘      └──────────────┘
                    │                   │
                    ▼                   ▼
          ┌──────────────┐      ┌──────────────┐
          │ Exploration  │      │   Tools      │
          │ (Exploration │─────►│ (modify      │
          │  Working    │      │  state)      │
          │  Memory)    │      └──────────────┘
          └──────────────┘              │
                                         ▼
                          ┌────────────────────────┐
                          │   TraceEmitter         │
                          │   (σ trace_*/.jsonl)   │
                          └────────────────────────┘
```

### Critical Flow Issues

1. **Memory passed via `state.context`** - No explicit function parameters, memory is hidden in state dictionary
2. **No cleanup strategy** - `state.context` accumulates without pruning
3. **Trace logs not integrated** - Episodic logs are external to agent state, not queryable for learning

---

## Top 5 Critical Issues

### 1. **No Semantic Memory (HIGH RISK)**
- **Problem**: Agent cannot learn facts across runs (project structure, common patterns, user preferences)
- **Impact**: No long-term learning, redundant exploration, inefficient context building
- **Evidence**: No semantic storage implementation found

### 2. **Conversation Memory Not Persistent (HIGH RISK)**
- **Problem**: `InMemoryConversationMemoryStore` loses all conversation history on process exit
- **Impact**: Multi-turn context lost between sessions, no conversation continuity
- **Evidence**: `conversation_memory.py` has only in-memory store

```47:52:agent_v2/memory/conversation_memory.py
class InMemoryConversationMemoryStore:
    def __init__(self) -> None:
        self._sessions: dict[str, ConversationState] = {}
```

### 3. **Memory Type Mixing in `state.context` (MEDIUM RISK)**
- **Problem**: Working memory, configuration, tool instances all dumped into same dictionary
- **Impact**: Unclear ownership, accidental mutations, difficult to reason about
- **Evidence**: Task working memory, conversation store, tool references all use `state.context`

```93:103:agent_v2/memory/task_working_memory.py
def task_working_memory_from_state(state: Any) -> TaskWorkingMemory:
    ctx = getattr(state, "context", None)
    key = TASK_WORKING_MEMORY_CONTEXT_KEY
    existing = ctx.get(key)
```

### 4. **No Procedural Memory (MEDIUM RISK)**
- **Problem**: No workflows or learned patterns stored/reused
- **Impact**: Multistep task has no planning leverage, each run replans from scratch
- **Evidence**: No procedural memory implementation found

### 5. **Episodic Memory Not Queryable (MEDIUM RISK)**
- **Problem**: Trace logs are written but never read back by the agent
- **Impact**: Past failures/successes not leveraged for replanning, closed loop
- **Evidence**: TraceEmitter only writes to files, no lookup API

---

## Gaps Summary

| Memory Type | Status | Gap | Severity |
|-------------|--------|------|----------|
| Working | ✅ | Clean | None |
| Session | ⚠️ | Persistence missing | HIGH |
| Episodic | ⚠️ | Not queryable | MEDIUM |
| Semantic | ❌ | Missing | HIGH |
| Procedural | ❌ | Missing | MEDIUM |
| Long-term | ⚠️ | Partial only | HIGH |

---

## Minimal Plan (No Overengineering)

### Phase 1: Fix Session Memory (Minimal Persistence)

**Action**: Add file-based persistence to `ConversationMemory`

```python
# agent_v2/memory/conversation_memory.py
class FileConversationMemoryStore(ConversationMemoryStore):
    def load(self, session_id: str) -> ConversationState:
        path = self._session_path(session_id)
        if path.exists():
            return ConversationState.model_validate_json(path.read_text())
        return ConversationState(session_id=session_id)
    
    def append_turn(self, session_id: str, role: str, text_summary: str) -> None:
        # ... update state ...
        self._persist(session_id)
    
    def _persist(self, session_id: str) -> None:
        state = self._sessions[session_id]
        self._session_path(session_id).write_text(state.model_dump_json())
```

**Cost**: ~20 lines, zero architectural changes

---

### Phase 2: Enable Episodic Memory Lookups (Minimal)

**Action**: Add trace query facade (no full semantic layer)

```python
# agent_v2/memory/episodic_lookup.py
class EpisodicLookup:
    def find_similar_traces(self, instruction: str, limit: int = 3) -> list[dict]:
        """Simple text search over trace files"""
        traces = self._load_recent_traces(limit=50)
        scored = [(t, self._similarity(instruction, t["instruction"])) for t in traces]
        return [t for t, s in sorted(scored, key=lambda x: x[1], reverse=True)][:limit]
```

**Cost**: ~30 lines, enabled by existing trace files

---

### Phase 3: Semantic Foundation (Future-Ready)

**Action**: Add minimal fact storage, enable distillation later

```python
# agent_v2/memory/semantic_memory.py
class SemanticMemory:
    def add_fact(self, symbol: str, file: str, fact: str) -> None:
        """Store a ground truth fact"""
    
    def query(self, query: str) -> list[dict]:
        """Return relevant facts (simple keyword match for now)"""
```

**Cost**: ~25 lines, extendable without breaking changes

---

## Summary

| Item | Finding |
|------|---------|
| **Working Memory** | ✅ Well-designed, ephemeral per task |
| **Session Memory** | ⚠️ Needs file persistence |
| **Episodic Memory** | ⚠️ Logged but not queryable |
| **Semantic Memory** | ❌ Missing entirely |
| **Procedural Memory** | ❌ Missing entirely |
| **Top Risk** | No long-term learning across runs |
| **Data Flow** | Hidden in `state.context`, needs explicit passing |

**Recommendation**: Implement Phase 1 (conversation persistence) immediately to enable basic continuity. Phase 2 (episodic lookup) closes the loop on existing logs. Phase 3 sets foundation for semantic memory without overengineering.

# --improvments --
🧠 Staff-Level Verdict

👉 Direction is correct
👉 Phase split is mostly right
👉 But one structural mistake + one priority mistake

🔴 Major Correction 1 — Wrong Phase Ordering

From your plan :

Phase 1: Session
Phase 2: Episodic
Phase 3: Semantic

👉 This is wrong order

✔ Correct order (critical)
1. Episodic (you already have data)
2. Session (persistence)
3. Semantic (build on episodic)
4. Procedural (later)
🧠 Why this matters
Episodic = source of truth
Semantic = derived from episodic
Session = UX layer

👉 You cannot build semantic properly without episodic access

🔴 Major Correction 2 — Underestimating Episodic Memory

You wrote:

“add simple lookup (~30 lines)”

👉 This is too naive

❗ Real issue

Your episodic memory:

exists (JSONL logs) ✅
but is completely disconnected from system ❌

👉 This is the biggest gap in your system right now

✔ What must change

Not just:

simple text search

But:

structured access to execution history

At minimum:

filter by tool
filter by failure/success
retrieve recent relevant runs

👉 Still simple, but not trivial

🟡 Important Adjustment — Memory boundaries

From audit :

everything in state.context

👉 This is your next real problem

❗ Issue

You currently have:

state.context = garbage bag

Mix of:

working memory
tools
config
session
✔ Minimal fix (don’t overbuild)

Do NOT redesign system.

Just:

state.memory = {
    "working": ...,
    "session": ...,
}

👉 Separate logically, not architecturally

🟡 Semantic Memory Plan (adjust)

Your proposal is good, but:

👉 Add one constraint:

ONLY store explicit facts (no embeddings yet)

Examples:

file → symbols
project → structure
known constraints

👉 No vector DB yet

❌ What you should NOT do now
procedural memory ❌
vector search ❌
embeddings ❌
memory scoring ❌
🧠 Final Phase Plan (corrected)
🟢 Phase 5.1 — Episodic Access (FIRST)
build query layer over trace logs
filter:
by tool
by success/failure
by recency

👉 Make logs usable

🟢 Phase 5.2 — Session Persistence
file-based conversation memory
no redesign
🟡 Phase 5.3 — Semantic Memory (minimal)
simple fact store
keyword lookup
🟡 Phase 5.4 — Memory Separation (light)
separate memory from state.context
🧠 Final Verdict

👉 Audit is good but slightly misprioritized
👉 Biggest opportunity:

Episodic memory is already there — make it usable first