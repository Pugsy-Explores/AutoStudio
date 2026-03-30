# Exploration Working Memory — Design (Schema + API + Integration)

**Status:** Implemented (`agent_v2/exploration/exploration_working_memory.py`, wired in `exploration_engine_v2.py`; tests in `tests/test_exploration_working_memory.py`).  
**Scope:** Exploration stage only — per `ExplorationEngineV2.explore` run, ephemeral, no persistence.

**Constraints (non-negotiable):** No new heavy abstractions, no database, no vector search, no prompt changes; must integrate cleanly into the existing loop. Bounded inspection snippets may be stored in working memory as facts (≤ `MAX_SNIPPET_CHARS`). Relationship endpoints use **file + symbol** disambiguation (e.g. `'<file>::<symbol>'` or `'<file>::<__file__>'` when symbol is absent).

### Converged plan (minimal deltas)

1. **Memory = single source of truth for Schema 4 output** — `_build_result` must not mix memory with the legacy `evidence` tuple list; pick one path only (see §8).
2. **Minimal gap filtering** — drop duplicates and obviously generic gaps (e.g. “need more context”); reuse the same spirit as existing engine gap filters; no extra heuristics.

**Leave unchanged for this iteration:** evidence/relationship/gap schema shape, relationship **vocabulary** (`callers` / `callees` / `related` only), snippet handling, compaction/merge rules.

---

## 1. Current state vs memory gap (audit)

### Runtime state in `ExplorationEngineV2` / `ExplorationState`

| Area | Role today | Reusable as “memory”? |
|------|------------|-------------------------|
| `seen_files`, `seen_symbols` | Traversal dedupe, enqueue/skip, scoring | **No** as factual knowledge; yes as **dedupe signals** only |
| `explored_location_keys`, `excluded_paths` | Avoid duplicate visits / wrong file scope | Control-plane only |
| `attempted_gaps`, `attempted_gap_targets` | Gap-driven expansion policy (no repeat) | **Promote** structured gaps with `type` + confidence; raw sets are not planner-facing |
| `evidence` (`list[tuple[phase, payload, ExecutionResult]]`) | Feeds `_build_result` → Schema 4 items | **Gap:** unstructured; final output should come from **normalized** evidence + relationships |
| `evidence_keys_seen` + `_evidence_delta_key` | Skip analyzer when no new (file, symbol, read_source) | Overlaps with memory dedupe; can align keys |
| `relationships_found` | Boolean for completion / `should_stop` | **Gap:** no stored **edges**; working memory should hold `relationships[]` |
| Queue / counters (`pending_targets`, `steps_taken`, utility streaks, etc.) | Loop control | Not promoted to memory |

### What should be promoted to working memory

- **Evidence:** (symbol, file, line_range, summary) + optional bounded snippet/read_source + confidence + source  
- **Gaps:** (type, description) + confidence + source (from analyzer gaps, with existing category rules where applicable), after **minimal** duplicate/generic filtering (see §7 Gap quality)  
- **Relationships:** (from, to, type) with file+symbol keys, from expansion buckets — **must stay** `callers` / `callees` / `related` to match `GraphExpander` output (no `calls` / `called_by` rename, no translation layer)

---

## 2. Working memory scope (strict)

- **Per exploration run** — one instance per `explore(...)` call  
- **Ephemeral** — dropped when the run ends  
- **Operations:** write (store facts), `get_summary()` for the output builder, compact (avoid bloat). **`query()` is not required** while memory is not used for intra-loop reasoning — omit, or leave a no-op stub for a future phase.

---

## 3. Memory schema (minimal structured)

### Evidence

| Field | Type | Notes |
|-------|------|--------|
| `symbol` | `string \| null` | |
| `file` | `string` | Canonical path recommended |
| `line_range` | `{ start: int, end: int }` | `start >= 1`, `end >= start` |
| `summary` | `string` | Derived fact, not raw context — **source rule (soft):** prefer **analyzer** (`UnderstandingResult.summary`); if empty/missing, **fallback** to inspection tool summary (`ExecutionResult.output.summary`); expansion-phase rows may use expansion summary when relevant. **No hard rejection** when the primary analyzer string is absent. |
| `snippet` | `string` (optional) | Bounded to `MAX_SNIPPET_CHARS` |
| `read_source` | `'symbol' \| 'line' \| 'head' \| null` | System-owned |
| `metadata` | `{ confidence: float, source: 'inspection' \| 'analyzer' \| 'discovery' \| 'expansion' }` | |

### Relationships

| Field | Type | Notes |
|-------|------|--------|
| `from` | `string` | File+symbol key |
| `to` | `string` | File+symbol key |
| `type` | `'callers' \| 'callees' \| 'related'` | **Frozen:** same literals as `GraphExpander` / `expand_result.output.data` buckets — do **not** rename to `calls` / `called_by` (avoids inconsistency and a translation layer). |
| `metadata` | `{ confidence: float, source: 'expansion' }` | |

### Gaps

| Field | Type | Notes |
|-------|------|--------|
| `type` | `string` | e.g. caller, callee, definition, config, usage, flow, none |
| `description` | `string` | Trimmed gap text |
| `metadata` | `{ confidence: float, source: 'analyzer' }` | |

**Do not store:** raw full-file content, unbounded context, duplicate entries (enforce dedupe).

---

## 4. Memory API (minimal, deterministic)

| Method | Signature (conceptual) | Behavior |
|--------|-------------------------|----------|
| `add_evidence` | `add_evidence(symbol, file, line_range, summary, *, snippet=None, read_source=None, confidence, source) -> EvidenceKey` | Dedupe by (file, symbol) |
| `add_relationship` | `add_relationship(from_key, to_key, rel_type, *, confidence, source) -> RelationshipKey` | `rel_type` ∈ `callers` \| `callees` \| `related` only. Dedupe by (from, to, type). |
| `add_gap` | `add_gap(gap_type, description, *, confidence, source) -> GapKey` | After gap-quality filters (§7); dedupe by (type, normalized description) |
| `get_summary` | `get_summary()` | Full structured snapshot + status hints for final builder |

**`query()`:** Not needed for v1 (memory is not used for reasoning inside the loop). **Do not** add flags like `include_relationships`. Stub or omit entirely until a later phase.

---

## 5. Integration points (exact)

1. **After Analyzer** — Write evidence: line_range / snippet / read_source from inspection; **summary** = analyzer first, inspection summary fallback if missing. Write gaps from `knowledge_gaps` only after minimal gap filter (§7), with `gap_type` from existing engine categorization where applicable.
2. **After Expansion** — Parse `expand_result.output.data` (`callers`, `callees`, `related`); add edges from anchor file_symbol to each neighbor (types unchanged).
3. **Final step** — Build `ExplorationResult` **only** from `memory.get_summary()`. **Critical:** do **not** merge or interleave with the legacy `evidence: list[tuple[...]]` for items/summary — memory is the **single source of truth** for planner-facing output. Legacy tuples may remain for Langfuse/telemetry only if needed, not for `_build_result` content.

---

## 6. Storage design

- **In-memory Python structures:** dicts keyed for dedupe (evidence by `(file, symbol)`, relationships by `(from, to, type)`, gaps by `(type, normalized_description)`).
- **Justification:** Working memory ≠ long-term memory; zero I/O latency; run-scoped and small caps (`EXPLORATION_MAX_ITEMS` for items, small fixed cap for gaps/relationships).

---

## 7. Policies

### Write policy

- Prefer entries with **confidence ≥ MIN_CONFIDENCE** (single deterministic constant, aligned with analyzer confidence scale) where confidence exists; **do not** hard-reject evidence solely because the summary did not come from the analyzer — use the fallback chain in §3 Evidence `summary` notes.
- **Non-duplicate:** reject or merge on dedupe keys (see below).

### Gap quality (minimal only)

Before `add_gap`:

- **Duplicate** — skip (same normalized description already stored).
- **Too generic** — skip strings that match the same class of noise the engine already treats as low-value (e.g. substrings like “need more context”, “insufficient context”, very short strings). **No** additional scoring or ML — mirror the existing “generic gap” spirit in code, not new policy machinery.

### Dedupe

- **Evidence:** by `(file, symbol)` (use `'<file>::<__file__>'` when symbol is null).
- **Relationships:** by `(from, to, type)`.
- **Gaps:** by `(type, normalized_description)` (trimmed, lowercased, bounded length).

### Compaction

- **Merge evidence:** on duplicate key, union `line_range` to `[min(start), max(end)]`, keep summary/snippet from highest-confidence row (tie-break by deterministic order).
- **Drop low-value:** drop below `MIN_CONFIDENCE`; cap counts to match Schema 4 and avoid bloat.

---

## 8. Output builder integration (critical)

### Single source of truth

- **`_build_result` (or equivalent) must read only from working memory** (`get_summary()`), not from the historical `evidence` append-only list.
- **No mixing:** if the run uses working memory for Schema 4, the tuple list must **not** drive `items`, `summary.key_findings`, or gap text. That avoids two divergent truths (the real bug when both exist).
- Telemetry / Langfuse may still log per-phase `ExecutionResult` objects; that is separate from the **normative** exploration artifact.

### Mapping

- **evidence** → Schema 4 `items[]` (inspection-grounded summaries; snippets/read_source when present).
- **relationships** → Represented in summary or key_findings as structured bullets (or future additive metadata only if SCHEMAS.md amended — default: surface via `summary` / `key_findings` without breaking Schema 4 top-level shape).
- **gaps** → `ExplorationSummary.knowledge_gaps` (with `knowledge_gaps_empty_reason` rules unchanged).
- **status** — `metadata.completion_status` / `termination_reason` from **existing** system gates; analyzer sufficiency informs summary text, not replacing `should_stop` / planner gating semantics.

---

## 9. Implementation plan (when executed)

1. Add `ExplorationWorkingMemory` (single module) under `agent_v2/exploration/`.
2. Instantiate in `ExplorationEngineV2._explore_inner`; wire writes at analyzer and expansion sites (summary fallback chain; minimal gap filter on ingest).
3. **Refactor `_build_result`** so Schema 4 content is produced **only** from `memory.get_summary()` — remove or bypass tuple-list-driven item construction for the final artifact; tuples optional for observability only.
4. Tests: unit tests for dedupe, merge, caps, gap generic/duplicate skip; integration test that `ExplorationResult` matches memory-only builder (pipeline ordering, no heuristic ranking).

---

## 10. Machine-readable bundle (for tooling)

```json
{
  "memory_schema": {
    "scope": "Exploration-stage only (per ExplorationEngineV2.explore run), ephemeral, no persistence",
    "evidence": {
      "symbol": "string | null",
      "file": "string (canonical path recommended)",
      "line_range": { "start": "int (>=1)", "end": "int (>=start)" },
      "summary": "string",
      "snippet": "string (optional; bounded to MAX_SNIPPET_CHARS)",
      "read_source": "Literal['symbol','line','head'] | null",
      "metadata": {
        "confidence": "float (0..1)",
        "source": "Literal['inspection','analyzer','discovery','expansion']"
      }
    },
    "relationships": {
      "from": "string (file_symbol key)",
      "to": "string (file_symbol key)",
      "type": "Literal['callers','callees','related']",
      "metadata": { "confidence": "float (0..1)", "source": "Literal['expansion']" }
    },
    "gaps": {
      "type": "string",
      "description": "string",
      "metadata": { "confidence": "float (0..1)", "source": "Literal['analyzer']" }
    }
  },
  "memory_api": {
    "add_evidence": "add_evidence(symbol, file, line_range, summary, *, snippet=None, read_source=None, confidence, source) -> EvidenceKey",
    "add_relationship": "add_relationship(from_key, to_key, rel_type in callers|callees|related, *, confidence, source) -> RelationshipKey",
    "add_gap": "add_gap(gap_type, description, *, confidence, source) -> GapKey",
    "get_summary": "get_summary() -> {evidence:[], relationships:[], gaps:[], status:{...}}",
    "query": "omit or stub in v1 — not used for reasoning yet; no expanded query API"
  },
  "integration_points": [
    "After Analyzer → write findings (analyzer summary preferred; inspection summary fallback)",
    "After Expansion → write relationships (callers|callees|related unchanged)",
    "Final step → build ExplorationResult ONLY from memory.get_summary() — no mixing with legacy evidence tuples"
  ],
  "storage_design": "In-memory dict/list structures; no DB; per-run ephemeral for minimal latency.",
  "policies": {
    "write_policy": "prefer high-confidence; allow summary fallback chain — not analyzer-only hard gate",
    "gap_filter_minimal": "skip duplicate gaps; skip overly generic gap strings (same spirit as engine generic-gap list)",
    "dedupe": "evidence by (file,symbol); relationships by (from,to,type); gaps by (type,normalized_description)",
    "compaction": "unchanged — merge line_ranges on duplicate evidence keys; drop below MIN_CONFIDENCE; enforce caps"
  },
  "implementation_plan": [
    "Add ExplorationWorkingMemory module",
    "Wire into exploration_engine_v2 explore loop with summary fallback + minimal gap filter",
    "_build_result uses ONLY memory — single source of truth; legacy tuples observability-only if kept",
    "Add unit/integration tests"
  ]
}
```
