# Phase 5.5 Planner-Memory Integration — Audit & Minimal Plan

**Date**: 2026-04-09  
**Purpose**: Audit planner input flow and propose safe, minimal memory injection strategy  
**Constraint**: DO NOT implement yet — only plan

---

## Revision (tightened)

**Overall verdict**: ~90–95% correct on flow and risks; the original plan was safe and phased. The first draft **over-engineered** how data reaches the prompt (indirect `model_extra` / metadata) and **split “injection” into too many layers**. This revision:

1. **Carries episodic / semantic payloads on `PlannerPlanContext` directly** — e.g. `planner_context.episodic_failures`, `planner_context.semantic_facts` — via **Pydantic `extra="allow"`** (minimal model config; **no new declared `Field`s** for recap strings). Avoid hidden `model_extra` keys, `state.metadata` indirection, and `model_dump()` side channels.
2. **Single rule for where memory enters the user-visible prompt**: extend the **`context_block` string only inside `planner_v2.py`** (`_compose_exploration_context_block` / `_compose_replan_context_block`). **Do not** add memory via registry `variables={...}`, new prompt templates, or extra planner “layers.”
3. **Drop Phase 5.5c** from this roadmap (conversation rolling summary): session memory already feeds the planner; conversation store is not optimized for bounded prompts; extra tokens add risk **without** clear need — revisit as a **separate** follow-up if needed.

### Golden rule (non-negotiable)

**Memory is advisory, not authoritative.** Every memory block MUST reinforce: **if memory conflicts with exploration, trust exploration** (same spirit as the existing session-memory banner).

### Final tightened plan (checkpoint)

| Phase | What | Bound | Prompt location |
|-------|------|-------|-----------------|
| **5.5a** | Episodic | Last **3** failures, ultra-compact | `planner_v2.py`, **after** `session_block` |
| **5.5b** | Semantic | **2–3** facts | Same place (after episodic block when present) |
| **Skip** | Session / conversation enhancement, multi-layer injection, declared recap `Field`s | — | — |

---

# STEP 1 — Planner Input Flow (CRITICAL)

## Current Flow Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   PlannerTaskRuntime                        │
│                   (run_explore_plan_execute)                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│         exploration_to_planner_context()                     │
│         (exploration_planning_input.py:64-122)              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              PlannerPlanContext Construction                 │
│              (schemas/planner_plan_context.py:25-62)        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│           call_planner_with_context()                        │
│           (exploration_planning_input.py:142-171)            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              PlannerV2.plan()                               │
│              (planner/planner_v2.py:306-350)                │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│         _build_plan_prompt_parts()                          │
│         (planner/planner_v2.py:878-962)                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│          _compose_exploration_context_block()               │
│          (planner/planner_v2.py:680-770)                    │
│          _compose_replan_context_block()                     │
│          (planner/planner_v2.py:772-876)                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│          _format_session_memory_block()                     │
│          (planner/planner_v2.py:996-1006)                  │
└─────────────────────────────────────────────────────────────┘
```

## PlannerPlanContext Fields Already Used

```25:62:agent_v2/schemas/planner_plan_context.py
class PlannerPlanContext(BaseModel):
    exploration: Optional[Any] = None                          # ✅ Primary input
    insufficiency: Optional[ExplorationInsufficientContext] = None  # ✅ Weak signal
    replan: Optional[ReplanContext] = None                     # ✅ Failure path
    session: Optional[Any] = Field(default=None)              # ✅ SessionMemory
    query_intent: Optional[QueryIntent] = None                 # ✅ Intent parsing
    exploration_budget: Optional[int] = None                   # ✅ Cap advisory
    validation_feedback: Optional[AnswerValidationResult] = None  # ✅ Answer check
    available_symbols: list[str] = Field(default_factory=list) # ✅ Symbol inventory
    missing_symbols: list[str] = Field(default_factory=list)  # ✅ Symbol gaps
```

## Where Prompt is Assembled

### User Prompt Construction Points

1. **exploration path**: `_compose_exploration_context_block()` → `_format_session_memory_block()`
2. **replan path**: `_compose_replan_context_block()` → `_format_session_memory_block()`

Both delegate to:

```996:1006:agent_v2/planner/planner_v2.py
@staticmethod
def _format_session_memory_block(session: Any) -> str:
    if session is None or not isinstance(session, SessionMemory):
        return ""
    body = session.to_prompt_block()
    if not body:
        return ""
    return (
        "\n--------------------------------\nSESSION MEMORY "
        "(read-only; use for vague references. If it conflicts with exploration, trust exploration.):\n"
        f"{body}\n"
    )
```

## Current Session Memory Injection Location

Session memory is injected at **line 735** and **line 833** in `_compose_exploration_context_block()` and `_compose_replan_context_block()`:

```735:agent_v2/planner/planner_v2.py
session_block = self._format_session_memory_block(planner_context.session)

session_block = self._format_session_memory_block(planner_context.session)
```

---

# STEP 2 — Memory Usage Opportunities

## A. Episodic Memory (Execution History)

### What's Available

**File**: `agent_v2/memory/episodic_query.py`

```44:100:agent_v2/memory/episodic_query.py
class EpisodicQuery:
    def query(
        self,
        tool: Optional[str] = None,
        success: Optional[bool] = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Return up to ``limit`` log rows, most recent first.
        
        Filters: tool, success, recency (implicit via sort)
        """
```

**Output Structure**:
```python
{
    "trace_id": str,
    "task_id": str,
    "tool": str,
    "success": bool,
    "error_type": Optional[str],
    "timestamp": str,
    "_trace_subdir": str,
    "_log_file": str
}
```

### Injection Opportunities

✅ **Can inject (Phase 5.5a scope):**
1. **Recent failures** — `EpisodicQuery(success=False, limit=3)` (**only** these in 5.5a)

✅ **Possible later (not 5.5a):**
2. **Recent runs** — broader `EpisodicQuery(limit=N)` (higher token / noise risk)
3. **Tool-specific patterns** — `EpisodicQuery(tool="...", limit=N)`

❌ **Cannot inject (yet):**
- Similarity search (not in current API)
- Instruction fingerprinting (not in current API)
- Pattern matching beyond tool/success/recency

### High-Value Use Cases

1. **Failure pattern avoidance**: "Recent edit failures show permission errors"
2. **Tool selection learning**: "Recent runs use search_code before open_file"
3. **Budget optimization**: "Last 3 runs exceeded exploration budget"

### Current Connection Status

❌ **NOT connected to planner** - EpisodicQuery exists but is never called from planner flow

---

## B. Semantic Memory (Explicit Facts)

### What's Available

**File**: `agent_v2/memory/semantic_memory.py`

```65:124:agent_v2/memory/semantic_memory.py
class SemanticMemory:
    """Append-only store of explicit facts; word-level token overlap on ``text``."""
    
    def add_fact(self, key: str, text: str, *, tags: Optional[list[str]] = None, 
                 source: Optional[str] = None) -> None:
        """Store a fact with metadata."""
        
    def query(self, query: str, limit: int = 10) -> list[dict]:
        """Token overlap keyword search."""
```

**Fact Structure**:
```python
{
    "key": str,
    "text": str,
    "text_lower": str,
    "tags": list[str],
    "timestamp": str,
    "source": Optional[str]
}
```

### Injection Opportunities

✅ **Can inject:**
1. **Project facts** - `query("project structure api")`
2. **File-level knowledge** - `query("src/api main")`
3. **Known constraints** - `query("constraint permission")`

### High-Value Use Cases

1. **Project understanding**: "Project uses FastAPI + PostgreSQL"
2. **Architecture knowledge**: "API layer in src/api, models in src/models"
3. **Known issues**: "File X requires permission escalation"

### Current Connection Status

❌ **NOT connected to planner** - SemanticMemory exists but is never queried in planner flow

---

## C. Session Memory (Already Partial)

### What's Already Injected

**File**: `agent_v2/runtime/session_memory.py`

Session memory is currently injected via `PlannerPlanContext.session`.

**Current Fields Used** (from `to_prompt_block()`):

```226:247:agent_v2/runtime/session_memory.py
def to_prompt_block(self) -> str:
    """Pruned JSON for planner prompt (~2.5k char cap)."""
    payload: dict[str, Any] = {
        "current_task": _truncate(self.current_task, 400),
        "intent_anchor": self.intent_anchor.model_dump(),
        "last_user_instruction": _truncate(self.last_user_instruction, 400),
        "last_decision": self.last_decision,
        "last_tool": self.last_tool,
        "active_file": self.active_file,
        "active_symbols": self.active_symbols[:5],
        "recent_steps": [s.model_dump() for s in self.recent_steps[-RECENT_STEPS_MAX:]],
        "explore_streak": self.explore_streak,
        "explore_decisions_total": self.explore_decisions_total,
        "last_exploration_engine_steps": self.last_exploration_engine_steps,
    }
```

### What's Missing from Session (out of scope for 5.5)

`ConversationMemory` (rolling summary, turn history) is **not** in the planner prompt today. **Do not add it in Phase 5.5** — defer until conversation memory is intentionally bounded and validated (see Revision above).

### Current Connection Status

⚠️ **PARTIALLY connected** — `SessionMemory` is injected; chat/conversation store remains separate.

---

# STEP 3 — Injection Points Analysis (single rule)

## Where memory appears in the prompt (only place)

**Strict rule**: Memory text is appended **only** inside the large `context_block` built in **`agent_v2/planner/planner_v2.py`**:

- `_compose_exploration_context_block()`
- `_compose_replan_context_block()`

**Pattern**: Same as `session_block` today — concatenate bounded sections into the exploration / replan narrative **before** `context_block` is passed to `reg.render_prompt_parts(...)` with **`instruction` + `context_block` only** (no new registry variables for memory).

**Order inside `context_block`** (after revision):

1. Existing sections (intent, exploration, validation, plan progress, etc.)
2. `session_block` (unchanged)
3. **Episodic failures block** (Phase 5.5a) — **last 3 failures**, ultra-compact
4. **Semantic facts block** (Phase 5.5b) — **2–3 facts**, ultra-compact

**Do not**:

- Thread memory through prompt template `variables` beyond the existing `context_block`
- Introduce parallel “injection layers” or declared schema `Field`s for string recaps (`episodic_recap` / `semantic_recap`)

## How data gets onto `PlannerPlanContext` (population vs prompt)

**Population** (orchestration, **not** prompt formatting): Before `call_planner_with_context()`, attach payloads on the context object so `PlannerV2` can read **`planner_context.episodic_failures`** and **`planner_context.semantic_facts`** directly.

**Recommended mechanism**: On `PlannerPlanContext`, set **`model_config = ConfigDict(extra="allow")`** (Pydantic v2). Then attach payloads in one obvious way, e.g. **`ctx.episodic_failures = failures`** / **`ctx.semantic_facts = facts`** if your Pydantic version exposes extras as attributes, **or** `PlannerPlanContext(..., episodic_failures=rows)` / `ctx.model_copy(update={"episodic_failures": rows})`. Pick **one** style per codebase and use it everywhere — **no** hidden `model_extra` string keys, **no** stuffing `state.metadata` for the planner to discover later.

**Reading in `planner_v2`**: `planner_context.episodic_failures` / `planner_context.semantic_facts`, or `getattr(..., "episodic_failures", None) or []` if absent.

---

# STEP 4 — Risks (CRITICAL)

## 1. Context Explosion (HIGH RISK)

**Problem**: Planner prompts can already be 4K+ tokens. Adding memory could exceed limits.

**Mitigation**:
- STRICT caps: episodic ~200 tokens, semantic ~200 tokens
- Conditional injection (feature flag)
- Truncate aggressively

**Failure Mode**: LLM hallucinates due to overwhelmed context

---

## 2. Conflicting Signals (MEDIUM RISK)

**Problem**: Exploration says "file A has function X", semantic or episodic memory suggests something different.

**Mitigation** (**golden rule**): **Memory is advisory, not authoritative.** Every episodic / semantic block MUST include an explicit line: **if memory conflicts with exploration, trust exploration** (mirror the existing session-memory banner). Semantic / episodic rows are hints, not ground truth.

**Failure Mode**: Planner makes confused decisions from contradictory inputs

---

## 3. Planner Instability (HIGH RISK)

**Problem**: New memory inputs could cause planner output regression

**Discovery Metrics**:
- Monitor plan document fingerprints before/after injection
- Track decision distribution changes (explore vs act vs synthesize)
- Measure plan quality validation rate

**Mitigation**:
- Feature gate: config flag to enable injection
- A/B testing with telemetry
- Rollback mode: disable on detection of anomalies

**Failure Mode**: Planner makes worse decisions with memory

---

## 4. Token Bloat (MEDIUM RISK)

**Problem**: Memory text is verbose, not optimized for token budget

**Example Bad**:
```
"Episodic Memory: Trace ID abc-123, Task ID task-456, Tool search_code ran at 2026-04-09T10:23:45Z with success=True and last_output_summary='Found 42 matches in src/api' ..."
```

**Mitigation**:
- Ultra-compressed format: "edit failure (permission denied) ∙ search_code (42 matches) ∙ open_file (found)"
- Budget enforcement: count actual tokens, not characters
- Hierarchical importance: failures > recent > tool-specific

**Failure Mode**: Context window exhaustion, poor decisions

---

## 5. Payload shape creep (LOW RISK, PREVENTABLE)

**Problem**: Ad-hoc dict keys on context or many new declared `Field`s make the planner contract hard to follow.

**Constraint from Architecture Rules**:
> Do NOT introduce new systems; do NOT redesign core architecture.

**Mitigation**:
- **Do not** add declared `episodic_recap` / `semantic_recap` string fields to `PlannerPlanContext` for Phase 5.5.
- Use **`extra="allow"`** and **two well-known optional extras**: `episodic_failures`, `semantic_facts` (list payloads), set only at orchestration boundaries — readable, grep-friendly, debuggable.
- Keep formatting logic in `planner_v2.py`; do not create parallel “memory context” types unless necessary later.

**Failure Mode**: Hidden coupling and untestable branches

---

# STEP 5 — Minimal Injection Strategy

## Design Philosophy

**Extend, do not replace** the existing `context_block` assembly in `planner_v2.py`.

**Bounded Size**:
- Episodic: **last 3 failures only**, ultra-compact, ~≤200 tokens total for the block
- Semantic: **2–3 facts**, ultra-compact, ~≤200 tokens total for the block
- **No** conversation rolling summary in Phase 5.5 (deferred)

**Optional behavior**: Opt-in via config flags; if disabled or no data, omit blocks entirely.

**Population**: Orchestration attaches `episodic_failures` / `semantic_facts` on `PlannerPlanContext` (extras); **prompt text** is produced only in `planner_v2.py`.

---

## Phase 5.5a: Episodic injection (**do this first** — failures only)

### Implementation constraints

✅ **Last 3 failures** — `EpisodicQuery(success=False, limit=3)`  
✅ **Ultra-compact** single-line or `∙`-joined recap; hard cap ~200 chars/tokens for the block  
✅ **Advisory** — banner: **if this conflicts with exploration, trust exploration**  
✅ **After `session_block`** in `_compose_exploration_context_block` and `_compose_replan_context_block`

### Prompt assembly (`planner_v2.py` only)

```python
@staticmethod
def _format_episodic_failure_block(failures: list[dict]) -> str:
    """Ultra-compact failure recap; failures already capped to 3 at query time."""
    if not failures:
        return ""
    lines = []
    for f in failures[:3]:
        tool = str(f.get("tool", "unknown"))[:16]
        err = str(f.get("error_type", f.get("error_message", "unknown")))[:32]
        ts = str(f.get("timestamp", ""))[:10]
        lines.append(f"[{ts}] {tool}: {err}")
    recap = " ∙ ".join(lines)
    recap = recap[:200]
    return (
        "\n--------------------------------\n"
        "RECENT FAILURES (read-only advisory; if this conflicts with EXPLORATION, trust EXPLORATION):\n"
        f"{recap}\n"
        "--------------------------------"
    )
```

Use: `failures = getattr(planner_context, "episodic_failures", None) or []` then `episodic_block = self._format_episodic_failure_block(failures)` and append after `session_block`.

### Population (`planner_task_runtime.py` — **not** prompt formatting)

Before `call_planner_with_context(...)`, when config enables episodic injection and log dir exists:

```python
failures = EpisodicQuery(Path(log_dir)).query(success=False, limit=3)
ctx = ctx.model_copy(update={"episodic_failures": failures})  # requires PlannerPlanContext extra="allow"
# Or, if extras are writable on the instance: ctx.episodic_failures = failures
```

**Do not** use `model_extra` hidden keys, `state.metadata`, or `model_dump()` side channels for this payload.

---

## Phase 5.5b: Semantic injection (**after 5.5a is validated**)

### Implementation constraints

✅ **2–3 facts** — `SemanticMemory.query(..., limit=3)`  
✅ Same **prompt-only** location: after session block (and after episodic block if present)  
✅ **Advisory** — same conflict rule as episodic and session memory  
✅ Explicit facts only; no LLM-derived “facts” in this phase

### Prompt assembly (`planner_v2.py`)

```python
@staticmethod
def _format_semantic_facts_block(facts: list[dict]) -> str:
    if not facts:
        return ""
    lines = []
    for f in facts[:3]:
        key = str(f.get("key", "unknown"))[:24]
        text = str(f.get("text", ""))[:60]
        lines.append(f"{key}: {text}")
    recap = " ∙ ".join(lines)[:200]
    return (
        "\n--------------------------------\n"
        "SEMANTIC FACTS (read-only advisory; if this conflicts with EXPLORATION, trust EXPLORATION):\n"
        f"{recap}\n"
        "--------------------------------"
    )
```

Use: `facts = getattr(planner_context, "semantic_facts", None) or []`.

### Population (`planner_task_runtime.py`)

When config enables semantic injection and fact store path exists, query from instruction (+ optional `SessionMemory.active_file`), then:

```python
ctx = ctx.model_copy(update={"semantic_facts": facts})
```

---

## Deferred (not Phase 5.5)

- **Session / conversation rolling summary** — `SessionMemory` already covers planner-facing session signals; conversation store is not optimized for bounded prompt use; **skip until** there is a clear need and a token budget.

---

# STEP 6 — Phased Implementation Plan

## Phase 5.5a: Episodic Failure Injection

### Sub-Step 5.5a.1: Infrastructure Setup
- [ ] Add config flags: `planner.enable_episodic_injection`, `agent_v2_episodic_log_dir`
- [ ] Ensure `EpisodicQuery` is importable from planner modules
- [ ] Add unit test for EpisodicQuery with temp dirs

### Sub-Step 5.5a.2: Context population
- [ ] Enable `PlannerPlanContext` **`model_config = ConfigDict(extra="allow")`** (minimal; no new declared fields)
- [ ] Before `call_planner_with_context()`, query **last 3** failures and `model_copy(update={"episodic_failures": rows})`
- [ ] Add integration test: run with log_dir, verify failures attached and appear in prompt

### Sub-Step 5.5a.3: Prompt assembly (`planner_v2.py` only)
- [ ] Add `_format_episodic_failure_block()`; read `getattr(planner_context, "episodic_failures", None) or []`
- [ ] Append after `session_block` in both `_compose_*_context_block` paths
- [ ] Telemetry: count failures included in prompt (optional)

### Sub-Step 5.5a.4: Validation & Rollback
- [ ] Feature: enable by default in dev, opt-in in production
- [ ] Monitor: plan fingerprint regression, decision distribution
- [ ] Rollback: config switch if degradation detected

**Success Criteria**:
- ✅ Up to **3** failures appear in planner prompt (bounded block)
- ✅ No planner output regression (fingerprint stable)
- ✅ Feature flag can disable injection without restart

**Reversibility**:
- Config flag disables population → empty `episodic_failures` → no prompt block
- `extra="allow"` only; no new declared planner contract fields

---

## Phase 5.5b: Semantic fact injection (**after 5.5a validated**)

### Sub-Step 5.5b.1: Infrastructure Setup
- [ ] Add config flags: `planner.enable_semantic_injection`, `agent_v2_semantic_memory_dir`
- [ ] Ensure `SemanticMemory` is importable
- [ ] Add unit test for SemanticMemory with temp files

### Sub-Step 5.5b.2: Context population
- [ ] Before `call_planner_with_context()`, query **2–3** facts; `model_copy(update={"semantic_facts": facts})`
- [ ] Add integration test: add facts, verify attached and appear in prompt

### Sub-Step 5.5b.3: Prompt assembly (`planner_v2.py` only)
- [ ] Add `_format_semantic_facts_block()`; read `getattr(planner_context, "semantic_facts", None) or []`
- [ ] Append after episodic block (or after session if episodic empty/disabled)
- [ ] Telemetry: count facts included (optional)

### Sub-Step 5.5b.4: Validation & Rollback
- [ ] Feature: **disabled by default** (opt-in only)
- [ ] Monitor: plan quality, exploration efficiency
- [ ] Rollback: config switch if conflicts detected

**Success Criteria**:
- ✅ **2–3** facts in planner prompt (bounded block)
- ✅ No decision regressions

**Reversibility**:
- Config off → no population → no block

---

# STEP 7 — Output Summary

## 1. Current Planner Input Flow

**Path**: `PlannerTaskRuntime` → `exploration_to_planner_context()` → `PlannerPlanContext` → `PlannerV2.plan()` → `_build_plan_prompt_parts()` → `_compose_exploration/replan_context_block()` → `_format_session_memory_block()`

**Current Memory Injection**:
- ✅ SessionMemory (via `PlannerPlanContext.session`)
- ❌ Episodic memory (NOT connected)
- ❌ Semantic memory (NOT connected)
- ⏸️ Conversation rolling summary — **deferred** (not Phase 5.5)

## 2. Where Memory Fits

**Prompt text**: **Only** in `planner_v2.py` — extend `context_block` in `_compose_exploration_context_block` / `_compose_replan_context_block` (after `session_block`).

**Data**: `planner_context.episodic_failures` / `planner_context.semantic_facts` via **`PlannerPlanContext` + `extra="allow"`**; populated in `planner_task_runtime.py` before `call_planner_with_context`.

**Golden rule**: Memory is **advisory**; **if it conflicts with exploration, trust exploration** (stated in every new block).

**Priority**:
1. Phase **5.5a** — last **3** episodic failures  
2. Phase **5.5b** — **2–3** semantic facts (**after** 5.5a validated)

## 3. Top Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Context explosion | HIGH | Strict caps; 3 failures / 2–3 facts only |
| Planner instability | HIGH | Feature flags; measure fingerprints / decisions |
| Conflicting signals | MEDIUM | Explicit “trust exploration” in every memory block |
| Token bloat | MEDIUM | Ultra-compact lines |
| Hidden coupling | MEDIUM | Named extras on context; **no** metadata indirection |

## 4. Minimal Injection Plan (Phased)

| Phase | Memory Type | Bound | Where prompt grows | When |
|-------|------------|-------|-------------------|------|
| **5.5a** | Episodic failures | **3** rows | After `session_block` in `planner_v2.py` | **First** |
| **5.5b** | Semantic facts | **2–3** rows | Same | After 5.5a stable |
| — | Conversation summary | — | **Skip** | Later if needed |

**Config**: Episodic and semantic toggles; semantic **off** by default.

## 5. Exact Integration Points

### File changes (planning only — not implemented here)

**`agent_v2/schemas/planner_plan_context.py`**
- Add `model_config = ConfigDict(extra="allow")` so `episodic_failures` / `semantic_facts` can be passed without new declared `Field`s.

**`agent_v2/config.py`**
```python
planner.enable_episodic_injection: bool = False
planner.enable_semantic_injection: bool = False
# + existing episodic root / semantic paths as already defined for Phase 5.x
```

**`agent_v2/planner/planner_v2.py`**
- `_format_episodic_failure_block`, `_format_semantic_facts_block`
- Wire into `_compose_exploration_context_block` and `_compose_replan_context_block` only (no new registry variables)

**`agent_v2/runtime/planner_task_runtime.py`**
- Before each `call_planner_with_context`, optionally `model_copy(update={"episodic_failures": ...})` / `semantic_facts`

**Do not**:
- Add memory via `render_prompt_parts` variables other than `context_block`
- Use `model_extra` hacks or `state.metadata` for planner-visible memory payloads

### Test requirements

**`tests/test_planner_memory_integration.py`** (suggested)
- Unit tests for `_format_episodic_failure_block` / `_format_semantic_facts_block`
- Tests that context with extras round-trips into prompt substring
- Optional: integration test with temp episodic / semantic dirs

---

# Final Recommendation

## Tightened plan

1. **5.5a only first**: fetch **3** failures → attach `episodic_failures` on context → format in **`planner_v2.py`** after session block; banner says trust exploration on conflict.  
2. **5.5b after validation**: **2–3** facts → `semantic_facts` → same prompt location.  
3. **No** Phase 5.5c conversation rolling summary for now.  
4. **One injection surface** for prompt text: `planner_v2.py` `context_block` assembly.  
5. **Direct context attributes** (`episodic_failures`, `semantic_facts`) with **`extra="allow"`** — not indirect enrichment.

**Timeline (indicative)**:
- 5.5a: ~1 week  
- 5.5b: ~1 week after 5.5a sign-off  

**Total in scope**: ~2 weeks for episodic + semantic; conversation enhancement **out of scope** until revisited.