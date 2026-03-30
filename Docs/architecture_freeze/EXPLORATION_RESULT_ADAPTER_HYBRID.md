# Hybrid Exploration Result Adapter (Design Only)

**Status:** Implemented (deterministic adapter + optional LLM synthesis; flag `AGENT_V2_ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS`, default off)  
**Related:** [EXPLORATION_WORKING_MEMORY_DESIGN.md](./EXPLORATION_WORKING_MEMORY_DESIGN.md), `agent_v2/exploration/exploration_engine_v2.py`, `agent_v2/exploration/exploration_working_memory.py`, `agent_v2/schemas/exploration.py`

---

## Purpose

Introduce a **layered adapter** that keeps the exploration pipeline **deterministic and memory-grounded**, while optionally adding **LLM-produced synthesis** (insights, optional coverage narrative) for planner consumption—without letting the model control schema, evidence, or structural fields.

---

## System boundary (contract)

| Type | Role |
|------|------|
| **`FinalExplorationSchema`** | **The planner-facing contract.** `ExplorationEngineV2.explore()` and `ExplorationRunner.run()` return this. Downstream stages (planner, mode manager, prompts) consume **`evidence`**, **`exploration_summary`**, **`relationships`**, **`metadata`**, **`key_insights`**, etc. |
| **`ExplorationResult`** | **Internal / legacy only** — historical Schema 4 bundle (`items` + `summary`). It is **not** the integration surface. It may appear only inside `agent_v2/exploration` (adapter glue) and the pre-V2 runner builder before `final_from_legacy_phase3_exploration_result` wraps it. **Do not** pass `ExplorationResult` into planner code paths. |

**LLM observability:** `trace.llm_used` and `trace.synthesis_success` are sufficient for now; do not add richer logging fields until there is a concrete need.

---

## Converged constraints (MANDATORY)

These six rules fix the main **drift / duplication** risks. Architecture elsewhere stays as previously designed (hybrid split, optional LLM, no full memory to LLM, safety fallback).

| # | Constraint | Rule |
|---|------------|------|
| 1 | **Single mapping path** | Exactly **one** deterministic transform: `WorkingMemory` → `FinalExplorationSchema` (via adapter). No parallel `memory → items` implementations. |
| 2 | **Relationships source** | `relationships` in the final schema come **only** from `memory.get_summary()["relationships"]`. **Never** from `summary.key_findings`, `overall`, or any derived prose. |
| 3 | **LLM input contract** | LLM bundle uses **`memory.get_summary()["evidence"]` as already ordered** (sorted in `get_summary()`). Apply **K cap only after** that ordering—no re-rank, re-select, or ad-hoc filter. |
| 4 | **Confidence semantics** | `confidence` is a **discrete, deterministic** projection of exploration/analyzer signals (e.g. sufficient → high, partial → medium, else low)—**not** a noisy float like an unconstrained mean. |
| 5 | **Trace shape** | `trace` is **minimal and structured** (fixed keys); no arbitrary log dumps. |
| 6 | **Evidence representation** | `FinalExplorationSchema.evidence` is **`list[ExplorationItem]`** (Schema 4 item type)—**strict projection**, no parallel DTO. |

---

## Step 1 — Audit: Current Output Path

### Code references

| Component | Role |
|-----------|------|
| `ExplorationWorkingMemory.get_summary()` | Returns `evidence` (capped by `max_evidence`), full `relationships`, full `gaps`; evidence sorted by tier then order. |
| `ExplorationEngineV2._build_result_from_memory(...)` | Today: maps snapshot → `ExplorationResult` (Schema 4). **At implementation time this must collapse into the single adapter path** (see below). |

### Deterministic vs missing layers (`deterministic_vs_missing_layers`)

| Layer | Deterministic today? | Notes |
|-------|-------------------|--------|
| Evidence → `ExplorationItem` | Yes | File ref, summary snippet cap, relevance score heuristic, `key_points` = single summary string, `tool_name`, `read_source`. |
| Relationships | Partially in summary | `summary.key_findings` may append a **string** about edge count; **structured relationship list is not** a first-class field on `ExplorationResult`. **Final schema fixes this; prose must not substitute for the graph.** |
| Gaps | Yes | `knowledge_gaps` from memory gap descriptions; `knowledge_gaps_empty_reason` when empty. |
| Status / termination | Yes | `completion_status`, `termination_reason`, `explored_files/symbols` from engine. |
| **Semantic compression** | **Weak / template-like** | `overall` is a short template; `key_findings` are mostly first evidence summaries + optional relationship blurb—**no cross-evidence synthesis**, no explicit “what we learned vs what’s still open” narrative. |
| **Objective coverage** | **Missing** | No explicit “how well we addressed the instruction” field beyond implicit evidence list. |

**Conclusion:** Structural mapping from memory is **correct and validated**. The **gap** is **planner-facing narrative quality** and **first-class relationships + single projection**—without duplicate transforms.

---

## Step 2 — Hybrid Adapter Architecture

### Single source of truth boundary (CRITICAL)

**Forbidden:** Two independent pipelines:

- `memory → _build_result_from_memory` **and**
- `memory → adapter → FinalExplorationSchema`

**Required:** **One** canonical mapping:

```text
WorkingMemory
   ↓
ExplorationResultAdapter.build(...)   ← ONLY place that maps memory → structured outputs
   ↓
FinalExplorationSchema (+ optional LLM fields)
   ↓
ExplorationResult (Schema 4)          ← thin assembly FROM adapter output, not a second remap
```

**`_build_result_from_memory` policy (pick one at implementation; both satisfy “single path”):**

- **Preferred:** Implement mapping **only** inside `ExplorationResultAdapter`; `_build_result_from_memory` **delegates** to the adapter and then wraps `ExplorationResult` (same `ExplorationItem` instances as `final.evidence`).
- **Alternative:** Deprecate `_build_result_from_memory` and call the adapter directly from `_explore_inner`.

In all cases, **`ExplorationResult.items` MUST be the same objects (or field-identical projections) as `FinalExplorationSchema.evidence`**—built once.

```text
WorkingMemory (unchanged)
   ↓
ExplorationResultAdapter (deterministic; sole mapper)
   ↓
Optional LLM Synthesis Layer (bounded, flag-controlled)
   ↓
FinalExplorationSchema   ← planner-facing
```

### Responsibilities

| Layer | Must |
|-------|------|
| **ExplorationResultAdapter** | Sole owner of memory → `ExplorationItem` list, gaps, relationships (from snapshot), status, summary templates for Schema 4, and `FinalExplorationSchema` base fields. |
| **LLM Synthesis** | **Only** generate `key_insights` (and optionally `objective_coverage`); **never** mutate evidence, relationships, gaps, or structural counts. |
| **FinalExplorationSchema** | **`evidence: list[ExplorationItem]`** plus relationships from snapshot only; validate strictly. |

### Relationships (IMPORTANT)

- **Canonical graph:** `memory.get_summary()["relationships"]` only.
- **Do not** infer or duplicate relationship information from `ExplorationSummary.key_findings` or `overall` for structured fields. (Legacy `key_findings` may still mention edges for human-readable Schema 4 parity only if desired, but **FinalExplorationSchema.relationships** must not be populated from that text.)

### Constraints (unchanged from product)

- Do **not** modify `ExplorationWorkingMemory` in the adapter path.
- Do **not** pass full memory or raw dumps to the LLM (capped summaries only).

---

## Step 3 — `FinalExplorationSchema` (Pydantic, planner-facing)

### Evidence: single representation (MANDATORY)

- **`evidence: list[ExplorationItem]`** — strict projection from `memory.get_summary()` through the **same** mapping logic that produces Schema 4 items. **No** parallel “evidence DTO” or subset shape.

### Other fields

| Field | Type | Source |
|-------|------|--------|
| `status` | `Literal["complete", "incomplete"]` | Deterministic (from engine metadata) |
| `objective_coverage` | `str \| None` | Optional LLM; **null** when LLM off or failed |
| `evidence` | `list[ExplorationItem]` | **Only** via adapter from memory snapshot (see Step 2) |
| `relationships` | List of `{from, to, type, confidence?}` (aligned with memory rows) | **`memory.get_summary()["relationships"]` only** |
| `gaps` | As per policy (e.g. descriptions or typed rows) | Deterministic from `memory.get_summary()["gaps"]` |
| `key_insights` | `list[str]` max 2–4 | LLM when enabled; else deterministic fallback (see Step 6) |
| `confidence` | **Discrete** band, e.g. `Literal["high", "medium", "low"]` | **Deterministic projection** from exploration completion / analyzer sufficiency signals (see below)—not an unconstrained float mean |
| `trace` | **Fixed minimal struct** (see below) | Adapter + synthesizer only |

### Confidence semantics (STABLE)

- **Do not** use “mean of item confidences” as the primary definition (unstable, hard for planner to interpret).
- **Do** define a **small enumerated mapping** from existing engine/analyzer signals, e.g.:
  - **high:** exploration marked sufficient / complete understanding where applicable
  - **medium:** partial coverage or incomplete with actionable path
  - **low:** stalled, no evidence, or policy-limited read
- Exact predicates are implemented once next to `ExplorationEngineV2` termination / metadata; **documented in code** so tests lock behavior.

### Trace (STRUCTURED, minimal)

**Required shape** (extend only with version bumps):

```json
{
  "llm_used": true,
  "synthesis_success": true,
  "adapter_version": "v1"
}
```

- **No** arbitrary log blobs; **no** dumping stack traces or full prompts here.
- On LLM off: `llm_used: false`, `synthesis_success: false` or omit synthesis-specific meaning per spec.
- On LLM on but failure: `llm_used: true`, `synthesis_success: false`.

### Relationship to Schema 4

- **`ExplorationResult`** remains the **backward-compatible** bundle for existing callers.
- Assembly rule: **`ExplorationResult.items == FinalExplorationSchema.evidence`** (same list or immutable copy policy—document at implementation). Summary/metadata for `ExplorationResult` are filled from the same adapter pass (no second mapping).

---

## Step 4 — LLM Synthesis Design (CRITICAL)

### Inputs (bounded, DETERMINISTIC ORDER)

1. **Evidence for LLM:** Take **`memory.get_summary()["evidence"]`** exactly as returned (already sorted in `get_summary()`). **Then** apply:
   - **cap K** (e.g. K ≤ 6) **after** that order—**first K rows** of the snapshot list.
   - Extract only **summary strings** (and optionally symbol/file labels) for the prompt—**no** re-ranking, **no** “top by score” rescoring.

2. **Relationships for LLM:** Derive compressed text **from** `memory.get_summary()["relationships"]` only (e.g. count by type + sample edges)—not from summary prose.

3. **Gaps:** From `memory.get_summary()["gaps"]` (capped).

### Outputs (strict JSON or tool schema)

- `key_insights`: 2–4 strings, each ≤ N chars.
- `objective_coverage`: optional single paragraph, capped.

### Prompt properties

- Small system + user message; **no** code snippets, **no** full item dumps.
- Temperature low; optional structured output.

### Failure behavior

- On timeout, parse error, or policy violation: **omit LLM fields** and use deterministic fallback; **never** fail the exploration run. Set `trace.synthesis_success` accordingly.

---

## Step 5 — Implementation Plan (future work)

1. **`ExplorationResultAdapter` (deterministic; sole mapper)**  
   - Implements **the only** `WorkingMemory` → `ExplorationItem` + relationships + gaps + summary/metadata projection.  
   - Exposes `build_final(...) -> FinalExplorationSchema` (without LLM) and helpers to wrap **`ExplorationResult`** from that output.

2. **`_build_result_from_memory`**  
   - **Delegates** to `ExplorationResultAdapter` (or is removed in favor of a single call site). **No** duplicate field-by-field mapping in the engine.

3. **`ExplorationLLMSynthesizer` (optional)**  
   - Input bundle built per Step 4 (deterministic order).  
   - Output: `key_insights`, optional `objective_coverage`.  
   - Uses **model router** — no direct vendor SDK in business logic.

4. **`ExplorationEngineV2` integration**  
   - One call: adapter → `FinalExplorationSchema`; optional synthesizer patch; then expose `ExplorationResult` + optional `FinalExplorationSchema` per API.  
   - Default: **no LLM**, deterministic trace.

---

## Step 6 — Safety + Determinism

| Requirement | Approach |
|-------------|----------|
| LLM optional | Feature flag (e.g. `ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS`). |
| Fallback | Deterministic-only; `key_insights` from a **fixed** policy (e.g. first N `ExplorationSummary.key_findings` equivalents computed in adapter—**not** ad hoc). |
| Schema always valid | Pydantic validation; LLM output validated and truncated; invalid → discard LLM fields; `trace.synthesis_success = false`. |
| No WorkingMemory mutation | Adapter reads snapshots only. |
| Single mapping | Enforced by code review + test that **`ExplorationResult.items` matches `FinalExplorationSchema.evidence`**. |

---

## Step 7 — Tests (MANDATORY when implemented)

1. **Deterministic-only path** — flag off; `FinalExplorationSchema` validates; **evidence list identity** matches `ExplorationResult.items`; relationships match `memory.get_summary()["relationships"]`.  
2. **Hybrid path** — mock model router; `key_insights` populated; **evidence/gaps/relationships** unchanged vs deterministic baseline.  
3. **LLM failure fallback** — mock raises; schema valid; trace reflects failure; no drift in factual fields.  
4. **Strict schema validation** — `model_validate` on round-trip dumps.  
5. **Single-path regression** — assert **no** second code path maps memory → items (e.g. grep / architectural test as appropriate).

---

## Step 8 — Non-Negotiables

- **Do not** let the LLM control schema shape or field names at runtime (fixed parser → Pydantic).  
- **Do not** pass full memory to the LLM.  
- **Do not** mutate `ExplorationWorkingMemory`.  
- **Do not** break **`ExplorationResult`** consumers; **`items` must equal `FinalExplorationSchema.evidence`**.  
- **Do not** introduce a second evidence DTO or duplicate mapping from memory to items.  
- **Do not** populate structured `relationships` from summary text.  
- Keep adapter **modular** (deterministic adapter vs synthesizer).

---

## Step 9 — Deliverables (when implemented)

1. This design doc (this file).  
2. Implementation: single adapter + optional synthesizer + `FinalExplorationSchema`.  
3. Tests listed in Step 7.  
4. **Example output** (in `Docs/` or `tests/fixtures/`): deterministic-only vs hybrid.

---

## Example: Deterministic vs Hybrid (illustrative)

**Deterministic `key_insights` fallback** uses a **fixed** rule (e.g. first three evidence summaries or adapter-computed bullets)—same inputs as today’s `key_findings` intent, **without** a second code path.

**Hybrid** adds 2–4 synthesized bullets while **evidence list, relationships, and gaps** stay **byte-for-byte identical** to the deterministic adapter output.

---

## What stays correct (do not roll back)

- Hybrid split (deterministic core + optional synthesis).  
- LLM optional with safe fallback.  
- No full working memory in the LLM prompt.  
- Adapter as the abstraction boundary.  
- Safety and schema validity on LLM failure.

---

## Changelog

| Date | Change |
|------|--------|
| 2026-03-27 | Initial design (planned only). |
| 2026-03-27 | Converged constraints: single mapping path, relationships from memory only, deterministic LLM input order, discrete confidence, structured trace, `evidence` = `list[ExplorationItem]`; `_build_result_from_memory` must delegate or deprecate. |
| 2026-03-27 | Implementation: `ExplorationResultAdapter` in `agent_v2/exploration/exploration_result_adapter.py`, `apply_optional_llm_synthesis` in `exploration_llm_synthesizer.py`, `FinalExplorationSchema` in `agent_v2/schemas/final_exploration.py`; engine `last_final_exploration`; tests `tests/test_exploration_result_adapter.py`. |

