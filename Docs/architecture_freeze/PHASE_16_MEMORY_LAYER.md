# Phase 16 — Memory layer (production design)

**Scope:** This document is the authoritative Phase 16 specification. It introduces a **structured, queryable memory layer** that improves reasoning across steps **without** contaminating core execution logic, the **retrieval pipeline**, or **ranking**. It builds on **Phase 9** (trace), **Phase 15** (replay), and coexists with **Phase 13–14** (LLM / diff visibility). This file is **not** executable.

**Framing**

Memory is the layer that either **stays clean** or becomes a **mess forever**. This spec favors **production-safe minimalism** over generic “agent memory” hype.

---

## Objective

```text
Introduce a structured, queryable memory layer that improves reasoning across steps
WITHOUT contaminating core execution logic or retrieval
```

---

## First principle (non-negotiable)

```text
Memory is NOT context stuffing
Memory is NOT prompt history
Memory is NOT logs
```

**Memory is:**

```text
A structured store of distilled, reusable knowledge derived from execution
```

---

## What problem memory solves

**Without memory**

```text
Agent repeats mistakes
Agent forgets discoveries
Agent re-searches same things
Agent loses intermediate insights
```

**With memory**

```text
Agent builds knowledge across steps and runs (bounded, explicit)
```

---

## Design principle

```text
Memory is a READ-ONLY signal to planning, NOT a control mechanism
```

This prevents:

- hidden heuristics  
- bias injection into retrieval or ranking  
- brittle, non-reproducible behavior  

**Architecture alignment:** Retrieval pipeline order and contracts stay frozen; memory **does not** override retrieval, inject actions, or bias context ranking (see **Guardrails** below).

---

## Types of memory (strict — v1)

Implement **two** types only for now.

### 1. Episodic memory (per run)

```text
What happened during THIS execution
```

**Examples**

- “`patch_executor.py` contains `execute_patch`”
- “test failed due to missing import”
- “file X already inspected”

### 2. Semantic memory (cross-run, optional, minimal)

```text
General reusable knowledge
```

**Examples**

- “`AgentLoop` handles retries”
- “`patch_executor` applies diffs safely”

Keep semantic memory **small** until metrics justify growth.

---

## Memory schema

**Core structure (illustrative — amend `SCHEMAS.md` on implementation):**

```python
class MemoryEntry:
    id: str
    type: Literal["episodic", "semantic"]

    content: str

    source: dict   # e.g. step_id, file, trace id

    confidence: float  # 0–1

    timestamp: float
```

**Storage (illustrative):**

```python
class MemoryStore:
    episodic: List[MemoryEntry]
    semantic: List[MemoryEntry]
```

Persist as appropriate (in-process v1 vs durable store later); keep bounded.

---

## Where memory is written

**Only** at defined points (no blanket “log everything”):

1. **After successful tool execution** — extract a key insight from the result (distilled, not raw).
2. **After exploration** — store findings (distilled).
3. **After failure** — store failure cause (distilled).

Exact hooks: **ExplorationRunner**, **PlanExecutor** (post-step), aligned with existing runtime boundaries — **no** writes inside retrieval stages.

---

## How memory is created (critical)

**Do not**

```text
store raw logs
store entire outputs
store everything
```

**Do**

Use the **model router / designated reasoning path** to **distill** one high-signal line (or short bullet) per write — **not** ad-hoc `openai.chat` in business logic (see project rules: **no direct LLM calls**).

**Illustrative extraction**

```python
def extract_memory(step, result):
    prompt = f"""
    Extract ONE key reusable insight from this step.
    Be concise and specific.

    Step: {step}
    Result: {result}
    """

    return model_router.generate(...)  # or designated distillation helper
```

**Goal**

```text
small
high-signal
reusable
```

---

## Where memory is read

**Primary read surface:** **planner input** only (not dispatcher policy as a hidden controller).

**Extend planner contract (illustrative):**

```python
class PlannerInput:
    exploration: ExplorationResult
    memory: List[MemoryEntry]
```

**Prompt usage (bounded)**

```text
Relevant past insights:
- ...
- ...
```

**Limit**

```text
top 5–10 max (configurable)
```

---

## Memory retrieval

**v1 — simple**

```python
def retrieve_memory(query):
    return keyword_match(memory_store)
```

**Later (Phase 17+ or dedicated sub-phase)**

```text
vector search over memory entries
```

Retrieval-for-memory is **separate** from repo retrieval; it must not replace or reorder **repository** retrieval stages.

---

## Integration points

| Component | Role |
|-----------|------|
| **ExplorationRunner** | Writes episodic findings (distilled) |
| **PlanExecutor** | Writes after steps (success / failure per policy) |
| **Planner** (e.g. PlannerV2) | Reads `memory` in `PlannerInput` |

---

## Trace integration

Add trace / graph step classification for observability:

```text
TraceStep.kind = "memory"
GraphNode.type = "memory"   # when projecting to execution graph
```

**Example flow (illustrative)**

```text
[tool] → [memory] → [LLM]
```

Emission must remain **observable** (Phase 9 / Phase 12 patterns).

---

## UI design

**Memory node**

```text
color: green
icon: brain / database
```

**Detail panel**

```text
content
confidence
source
```

---

## Critical guardrails

**Do not**

```text
let memory override retrieval
let memory inject actions
let memory bias ranking
```

**Do not** flood the store with low-confidence noise.

**Always**

```text
keep memory optional and bounded
```

If memory is empty, execution path must behave as today.

---

## Testing

**Add:** `tests/test_memory_layer.py`

**Validate**

```text
- memory created after steps (when policy says so)
- memory retrieved correctly (keyword v1)
- planner receives memory in PlannerInput
- memory does not break execution when empty or malformed edge cases
- no violation of retrieval invariants (regression tests as needed)
```

---

## Expected impact

**Before**

```text
agent repeats work
agent forgets distilled context between steps
```

**After**

```text
agent accumulates explicit, bounded knowledge for planning
agent can become faster + more accurate (measurable in eval harness)
```

---

## Principal engineer warning

```text
Memory is where most systems silently degrade.
```

**Bad memory** → hidden bias, hallucination reinforcement, non-determinism.

**Good memory** → clean signal, faster reasoning, better plans — **if** distillation, bounds, and guardrails hold.

---

## Implementation plan (ordered)

1. Create `MemoryEntry`, `MemoryStore` (schemas + minimal API).
2. Add memory extraction (distillation via model router / designated path).
3. Hook **PlanExecutor** + **ExplorationRunner** (write points only).
4. Add retrieval (keyword v1).
5. Inject into `PlannerInput` and planner prompts (cap 5–10).
6. Add trace / graph `memory` nodes.
7. Tests.

---

## Final state after Phase 16

```text
Trace      → what happened
Replay     → how it happened (playback)
Diff       → what changed
Memory     → what was learned (distilled)
```

```text
This is a self-improving agent foundation only if memory stays bounded and subordinate to retrieval + policy.
```

---

## Next phase (preview)

**Phase 17 — Multi-agent separation** (Explorer / Planner / Executor as independent agents or roles with explicit boundaries).

**Validation deliverable when implementing Phase 16**

```text
- 2–3 memory entries generated in a real run
- one planner input (or fixture) showing memory usage in context
```

Then tune **signal vs noise** (the hardest part).
