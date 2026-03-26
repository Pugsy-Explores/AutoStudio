# Langfuse Exploration Tracing — Audit & Production Plan

**Status:** Design only (implementation tracks this document).  
**Audience:** Principal-engineering review; aligns exploration observability with Langfuse traces, spans, and generations.

---

## Converged mandatory requirements (non-negotiable)

These seven decisions **override** earlier optional wording elsewhere in the doc:

1. **Single LLM entry contract** — All exploration LLM work goes through **one** traced wrapper (`exploration_llm_call` or equivalent). No direct `call_reasoning_model`, no bypass via raw `llm_generate_fn` for production paths (tests may inject **through** the wrapper).
2. **Synthesis** — Must be a **child** of the `exploration` span, with generation metadata: `stage=synthesis`, `input_source=adapter_output`.
3. **Trace ownership** — If `langfuse_trace` is **None**, exploration **must** create a **fallback trace** (do not rely purely on upstream).
4. **Generation count sanity** — Enforce invariant: **`#` LLM invocations in exploration code path == `#` Langfuse generations** for that run (automated check / test; not UI-only).
5. **`prompt_registry_key`** — **Mandatory** on **every** exploration generation `input.extra` (not optional).
6. **Adapter** — **Do not** instrument `ExplorationResultAdapter` (deterministic; noise).
7. **WorkingMemory** — **Do not** instrument (high-frequency, low signal, trace noise).

---

## Executive summary

The exploration pipeline already wires **Langfuse** for several **LLM** stages and some **non-LLM** stages via `ExplorationEngineV2` + `langfuse_helpers`. Today, **token attribution** relies on **`get_last_chat_usage()`** after `call_reasoning_model`, while **multiple entry paths** (direct calls, injected `llm_generate_fn`, synthesis) break **trace consistency** and **generation/token alignment**.

**Mandatory fix:** enforce a **single traced LLM wrapper** used by every exploration stage including **synthesis**; attach **`prompt_registry_key`** to every generation; **fallback** trace when upstream omits `langfuse_trace`; **assert** generation count matches LLM call count. **Do not** add spans for adapter or working memory.

---

## Step 1 — Audit: current coverage

### How tracing enters exploration

- **Planner / mode manager** passes `langfuse_trace` (or `obs.langfuse_trace`) into `ExplorationRunner.run` → `ExplorationEngineV2.explore(..., langfuse_trace=...)`.
- **No `@observe` decorators** were found on exploration modules; instrumentation is **explicit** (`span`, `generation`) via `agent_v2.observability.langfuse_helpers`.

### Component-by-component

| Component | Langfuse today | Notes |
|-----------|----------------|--------|
| **QueryIntentParser** | **Yes** — `try_langfuse_generation` + `langfuse_generation_end_with_usage` | Must migrate internals to **single wrapper** + mandatory `prompt_registry_key` |
| **CandidateSelector** | **Yes** | Same |
| **ExplorationScoper** | **Yes** | Same |
| **Inspector / InspectionReader** | **Partial** | **No** Langfuse inside modules; **engine** wraps inspect with **spans** + bounded output (not generations) |
| **UnderstandingAnalyzer** | **Yes** | Same wrapper + key policy |
| **Expansion loop** | **Partial** | Tool-style spans + bounded outputs |
| **WorkingMemory** | **No** | **Policy: leave untraced** (do not add) |
| **ExplorationResultAdapter** | **No** | **Policy: do not instrument** |
| **exploration_llm_synthesizer** | **No** | Must use wrapper + nested under `exploration`; tags `stage`, `input_source` |

### LLM call paths (problem statement)

- **`call_reasoning_model`** — multiple direct call sites.
- **`llm_generate_fn` / `llm_generate` injection** — tests and harness can skip tracing if they bypass the wrapper.
- **Synthesis** — calls LLM **outside** the same structural pattern as intent/selector/analyzer.

**Target:** exactly **one** implementation path:  
`exploration_llm_call(...)` → `try_langfuse_generation` → `call_reasoning_model` (or delegated) → `langfuse_generation_end_with_usage` with **`prompt_registry_key`** in `input.extra`.

### Audit JSON (Step 1 output)

```json
{
  "covered_components": [
    "QueryIntentParser (LLM generation + usage) — refactor to single wrapper",
    "CandidateSelector single + batch — refactor to single wrapper",
    "ExplorationScoper — refactor to single wrapper",
    "UnderstandingAnalyzer — refactor to single wrapper",
    "ExplorationEngineV2 outer span `exploration` + subspans (query_intent, inspect, analyze, expand, discovery, scope, select)",
    "Non-LLM tool outcomes via span end (inspect / discovery / expand summaries)"
  ],
  "missing_components": [
    "exploration_llm_synthesizer — must use wrapper + exploration parent + metadata tags",
    "Unified exploration_llm_call module",
    "Fallback trace when langfuse_trace is None",
    "Automated generation-count invariant test"
  ],
  "explicitly_excluded_from_instrumentation": [
    "ExplorationResultAdapter",
    "ExplorationWorkingMemory"
  ],
  "llm_calls_without_tracing_today": [
    "apply_optional_llm_synthesis",
    "Any path that injects llm_generate without routing through exploration_llm_call"
  ]
}
```

---

## Step 1b — Single LLM entry contract (MANDATORY)

### Problem

- Multiple code paths → `call_reasoning_model` + optional injected `llm_generate_fn` + synthesis **without** the same generation lifecycle breaks:
  - **Token attribution** (`get_last_chat_usage` ordering / wrong generation)
  - **Trace consistency** (missing or orphan generations)

### Fix (minimal, strict)

**All** exploration LLM invocations **MUST** go through **one** function (name TBD; e.g. `exploration_llm_call` in `agent_v2/observability/` or `agent_v2/exploration/`):

1. Accept: `prompt` (or system+user), `prompt_registry_key`, `generation_name`, Langfuse parents, `task_name`, optional `model_name`.
2. **`clear_last_chat_usage`** (or equivalent ordering) before invoke — consistent with `try_langfuse_generation` behavior.
3. **`try_langfuse_generation(..., input=langfuse_generation_input_with_prompt(prompt, extra={..., "prompt_registry_key": <mandatory>}))`**
4. **`call_reasoning_model`** (production) — injected callables in tests **must** still be invoked **inside** this wrapper so generations remain countable, **or** tests use a **no-op Langfuse** facade with the same call count.

**Forbidden in exploration code (after refactor):**

- Direct `call_reasoning_model` outside the wrapper.
- Direct `llm_generate_fn(...)` from synthesizer without wrapping.

**Injected `llm_generate_fn`:** Either wrap at **ExplorationRunner** construction so the engine always receives `wrapped(fn)`, or document that **harness** must pass a function that internally calls `exploration_llm_call`.

---

## Step 2 — Target tracing model

### Hierarchy (one trace per exploration run)

- **Root trace:** Prefer upstream planner/task trace. **If `langfuse_trace is None`:** exploration **creates a fallback trace** (e.g. via `langfuse_client` / SDK `trace()`), then attaches `exploration` span. **Never** run “silent” with zero trace when Langfuse is enabled.
- **Single parent span for the engine run:** `exploration` (under that trace).

### Child spans (stages)

| Stage | Span name | Type |
|-------|-----------|------|
| Parse intent | `exploration.query_intent` (+ retry) | Span; LLM → **generation** (via wrapper) |
| Select | `exploration.select` | Span; LLM → **generation** |
| Scope | `exploration.scope` | Span; LLM → **generation** |
| Inspect / read | `exploration.inspect` | **Span only** (non-LLM) |
| Analyze | `exploration.analyze` | Span; LLM → **generation** |
| Expand / discover | `exploration.expand` / `exploration.discovery` | Spans; tool summaries |
| **Adapter** | *(none)* | **Do not instrument** |
| Synthesis | `exploration.synthesis` | **Child of `exploration` span**; LLM → **generation** with metadata below |

### Synthesis lineage (MANDATORY)

Synthesis runs **after** the adapter materializes planner-facing output; it must **not** look detached in Langfuse:

- Parent: **`exploration`** span (same as main loop), or a dedicated child span **`exploration.synthesis`** whose parent is **`exploration`**.
- **Generation** `metadata` (or `input.extra`) **must** include:
  - `stage: "synthesis"`
  - `input_source: "adapter_output"`

This preserves **lineage**: synthesis consumes **adapter output**, not raw engine internals.

**Generations** are **only** for LLM calls.

---

## Step 3 — Instrumentation strategy

### A. Span-level (non-LLM)

- Bounded inputs/outputs as today; iteration index on hot loops where useful.
- **No** adapter span. **No** working-memory spans/events.

### B. Generation-level (LLM only)

Every generation **must** include in `input` / `extra`:

| Field | Required |
|-------|----------|
| `prompt_registry_key` | **Yes** — e.g. `exploration.analyzer`, `exploration.selector.batch` |
| Truncated prompt | Yes (existing helper) |
| `langfuse_generation_end_with_usage` | Yes |

Synthesis additionally: `stage`, `input_source` as in Step 2.

---

## Step 4 — Integration points (exact code locations)

| Location | Action |
|----------|--------|
| **New** `exploration_llm_call` (or equivalent) | Centralize wrapper; used by intent, selector, scoper, analyzer, synthesis. |
| `exploration_engine_v2.py` — `explore` | If `langfuse_trace is None` → **create fallback trace**; then create `exploration` span. |
| `explore` → `apply_optional_llm_synthesis` | Pass **`exploration_outer`**; child span **`exploration.synthesis`**; generation metadata **`stage`**, **`input_source`**. |
| `exploration_llm_synthesizer.py` | Only LLM via **`exploration_llm_call`**; no direct `call_reasoning_model`. |
| `ExplorationRunner` | Wire injected LLM through wrapper **or** document harness contract. |
| `query_intent_parser.py`, `candidate_selector.py`, `exploration_scoper.py`, `understanding_analyzer.py` | Replace ad-hoc sequences with **`exploration_llm_call`** + mandatory **`prompt_registry_key`**. |
| **`exploration_result_adapter.py`** | **No change** — no spans. |
| **Working memory** | **No change** — no instrumentation. |
| **Tests** | **`test_generation_count_matches_llm_calls`** (or similar): for a fixed scenario, assert **count(LLM invocations via wrapper)** == **count(generations)** recorded (or mock observer). |

---

## Step 5 — Token + cost tracking design

- **Phase 1 (mandatory):** Single wrapper serializes **exploration** LLM calls per run **from the same thread**; each call: clear usage → model → end generation with **`get_last_chat_usage()`**.
- **Phase 2 (tech debt):** Pass **explicit usage dict** from `call_reasoning_model` return value into `gen.end` to remove global last-usage hazard under concurrency.

---

## Step 6 — Prompt capture

- **Mandatory:** `prompt_registry_key` on **every** exploration generation — required for debugging and **prompt version** correlation.
- Truncation policy unchanged (`LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS`).

---

## Step 7 — Minimal design constraints

- **No** adapter instrumentation.
- **No** working-memory instrumentation.
- **No** huge payloads.
- **Fallback trace** when upstream omits trace — avoids **silent** loss of observability.
- **Non-blocking:** Langfuse SDK batch/async; try/except on end calls.

---

## Step 8 — Generation count sanity (MANDATORY)

**Invariant:** For a given exploration run configuration, the number of **LLM calls executed through `exploration_llm_call`** must equal the number of **Langfuse generations** created for exploration (mock or real client).

**Why:** Catches silent missing instrumentation, broken wrappers, and synthesis bypass.

**How (examples):**

- Unit/integration test with **mock** Langfuse parent that records `generation(...)` invocations.
- Or: wrapper increments a **thread-local** `exploration_generation_count` cleared per run, compared to expected N for a **stubbed** pipeline step list.

**Not sufficient:** Manual UI spot-check alone.

---

## Step 9 — Plan JSON (updated)

```json
{
  "current_gaps": [
    "Multiple LLM entry points without single wrapper",
    "Synthesis not nested + tagged",
    "No fallback trace when langfuse_trace is None",
    "prompt_registry_key not universal",
    "No automated generation-count invariant"
  ],
  "target_tracing_architecture": {
    "root_trace": "Upstream OR fallback created in explore() if missing",
    "exploration_parent_span": "exploration",
    "stage_spans": [
      "exploration.query_intent",
      "exploration.query_intent.retry",
      "exploration.select",
      "exploration.scope",
      "exploration.inspect",
      "exploration.analyze",
      "exploration.discovery",
      "exploration.expand",
      "exploration.synthesis"
    ],
    "explicitly_no_spans": ["adapter", "working_memory"],
    "generations_only_on": [
      "All paths through exploration_llm_call"
    ],
    "synthesis_metadata": {
      "stage": "synthesis",
      "input_source": "adapter_output"
    }
  },
  "single_llm_wrapper_contract": {
    "name": "exploration_llm_call (or equivalent)",
    "flow": "try_langfuse_generation → call_reasoning_model → langfuse_generation_end_with_usage",
    "forbidden": ["direct call_reasoning_model in exploration modules", "unwrapped synthesis"]
  },
  "llm_instrumentation_strategy": {
    "prompt_registry_key": "MANDATORY on every generation extra",
    "wrapper_only": true
  },
  "token_tracking_plan": {
    "phase_1": "Single wrapper + ordering guarantees per run",
    "phase_2": "Explicit usage dict from model response"
  },
  "implementation_plan": [
    "1. Implement exploration_llm_call with mandatory prompt_registry_key and Langfuse generation lifecycle.",
    "2. Add fallback trace creation in ExplorationEngineV2.explore when langfuse_trace is None.",
    "3. Refactor QueryIntentParser, CandidateSelector, ExplorationScoper, UnderstandingAnalyzer to use wrapper only.",
    "4. Refactor apply_optional_llm_synthesis: child of exploration span; metadata stage + input_source; use wrapper only.",
    "5. Wire ExplorationRunner injected LLM through wrapper or enforce harness contract.",
    "6. Add automated test: LLM call count == generation count for representative exploration run.",
    "7. Do NOT add adapter or working-memory spans."
  ]
}
```

---

## Step 10 — Non-negotiables checklist

| Requirement | Policy |
|-------------|--------|
| Single trace per exploration run | Upstream **or** **fallback** trace — never silent |
| All LLM calls observable | **Only** via **`exploration_llm_call`** |
| Token usage captured | `langfuse_generation_end_with_usage` after each wrapped call |
| Prompts + versioning | **`prompt_registry_key` mandatory** on every generation |
| Synthesis lineage | Child of **`exploration`**; **`stage`**, **`input_source`** |
| Generation count | **Automated invariant** test |
| Adapter / memory | **Not instrumented** |
| Keep system fast | Non-blocking Langfuse; no extra WM spans |

---

## References (external)

- Langfuse: traces, spans, generations.
- Token/cost: attach usage to generations; model id for cost.

---

## Document history

| Date | Change |
|------|--------|
| 2026-03-27 | Initial audit + plan (design only) |
| 2026-03-27 | Converged requirements: single LLM wrapper, synthesis tags, fallback trace, generation-count invariant, mandatory prompt_registry_key, no adapter/WM instrumentation |
