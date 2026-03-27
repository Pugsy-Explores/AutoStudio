# Exploration: Entity Grounding & Metadata-Rich Candidates (Surgical Plan)

**Scope:** Improve `query → retrieval → scoper` alignment without redesigning exploration, adding services, or file-based heuristics (e.g. “if test then penalize”). **Composable** with the existing `ExplorationEngineV2` loop.

**Problem statement (from logs):** Intent queries skew semantic (“exploration scope layer”) without **code-grounded anchors**; vector retrieval returns **mixed** hits; the scoper sees **thin** evidence (path + snippets) and may prefer **tests or reference-only** files. Failure mode is **selection misalignment**, not loop control.

---

## Step 1 — Audit: Current Query Flow

### 1.1 `QueryIntentParser.parse(...)`

- **Implementation:** `agent_v2/exploration/query_intent_parser.py`
- **Prompt:** `agent/prompt_versions/exploration.query_intent_parser/v1.yaml` (registry key `exploration.query_intent_parser`)
- **Output:** Validated `QueryIntent` (`agent_v2/schemas/exploration.py`): `symbols`, `keywords`, `regex_patterns`, `intents`
- **Coercion:** `_coerce_for_query_intent` maps legacy aliases `symbol_queries` → `symbols`, `text_queries` → `keywords`, `intent` → `intents`

### 1.2 Where `queries` and `symbols` go today

| Field | Role downstream |
|--------|------------------|
| `symbols` | Fed as **symbol** channel queries into `_discovery` → `Dispatcher.search_batch(..., mode="symbol")` → merged with label `source` → `graph` |
| `keywords` | **Text** channel → `mode="text"` → merged as `vector` |
| `regex_patterns` | **Regex** channel → `mode="regex"` → merged as `grep` |
| `intents` | Not search queries; concatenated for **CandidateSelector** (`intent_text` in `_enqueue_ranked`) |

### 1.3 Retrieval path (exploration discovery)

- **Entry:** `ExplorationEngineV2._discovery` in `agent_v2/exploration/exploration_engine_v2.py`
- **Behavior:** Three parallel batches (`symbol`, `regex`, `text`) via `self._dispatcher.search_batch`, results merged by `(canonical_path, symbol_dedupe_key)` with per-channel score breakdown, then `DISCOVERY_MERGE_TOP_K` cut.
- **Important implementation detail:** `agent_v2/runtime/dispatcher.py` `search_batch` builds a **generic** SEARCH step (`query`, `description`, `_react_args`) and does **not** currently attach the `mode` argument to the step. Downstream `_search_fn` uses `state.context.get("chosen_tool")` for retriever **order**, not per-batch mode. **Net:** today the **three batches are labeled** in merge metadata, but **routing** may not strictly isolate graph vs grep vs vector unless the execute path is extended to read a per-batch hint (see Step 3 — surgical wiring).

### 1.4 Scoper

- **Class:** `ExplorationScoper` — `agent_v2/exploration/exploration_scoper.py`
- **Input:** `candidates: list[ExplorationCandidate]`; rows are **deduped by `file_path`** into JSON: `index`, `file_path`, `sources`, `snippets`, `symbols` (per-row symbol list from chunks)
- **Prompt:** `agent/prompt_versions/exploration.scoper/v1.yaml` — instructions already say “core implementation over wrappers/tests” but **without** structured **definition vs reference** signals.

---

## Step 2 — Entity Grounding Design (No New Layer)

### 2.1 Target schema (extend `QueryIntent`)

Keep **backward compatibility** by **adding** fields and treating absent fields as empty. Recommended Pydantic shape (align names with user request; map to existing channels where possible):

```python
class QueryIntent(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    identifiers: list[str] = Field(default_factory=list)  # filenames, dotted modules, path fragments
    semantic_queries: list[str] = Field(default_factory=list)  # renamed conceptual role of old "keywords"
    regex_patterns: list[str] = Field(default_factory=list)
    intents: list[str] = Field(default_factory=list)
    confidence: dict[str, str] = Field(default_factory=dict)  # e.g. {"symbols": "high"|"low"}
```

**Compatibility strategy for `keywords`:**

- **Option A (preferred):** Add `semantic_queries` as the new canonical field; in `_coerce_for_query_intent`, if `semantic_queries` empty and `keywords` present, copy `keywords` → `semantic_queries`. Deprecate `keywords` in the prompt only (schema stays accepting both until one release).
- **Option B:** Keep `keywords` as alias for `semantic_queries` in code forever (no rename in YAML).

### 2.2 Prompt changes (`exploration.query_intent_parser/v1.yaml`)

**System prompt additions (strict):**

1. **Extract code-like tokens first:** `CamelCase`, `snake_case`, `SCREAMING_SNAKE`, dotted modules (`pkg.submod`), file names (`foo_bar.py`), and **config keys** if they look like identifiers.
2. **Prefer symbols and identifiers over prose.** Natural-language phrases only go to `semantic_queries` when identifiers cannot be inferred.
3. **Empty is OK** if the instruction is purely conceptual — then fill `semantic_queries` and set `confidence.symbols` to `"low"`.
4. **JSON shape:** emit `identifiers`, `semantic_queries`, `confidence`, and `symbols` with documented caps (reuse existing caps style: max symbols, max identifiers, bounded semantic list).

**Enforcement (no new “rules engine”):**

- **Schema + validation:** Pydantic rejects unknown keys; keep coercion for aliases.
- **Prompt constraints:** “Symbols must match `^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$` OR be a filename ending in `.py`” — stated as **LLM output constraint**, not a Python heuristic filter (optional: **lenient** strip only, not path-based scoring).

### 2.3 Caller changes (minimal)

- `query_intent_parser.py`: extend `_coerce_for_query_intent` for `semantic_queries` / `keywords` alias; extend `_remove_repeated_queries` to dedupe new fields against previous payload.
- Any code that reads `intent.keywords` in discovery: switch to **`semantic_queries` with fallback** `intent.keywords` (one-liner helper property on `QueryIntent` is acceptable: `def text_queries(self): return self.semantic_queries or self.keywords`).

---

## Step 3 — Retrieval Integration (No New Retrieval System)

### 3.1 Field → channel mapping

| New / existing field | Channel | Existing mechanism |
|---------------------|---------|---------------------|
| `symbols` | Exact / graph / symbol lookup | `mode="symbol"` batch (unchanged) |
| `identifiers` | Path / module / filename surface | Route to **`regex` or `text`** batch: prefer **grep** with literal path/module substring, or **vector** if grep empty (see 3.2) |
| `semantic_queries` | Embedding / hybrid | `mode="text"` batch (same as today’s keywords) |

### 3.2 Merge strategy

- **Keep** `_discovery` merge map and `DISCOVERY_MERGE_TOP_K` ordering.
- **Add** a fourth list `identifier_queries` (capped) merged into `regex_queries` **or** run as a **parallel** `search_batch` with `mode="regex"` only for identifiers that look like paths (`/`, `.py`, dotted modules).

**Surgical wiring note:** To make channels **actually** hit graph vs grep vs vector, set `state.context["chosen_tool"]` (or a dedicated `discovery_retriever_hint`) **once per `_collect_pairs` invocation** before `search_batch`, e.g. `retrieve_graph` | `retrieve_grep` | `retrieve_vector`, matching the batch. Clear after each batch to avoid races. This touches only `ExplorationEngineV2._discovery` + `Dispatcher.search_batch` step/context — **not** a new pipeline.

### 3.3 No new retrieval system

- Still **one** `Dispatcher` and **one** SEARCH tool path.
- **Reindex** only affects **metadata** returned into rows (Step 4), not a second index service.

---

## Step 4 — Metadata Enrichment (Index-Time)

### 4.1 Where chunks are built

- **Primary:** `repo_index/indexer.py` — `_build_embedding_index` builds Chroma documents **per symbol** with metadata: `path`, `symbol`, `line` (already).
- **Read path:** `agent/retrieval/vector_retriever.py` — returns `file`, `symbol`, `line`, `snippet` from Chroma metadatas.

### 4.2 Proposed metadata extension

For each embedded chunk row:

```python
metadata = {
    "path": "<relative path>",
    "symbol": "<symbol_name>",
    "line": <int>,
    "symbols": ["<def>", "..."],   # optional: defs in chunk scope
    "imports": ["module", "..."],  # optional: top-level imports for file
}
```

### 4.3 How to extract `symbols` / `imports`

- **Symbols:** Already produced by `extract_symbols` / symbol records in the same indexer pass; for chunk scope, use **defining** symbol for the row plus optional **exported** names from the same AST slice (reuse `extract_symbols` / tree walk — **no new parser service**).
- **Imports:** Reuse `repo_index/dependency_extractor.py` or a lightweight AST pass in `parse_file` trees already used in `indexer.py`.

### 4.4 Backward compatibility

- **Additive** Chroma metadata keys; old clients ignore unknown keys.
- **Vector retriever:** map new `imports` into `ExecutionResult` / discovery row dict when building `ExplorationCandidate` in `_discovery` ingestion (extend row parsing: `row.get("imports")` → attach on candidate — requires optional fields on `ExplorationCandidate`).

---

## Step 5 — Scoper Input Upgrade

### 5.1 Schema

Extend `ExplorationCandidate` (additive fields, default empty):

```python
class ExplorationCandidate(BaseModel):
    symbol: Optional[str] = None
    file_path: str
    snippet: Optional[str] = None
    source: Literal["graph", "grep", "vector"]
    imports: list[str] = Field(default_factory=list)
    defined_symbols: list[str] = Field(default_factory=list)
    snippet_summary: str = ""  # optional short LLM-free summary: first line + signature
```

Populate `snippet_summary` deterministically (e.g. first non-empty line of snippet + `symbol` if present) to avoid extra LLM calls.

### 5.2 `ExplorationScoper._aggregate_payload_by_file_path`

- Merge **unique** `imports` and `defined_symbols` across rows for the same `file_path`.
- Add `snippet_summary` per row or aggregate (cap length).

### 5.3 Prompt change (`exploration.scoper/v1.yaml`)

Add explicit reasoning tasks (LLM-led, **not** heuristics):

- For each candidate, classify whether the file **defines** vs **only references** the symbols implied by the instruction (use `defined_symbols` / `symbols` / `snippet_summary`).
- Prefer files that **define** core behavior; deprioritize files that only **import** or **mention** symbols in isolation.

This uses **structured evidence**, not filename rules.

---

## Step 6 — Backward Compatibility

| Scenario | Behavior |
|----------|----------|
| `symbols` / `identifiers` empty | Same as today: `semantic_queries` / `keywords` drive vector/hybrid; merge unchanged |
| Old prompts still emit `keywords` only | Coercion fills `semantic_queries`; pipeline unchanged |
| Index lacks `imports` on old Chroma rows | Fields empty; scoper falls back to current instructions + snippets |
| Older `ExplorationCandidate` without new fields | Defaults: empty lists / empty summary |

---

## Step 7 — Minimal Change Constraint (Checklist)

```json
{
  "files_to_modify": [
    "agent/prompt_versions/exploration.query_intent_parser/v1.yaml",
    "agent_v2/exploration/query_intent_parser.py",
    "agent_v2/schemas/exploration.py",
    "agent_v2/exploration/exploration_engine_v2.py",
    "agent_v2/runtime/dispatcher.py",
    "agent/execution/step_dispatcher.py",
    "repo_index/indexer.py",
    "agent/retrieval/vector_retriever.py",
    "agent/prompt_versions/exploration.scoper/v1.yaml",
    "agent_v2/exploration/exploration_scoper.py",
    "tests/test_query_intent_parser.py"
  ],
  "functions_to_modify": [
    "QueryIntentParser._coerce_for_query_intent",
    "QueryIntentParser._remove_repeated_queries",
    "ExplorationEngineV2._discovery",
    "Dispatcher.search_batch",
    "_search_fn (step_dispatcher) — read per-batch retriever hint from context",
    "_build_embedding_index",
    "vector_search_with_embedder / row shaping",
    "ExplorationScoper._aggregate_payload_by_file_path"
  ],
  "new_fields_added": [
    "QueryIntent.identifiers",
    "QueryIntent.semantic_queries",
    "QueryIntent.confidence",
    "ExplorationCandidate.imports",
    "ExplorationCandidate.defined_symbols",
    "ExplorationCandidate.snippet_summary",
    "Chroma metadata: symbols[], imports[]"
  ],
  "prompt_changes": [
    "exploration.query_intent_parser/v1.yaml — entity grounding + confidence",
    "exploration.scoper/v1.yaml — define vs reference reasoning"
  ],
  "no_changes_to": [
    "ExplorationEngineV2 control loop / expand-refine policy",
    "Planner, dispatcher policy engine, retrieval pipeline **order** (immutable per architecture rules)",
    "New microservices, new retrieval daemons, parallel embedding systems"
  ]
}
```

---

## Step 8 — Validation Plan

**Before vs after (offline eval harness + logs):**

| Metric | How to measure |
|--------|----------------|
| **% test files in scoper selection** | Parse `exploration.scope` outputs / JSON logs; `path` contains `test` / `tests/` (report only — **not** used for scoring in prod) |
| **Implementation reach** | % runs where first `analyzer` “sufficient” hits a **non-test** file under `agent/` / `src/` (human label or fixture cases) |
| **Insufficient loops** | Count `evidence_sufficiency == "insufficient"` transitions per case from `exploration` logs |

**Fixture updates:** extend `tests/fixtures/exploration_*_cases.py` with cases that **require** symbol grounding (e.g. instruction uses vague phrase but repo has unique `ExplorationEngineV2`).

---

## Non-Negotiables (Confirmed)

- **No** test-file heuristics in ranking.
- **No** new services or pipelines.
- **No** refactor of exploration loop or planner.
- **Yes** additive schema, additive index metadata, prompt-led scoper reasoning.

---

## Expected Outcome

- Queries carry **identifiers** and **symbols** when the repo supports them.
- Retrieval batches **bind** queries to the right **retriever** behavior via explicit context hints (surgical dispatcher fix).
- Candidates carry **imports** and **definition symbols** so the scoper can **prefer definers** without brittle rules.
- Exploration reaches **implementation** files more often with fewer **misaligned** early stops.
