# Agent V2 — Retrieval infra audit (scoped)

**Scope:** `agent_v2/exploration/`, `agent_v2/runtime/` (`exploration_runner.py`, `action_generator.py`), `agent_v2/schemas/exploration.py`, retrieval wiring (`dispatcher.search_batch`, `graph_expander`, discovery in `exploration_engine_v2.py`).  
**Date:** 2026-03-30.  
**Method:** Code inspection only; no redesign or recommendations.

---

## JSON artifacts (requested)

```json id="1a2b3c"
{
  "identifier_anchors_present": true,
  "where_used": [
    "agent_v2/schemas/exploration.py — ExplorationState.explored_location_keys (tuple canonical path + symbol), ExplorationTarget/ExplorationCandidate/ReadPacket file_path + symbol",
    "agent_v2/exploration/exploration_engine_v2.py — _canonical_path, _make_location_key, _evidence_delta_key(canonical_path, symbol, read_source)"
  ],
  "how_used": "Canonical resolved paths pair with optional symbol strings for dedupe, queue membership, and evidence keys; discovery merges rows using _canonical_path as file key."
}
```

```json id="2b3c4d"
{
  "translation_layer_present": true,
  "entry_points": [
    "agent_v2/exploration/query_intent_parser.py — QueryIntentParser.parse() → QueryIntent",
    "agent_v2/schemas/exploration.py — QueryIntent (intent_type, symbols, keywords, regex_patterns, …)"
  ],
  "notes": "No identifier \"ES3\" appears under agent_v2. Instruction → structured QueryIntent is implemented via QueryIntentParser and registry key exploration.query_intent_parser; _coerce_for_query_intent maps aliases (symbol_queries/text_queries) to symbols/keywords."
}
```

```json id="3c4d5e"
{
  "symbol_index_present": "implicit",
  "source": [
    "agent_v2/exploration/file_symbol_outline.py — load_python_file_outline() via repo_index.parser / symbol_extractor (per-file, on demand)",
    "agent_v2/exploration/exploration_engine_v2.py — _symbol_outline_cache, _outline_full_for_file",
    "ExplorationState.seen_symbols / expanded_symbols / graph batch: graph_symbol_edges_batch.fetch_callers_callees_batch backed by configured SQLite graph (see config + graph_symbol_edges_batch)"
  ],
  "how_accessed": "Outlines loaded per canonical file path for selector batch; graph edges fetched by (canonical file, symbol name) batch; no standalone global symbol dictionary module in this scope."
}
```

```json id="4d5e6f"
{
  "vector": true,
  "keyword": true,
  "graph": true,
  "integration_point": "agent_v2/exploration/exploration_engine_v2.py — _discovery() runs parallel symbol/regex/text channels; _discovery_query_channel_to_source maps text→vector, regex→grep, symbol→graph labels on ExplorationCandidate; Dispatcher.search_batch() executes SEARCH/search_multi (docstring references vector retriever batch). Graph expansion: agent_v2/exploration/graph_expander.py — fetch_graph from agent.retrieval.adapters.graph; analyzer relationships: graph_symbol_edges_batch + SQLite."
}
```

```json id="5e6f7g"
{
  "jit_retrieval": true,
  "where_triggered": [
    "exploration_engine_v2._explore_inner — initial _run_discovery_traced after QueryIntentParser.parse",
    "Same file — intent_bootstrap_pass loop: re-parse + _run_discovery_traced when pending_targets empty and coverage weak",
    "Same file — control.action == refine: re-parse + _run_discovery_traced(refine_phase=True)",
    "Per-target inspection: InspectionReader.inspect_packet / read_snippet (bounded), not whole-repo load"
  ],
  "mechanism": "Step-bounded main loop with on-demand discovery batches and bounded file reads; no engine-level preload of entire repository."
}
```

---

## Output block `6f7g8h` — Findings per category (1–5)

### 1. Identifier anchors

| Capability | Yes / no | Evidence |
|------------|----------|----------|
| `file_path` as anchor | Yes | `ExplorationCandidate.file_path`, `ExplorationTarget.file_path`, `ReadPacket.file_path`, discovery row keys `file` / `file_path` in `_discovery` ingest (`exploration_engine_v2.py`). |
| `symbol` as anchor | Yes | `ExplorationTarget.symbol`, `ReadPacket.symbol`, `ExplorationState.explored_location_keys` pairs with symbol string (`schemas/exploration.py`, `_make_location_key`). |
| `canonical_path` as anchor | Yes | `_canonical_path` and use in `file_merge`, `_make_location_key`, `_evidence_delta_key` (`exploration_engine_v2.py`). |

### 2. Lightweight translation layer (ES3-like)

| Capability | Yes / no | Evidence |
|------------|----------|----------|
| Instruction → structured queries | Yes | `QueryIntentParser.parse` → `QueryIntent` (`query_intent_parser.py`). |
| Separates `intent_type`, symbols, keywords | Yes | `QueryIntent` fields `intent_type`, `symbols`, `keywords`, `regex_patterns` (`schemas/exploration.py`). |
| Named “ES3” | No | `ES3` string not present under `agent_v2` (grep). |

### 3. Symbol index / dictionary

| Kind | Evidence |
|------|----------|
| Explicit global registry in-scope | Not present as a dedicated registry module. |
| Implicit | Per-file outlines (`file_symbol_outline.py`); state sets `seen_symbols` / `expanded_symbols`; SQLite graph lookups (`graph_symbol_edges_batch`, `graph_expander.fetch_graph`). |

### 4. Hybrid retrieval

| Mode | Present | Evidence |
|------|---------|----------|
| Vector-oriented text channel | Yes | `_discovery_query_channel_to_source`: `text` → `"vector"`; `_collect_pairs(..., "text", text_queries)`; `Dispatcher.search_batch` docstring references vector batch. |
| Keyword / grep-style | Yes | `regex` channel → `"grep"`; intent `keywords` feed `text_queries` (natural-language/text channel). |
| Graph | Yes | Symbol channel labeled `"graph"` on candidates; `GraphExpander.expand` uses `fetch_graph`; relationships block uses `fetch_callers_callees_batch`. |

**Integration:** `_discovery` merges by `_canonical_path` and combines `source_channels` on `ExplorationCandidate` (`exploration_engine_v2.py`).

### 5. Just-in-time retrieval

| Capability | Yes / no | Evidence |
|------------|----------|----------|
| Additional fetch when gaps / loop | Yes | Refine path and intent bootstrap re-run discovery; expand path runs graph expansion (`_run_discovery_traced` at ~451, ~542, ~948). |
| Avoids whole-repo preload in engine | Yes | Bounded steps `EXPLORATION_MAX_STEPS`, read policy `read_snippet`, discovery caps (`DISCOVERY_*_CAP`); no bulk indexing call in `_explore_inner`. |

### Runtime files in scope

- **`action_generator.py`:** Delegates `next_action_exploration` to injected callable; **no retrieval implementation**.
- **`exploration_runner.py`:** Wires `ExplorationEngineV2`, `QueryIntentParser`, scoper/selector, `GraphExpander`, `Dispatcher`-backed execution; retrieval behavior lives in engine + dispatcher.

---

## Yes/no summary

| # | Question | Answer |
|---|----------|--------|
| 1 | Identifier anchors (`file_path`, `symbol`, `canonical_path`) used as primary anchors | **Yes** |
| 2 | Translation layer (ES3 or similar): instruction → structured queries; `intent_type` + symbols + keywords | **Yes** (not named ES3) |
| 3 | Symbol index | **Implicit** (outlines + graph + state), not a single explicit global registry in-scope |
| 4 | Hybrid: vector + keyword/grep + graph | **Yes** (three discovery channels + graph expansion; schema labels `graph`/`grep`/`vector`) |
| 5 | JIT retrieval (on demand, not full preload) | **Yes** |
