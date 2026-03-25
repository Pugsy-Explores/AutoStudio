# Phase 12.6.G — Observability context & Langfuse projection (agent_v2)

**Scope:** Define **how** Langfuse and internal trace relate for **exploration → planning → execution**, without changing execution semantics. This phase **locks** propagation pattern, span taxonomy, generation naming, events, and the **TraceEmitter vs Langfuse** split. Implementation follows this spec; it is **not** duplicated in this document.

**Relationship:** Extends **Phase 11** (Langfuse client + root trace), **Phase 9** (Trace schema / `TraceEmitter`), and **Phase 12.6** (ExplorationEngineV2, Scoper, etc.). **Does not** add metrics systems, analytics layers, or sampling policies.

---

## 1. Critical: observability context propagation

### 1.1 Problem with manual `langfuse_trace` threading

Passing `langfuse_trace` through every function is:

- a **leaky abstraction**
- **repeated boilerplate**
- **easy to forget** (V2 exploration today does not receive the handle)
- **inconsistent parent–child** relationships in Langfuse

### 1.2 Fix: single runtime-level carrier

Introduce **`ObservabilityContext`** (name may be shortened to `ObsContext` in code if preferred):

```text
class ObservabilityContext:
    langfuse_trace: LFTraceHandle | None   # root handle for this agent run
    current_span: Any | None               # optional: active span for nesting (e.g. executor.step)
```

**Storage:** `state.metadata["obs"]` (not `trace` — reserved for internal `Trace` graph).

**Rule:** **No** function should take `langfuse_trace` as an explicit parameter in the **steady-state** API. Callers read:

```python
obs = state.metadata.get("obs")
# obs.langfuse_trace.span(...) / obs.current_span / etc.
```

**Migration:** Existing `langfuse_trace` on metadata may be deprecated in favor of `obs.langfuse_trace`; adapters can populate `obs` from `langfuse_trace` when `ModeManager` / `AgentRuntime` bootstraps the run.

**Result:** Clean propagation, **consistent hierarchy**, **zero parameter pollution** for observability handles.

### 1.3 `current_span` — **mandatory lifecycle rules**

Defining `current_span` without **when / who / reset** guarantees future bugs. Lock the following.

**Principle:** `current_span` is **not** a global. It is **scoped** to whichever phase owns the active nested work.

**Rule:** At most **one** logical owner sets `current_span` at a time for a given run (serialized phases: exploration, then planning, then execution).

| Owner | When `current_span` is set | When it is cleared |
|--------|----------------------------|--------------------|
| **ExplorationEngine** (or equivalent) | Entering a child span under **`exploration`** (e.g. `exploration.scope`) | On exit of that span (success or failure) |
| **PlanExecutor** | Entering **`executor.step`** for a plan step | On exit of that step span (success, failure, retry exhaustion, abort) |

**Who may write:** Only the **active phase module** above. Do not set `current_span` from deep helpers unless those helpers are clearly delegated by the owner (prefer: owner passes span into generator / dispatcher wrapper).

**Pattern (conceptual):**

```text
with obs.span("exploration.scope") as span:
    obs.current_span = span
    try:
        ...  # nested generations use obs.current_span
    finally:
        obs.current_span = None
        span.end(...)
```

Or equivalent **try / finally** if the SDK has no context manager — **must** clear `current_span` and **end** the span on all paths.

**Nested generations:** `argument_generation` nests under **`executor.step`** → use `PlanExecutor`-owned `current_span` while that step span is active (same pattern as today’s `_current_langfuse_span`, migrated onto `obs`).

---

## 2. Over-instrumentation risk (span budget)

**Rule — span ONLY when:**

1. **Boundary of responsibility** changes, **or**
2. **External I/O** happens, **or**
3. **Duration** matters for diagnosis

**Do not** add spans for every internal subroutine or each exploration loop iteration.

---

## 3. Final span set (exploration) — **tight**

Under parent **`exploration`**, child spans use **fully qualified names** everywhere (docs, code, Langfuse UI):

| Child span (exact name) | Rationale |
|---------------------------|-----------|
| **`exploration.scope`** | LLM; responsibility boundary (breadth reduction) |
| **`exploration.select`** | LLM; responsibility boundary |
| **`exploration.inspect`** | External I/O (read/snippet/dispatcher) |
| **`exploration.analyze`** | LLM; responsibility boundary |

**Removed from the earlier draft (do not add as separate spans):**

- ~~`exploration.discovery`~~
- ~~`exploration.retrieve`~~ as multiple children (`graph` / `vector` / `bm25`)

**Why:** Those are **implementation details**, not stable reasoning boundaries. They create a **noisy** Langfuse graph.

---

## 4. Retrieval: one span, intent-level metadata — **not** internal split

**Do not** expose `retrieve.graph`, `retrieve.vector`, `retrieve.bm25` as separate observability nodes. That **leaks retrieval internals** into the product trace.

**Correct:** A **single** span when retrieval/discovery is worth attributing:

- **Name:** `exploration.retrieve` (optional **only** if you need one aggregate I/O slice; see §2 — if it adds noise, fold counts into **`exploration`** parent metadata only).

**Preferred minimal shape:** Either **no** dedicated retrieve span and attach to **`exploration`** metadata, **or** one span with:

```json
{
  "sources": ["graph", "bm25", "vector"],
  "candidate_count": 20
}
```

**Principle:** Observability reflects **intent and outcomes**, not every internal algorithm.

---

## 5. Generation naming — **strict standard**

For **LLM** steps, enforce:

```text
span.name == generation.name
```

**Examples (aligned with spans):**

| Span name | Generation name |
|-----------|-----------------|
| `exploration.scope` | `exploration.scope` |
| `exploration.select` | `exploration.select` |
| `exploration.analyze` | `exploration.analyze` |
| `planning` | `planner` or `planner_replan` (same naming policy: generation under `planning`) |

**Why:** Langfuse UI clarity — generations nest predictably under their span.

**Naming rule:** Always use **fully qualified** span names (`exploration.scope`, `exploration.select`, …, `executor.step`) — **never** ambiguous short names (`scope`, `select`) in spec or instrumentation.

---

## 6. TraceEmitter vs Langfuse — **locked decision**

| System | Source of truth for |
|--------|---------------------|
| **Langfuse** | **LLM calls** (generations) + **product** spans/events for operator visibility |
| **TraceEmitter** / **`Trace`** | **Replay** and **CLI/API** structured timeline (tool steps, ordered facts) |

**Do not** automatically sync `record_llm` with Langfuse generations.

**Why:** Double maintenance **guarantees drift**. If both need similar facts, duplicate **only** at explicit boundaries (e.g. one bootstrap module sets `obs`, another records `Trace` for tools) — **not** bidirectional sync.

---

## 7. Exploration loop — **no iteration-level spans**

**Do not** create:

```text
exploration_iter_1, exploration_iter_2, …
```

**Do:** A **single** **`exploration`** parent span for the phase, with **child spans per meaningful action** (`exploration.scope`, `exploration.select`, `exploration.inspect`, `exploration.analyze`) as they occur — not per engine tick.

---

## 8. Execution wrapper — **rejected (Option A)**

An intermediate **`execution`** wrapper around **`executor.step`** was considered **inconsistent** with §2: a wrapper adds **no new responsibility boundary** — **PlanExecutor** already defines boundaries via **per-step** spans.

**Decision (locked):** **Do not** introduce an `execution` parent span.

**Final sibling structure under `agent_run`:** `exploration` → `planning` → **`executor.step`** (repeated per step, as siblings or sequential observations per your SDK — **not** nested under a redundant `execution` folder).

---

## 9. Executor span — **standardize**

**Final span name for each plan step:** **`executor.step`** (not `step_3_search` as the **observation name**; legacy names may map via metadata).

**Metadata (required):**

```json
{
  "step_index": 1,
  "action": "search"
}
```

**Why:** Stable schema beats ad hoc string names in dashboards and queries.

---

## 10. Scoper visibility — **required metadata**

For **`exploration.scope`** (span + generation):

```json
{
  "input_count": 20,
  "output_count": 6
}
```

**Why:** Direct signal whether the scoper is **narrowing** appropriately or **broken** (e.g. pass-through, parse failures).

---

## 11. Event placement — **strict attachment**

**Rule:** Events belong to the **Langfuse observation (span) where the fact occurred** — **not** bulk-attached to **`agent_run`**.

**Never** fire all events at root: that **destroys causality** in the trace graph.

| Event | Attach to span |
|-------|----------------|
| `no_relevant_candidate` | **`exploration`** |
| `pending_exhausted` | **`exploration`** |
| `primary_symbol_sufficient` | **`exploration`** |
| `retry` | **`executor.step`** (the step being retried) |
| `replan_triggered` | **`executor.step`** (failed step that triggered replan) |
| `replan_failed` | **`executor.step`** (or last active step before abort — document choice in code) |
| `executor_aborted` | **`executor.step`** if mid-step; else the **current** `executor.step` context when abort is detected |
| `deadlock` | **`executor.step`** when scheduling deadlock is detected (or the innermost active executor context) |

**Implementation note:** Map “attach to span” to the SDK’s API for **observation-scoped** events (or equivalent). If the SDK only supports trace-level events, **still** record **semantic** ownership by linking `parent_observation_id` / metadata — do **not** default to root-only emission.

Without exploration failure events on **`exploration`**, **silent** exploration degradation is invisible in Langfuse.

---

## 12. Final converged model (graph)

```text
Trace: agent_run

Spans (top-level under agent_run):
  exploration                         (parent for exploration phase)
    ├── exploration.scope           + generation exploration.scope
    ├── exploration.select          + generation exploration.select
    ├── exploration.inspect         (I/O; generation only if LLM involved)
    ├── exploration.analyze         + generation exploration.analyze
    [optional: exploration.retrieve OR candidate metadata on exploration only]

  planning                            (phase boundary for planner LLM)
    └── generation: planner | planner_replan

  executor.step                       (repeat per plan step; no execution wrapper)
        └── generation: argument_generation (nested under this step)

Generations (LLM) — names:
  exploration.scope
  exploration.select
  exploration.analyze
  planner | planner_replan
  argument_generation

Events — STRICT attachment:

  On exploration:
    no_relevant_candidate
    pending_exhausted
    primary_symbol_sufficient

  On executor.step:
    retry
    replan_triggered
    replan_failed
    executor_aborted
    deadlock
```

**Principal Engineer verdict:** This model is **implementation-ready**: propagation (**§1**), boundaries (**§2–4, §8–9**), span minimalism, event semantics (**§11**), and system separation (**§6**).

---

## 13. Implementation checklist

### MUST

- Introduce **`ObservabilityContext`** and store under **`state.metadata["obs"]`**; implement **`current_span`** lifecycle (**§1.3**); stop threading **`langfuse_trace`** as a long-term public parameter (migrate call sites).
- **Remove** retrieval sub-spans (`graph` / `vector` / `bm25` as separate nodes); use **one** intent-level representation (metadata on **`exploration`** or single **`exploration.retrieve`** span).
- **Unify** span + generation names for LLM steps (**§5**); use **fully qualified** span names only (**§3, §5**).
- **Standardize** executor span to **`executor.step`** with **§9** metadata; **do not** add an **`execution`** wrapper span (**§8**).
- **Close every span** via **try/finally** or a **context manager** so observations always `end()` — avoids **broken / leaked** traces in Langfuse.
- Emit **events** per **§11** (never all on root).

### SHOULD

- **No** iteration-level spans under **`exploration`** (**§7**).
- Add **exploration failure events** on **`exploration`** (**§11**).
- Add **scoper ratio** metadata (**§10**) on every scoper invocation.

### OPTIONAL

- Unified **prompt truncation policy** (e.g. same cap as planner `12k`) for all generations.
- Thin **helpers** in `agent_v2/observability/` for no-op safe span/generation/event (wrappers still use try/finally internally).

---

## 14. Non-goals

- No new metrics platform, analytics layer, or sampling.
- No change to **execution logic**, **retrieval pipeline order**, or **business rules** inside observability code.
- No automatic **TraceEmitter ⟷ Langfuse** synchronization (**§6**).

---

## 15. References

- Phase 11 — Langfuse client: `agent_v2/observability/langfuse_client.py`
- Phase 9 — Trace: `agent_v2/runtime/trace_emitter.py`, `Docs/architecture_freeze/PHASE_9_TRACE_OBSERVABILITY.md`
- Exploration Scoper: `Docs/architecture_freeze/PHASE_12_6_F_EXPLORATION_SCOPER.md`
