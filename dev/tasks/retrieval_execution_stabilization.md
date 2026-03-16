# Retrieval Execution Stabilization

You are acting as a principal engineer implementing a system stabilization phase.

Do not redesign architecture.

Do not introduce new features beyond what is specified.

Follow the tasks exactly in the order specified.

Focus on latency reduction, reliability, and separation of responsibilities.

---

You are acting as a principal engineer performing a stabilization upgrade of the AutoStudio retrieval system.

The existing stabilization plan is correct but missing several critical production features.

Do NOT redesign the architecture.

Do NOT add new frameworks.

Extend the existing plan only.

---

## TASK 1 — Split SEARCH into two tools

### Goal

Reduce latency by separating:
- candidate discovery
- context expansion

Right now the SEARCH tool does both. That is wrong.

### Required changes

#### 1. Create new tool: SEARCH_CANDIDATES

**File:** `agent/tools/search_candidates.py`

**Implementation rules:**
- SEARCH_CANDIDATES performs ONLY candidate discovery.
- **Allowed operations:**
  - BM25
  - vector retrieval
  - symbol lookup
  - repo_map lookup
  - grep fallback
- **NOT ALLOWED:**
  - graph expansion
  - symbol body reading
  - LLM ranking
  - context building

**Return format:**
```json
{
  "candidates": [
    {
      "symbol": "...",
      "file": "...",
      "snippet": "...",
      "score": float,
      "source": "bm25|vector|grep|symbol"
    }
  ]
}
```

**Maximum:** top 20 candidates

#### 2. Modify retrieval pipeline

**File:** `agent/retrieval/retrieval_pipeline.py`

Add new function:
```python
def search_candidates(query: str) -> list[dict]
```

**Implementation:**
```
candidates = []
candidates += bm25_search(query)
candidates += vector_search(query)
candidates += repo_map_lookup(query)
candidates += grep_search(query)
candidates = rrf_merge(candidates)
return top_k(candidates, 20)
```

**Do NOT run:**
- symbol expansion
- context ranking
- pruning

#### 3. Register tool in tool registry

**File:** `agent/tools/__init__.py`

- Add: `search_candidates` (export as SEARCH_CANDIDATES tool)
- Remove or deprecate heavy monolithic SEARCH tool usage where it conflates discovery + context building

---

## TASK 2 — Create BUILD_CONTEXT tool

### Goal

Move expensive operations into a separate tool.

### Required changes

#### 1. Create new tool

**File:** `agent/tools/build_context.py`

**Function:** `build_context(candidates)`

**Allowed operations:**
- graph expansion
- symbol expansion
- read symbol body
- reranking
- context pruning

**Pipeline:**
```
candidates
  ↓
graph expansion
  ↓
symbol body read
  ↓
reranker
  ↓
context pruning
  ↓
context builder
```

**Return:**
```json
{
  "context_blocks": [...]
}
```

#### 2. Modify planner prompt

**File:** `agent/prompts/planner_system.yaml`

Replace SEARCH usage.

**Old plan:** SEARCH → EDIT

**New plan:** SEARCH_CANDIDATES → BUILD_CONTEXT → EDIT

**Example:**
```json
{
  "steps": [
    {"tool": "SEARCH_CANDIDATES", "query": "expand_graph"},
    {"tool": "BUILD_CONTEXT"},
    {"tool": "EXPLAIN"}
  ]
}
```

Add SEARCH_CANDIDATES and BUILD_CONTEXT as allowed actions alongside EDIT, SEARCH, EXPLAIN, INFRA.

---

## TASK 3 — Retrieval latency instrumentation

### Goal

Add stage timers to measure latency per stage.

### Required changes

#### 1. Create metrics module

**File:** `agent/retrieval/retrieval_metrics.py`

Create:
```python
class RetrievalMetrics:
    def start(stage: str) -> None
    def end(stage: str) -> None
```

**Stages to measure:**
- bm25
- vector
- grep
- repo_map
- rrf_merge
- graph_expand
- symbol_expand
- rerank
- context_prune

#### 2. Modify pipeline

**File:** `agent/retrieval/retrieval_pipeline.py`

Wrap every stage:
```python
metrics.start("vector")
vector_results = vector_search(query)
metrics.end("vector")
```

**Log output format:**
```
[retrieval_metrics]
vector=0.42s
bm25=0.12s
graph_expand=0.9s
rerank=0.8s
```

**Goal:** SEARCH_CANDIDATES < 1s, BUILD_CONTEXT < 5s

---

## TASK 4 — Replace query rewrite with query expansion

### Goal

Replace `rewrite_query_with_context()` with `generate_query_expansions()`.

### Required changes

#### 1. Create new module

**File:** `agent/retrieval/query_expansion.py`

**Function:**
```python
def generate_query_expansions(query: str) -> list[str]
```

**Rules:**
- Generate 5–8 variants.
- **Example input:** `expand graph implementation`
- **Output:** `["expand", "expand_graph", "graph_expand", "expand_nodes", "Graph.expand", "expand_neighbors"]`

**Implementation:**
- 1 call to small LLM
- OR deterministic token splitting

#### 2. Modify retrieval pipeline

Instead of `rewritten_query`, use:
```python
expansions = generate_query_expansions(query)
for q in expansions:
    run bm25/vector
```

Merge results using RRF.

---

## TASK 5 — Remove duplicate ranking passes

### Goal

Ensure ranking occurs only once.

### Required changes

- Search code for `rank_context`.
- You will likely find `rank_context()` called multiple times.
- Delete the first pass.
- **Final ranking stage:** cross encoder reranker only.

**File:** `agent/retrieval/reranker/`

Ensure only one call: `reranker.rerank(candidates)`.

---

## TASK 6 — Add model warm-start

### Goal

Prevent first-query latency spikes by preloading models at boot.

### Required changes

#### 1. Create startup initializer

**File:** `agent/runtime/model_bootstrap.py`

**Function:**
```python
def initialize_models() -> None
```

**Load:**
- embedding model
- reranker
- router model
- planner tokenizer

**Example:**
```python
embedding_model = SentenceTransformer(...)
reranker = CrossEncoder(...)
```

#### 2. Call during agent boot

**File:** `agent/runtime/agent_boot.py`

Add: `initialize_models()` at startup.

---

## TASK 7 — Tool latency budgets

### Goal

Enforce per-tool timeout limits.

### Required changes

#### 1. Create tool policy config

**File:** `config/tool_budgets.py`

Add:
```python
TOOL_BUDGETS = {
    "SEARCH_CANDIDATES": 1.0,
    "BUILD_CONTEXT": 5.0,
    "EDIT": 10.0,
    "EXPLAIN": 5.0
}
```

#### 2. Modify agent loop

**File:** `agent/autonomous/agent_loop.py`

Add enforcement:
```python
timeout = TOOL_BUDGETS[tool_name]
run_tool_with_timeout(tool, timeout)
```

If timeout exceeded: tool failure → retry → fallback.

---

## TASK 8 — Improve failure policy

### Goal

Agents must never terminate on first tool failure.

### Required changes

**File:** `agent/meta/evaluator.py` and `agent/meta/trajectory_loop.py`

**Current behavior:** tool failure → FATAL

**Replace with:**
- tool failure → retry tool
- retry limit = 2
- if still failing: fallback tool
- continue plan

**Example:**
- SEARCH_CANDIDATES fail → fallback grep search

---

## TASK 9 — Update documentation

### Goal

Keep docs aligned with new pipeline.

### Required changes

Update:
- `Docs/RETRIEVAL_ARCHITECTURE.md`
- `Docs/AGENT_LOOP_WORKFLOW.md` (or create `Docs/AGENT_EXECUTION_MODEL.md` if referenced)
- `README.md`

Add new pipeline diagram:
```
SEARCH_CANDIDATES
        ↓
BUILD_CONTEXT
        ↓
EXECUTOR
```

---

## TASK 10 — Validation benchmarks

### Goal

Provide a script to validate latency targets.

### Required changes

**File:** `scripts/run_retrieval_latency_benchmark.py`

**Metrics to print:**
- search_latency
- context_latency
- total_agent_runtime

**Target:**
- search_latency < 1s
- context_latency < 5s
- agent_runtime < 10s

---

## TASK 11 — Add retrieval caching (Improvement 1)

### Goal

Reduce redundant retrieval calls via LRU caches.

### Required changes

**File:** `agent/retrieval/retrieval_cache.py`

**Extend** the existing module (do not replace). Add:

1. **candidate_cache:** `query → candidate list`
2. **context_cache:** `symbol → expanded context`

**Implementation:**
- Use LRU with size 1024 for each cache.
- Use `functools.lru_cache` or a simple LRU dict (evict oldest when full).

**Integrate cache lookup in:**
- `search_candidates()` — check `candidate_cache` before running retrieval; store result on miss.
- `build_context()` — check `context_cache` for symbol→context; store on miss.

---

## TASK 12 — Normalize retrieval scores (Improvement 2)

### Goal

Normalize raw scores before RRF merge to avoid scale dominance.

### Required changes

**File:** `agent/retrieval/retrieval_pipeline.py` (in `search_candidates()` or equivalent)

**Before RRF merge:**
```python
normalized_score = raw_score / max_score  # per-source list
```

Apply normalization for:
- bm25
- vector
- grep

Then run RRF.

**Note:** Normalization is applied in the caller before passing to `reciprocal_rank_fusion`. The `rank_fusion.py` module itself uses rank positions; normalization ensures each source list has comparable score ranges before any pre-RRF scoring logic.

---

## TASK 13 — Penalize test files (Improvement 3)

### Goal

Down-rank test files in candidate scoring.

### Required changes

**File:** `agent/retrieval/retrieval_pipeline.py` (or `search_candidates` tool)

Add scoring penalty before ranking:
```python
if "/tests/" in path or "test_" in path:
    score *= 0.3
```

Apply before final ranking/reranking.

---

## TASK 14 — Enforce context budgets (Improvement 4)

### Goal

Hard limits on context size.

### Required changes

#### 1. Create config

**File:** `config/context_limits.py`

Add:
```python
MAX_CONTEXT_TOKENS = 8000
MAX_CONTEXT_SNIPPETS = 12
MAX_CONTEXT_FILES = 6
```

#### 2. Modify context builder

**File:** `agent/retrieval/context_builder.py`

Enforce limits when building context:
- Cap total tokens to `MAX_CONTEXT_TOKENS`
- Cap snippets to `MAX_CONTEXT_SNIPPETS`
- Cap unique files to `MAX_CONTEXT_FILES`

---

## TASK 15 — Structured retrieval logs (Improvement 5)

### Goal

All retrieval tool logs must include trace identifiers.

### Required changes

**File:** `agent/retrieval/retrieval_metrics.py`

Extend `RetrievalMetrics` to include:
- `trace_id`
- `query_id`
- `step_id`

All tool logs (search_candidates, build_context, pipeline stages) must include these fields when available.

---

## TASK 16 — Safe query expansion (Improvement 6)

### Goal

Filter out unsafe or noisy query expansions.

### Required changes

**File:** `agent/retrieval/query_expansion.py`

Add filter stage after generating expansions:

**Rules:**
- `allowed_pattern = r"[A-Za-z0-9_\.]+"`
- `max_tokens = 4`

**Remove expansions that:**
- contain spaces (or have > 4 tokens)
- contain punctuation (except `.` and `_`)
- contain stopwords (e.g. common words: the, a, an, is, are, was, were, be, been, being, have, has, had, do, does, did, will, would, could, should, may, might, must, shall, can)

---

## TASK 17 — Parallel candidate retrieval (Improvement 7)

### Goal

Run bm25, vector, grep in parallel to reduce latency.

### Required changes

**File:** `agent/tools/search_candidates.py` or `agent/retrieval/retrieval_pipeline.py`

Modify `search_candidates()`:

Run these in parallel:
- bm25_search
- vector_search
- grep_search

Use:
```python
asyncio.gather(bm25_task(), vector_task(), grep_task())
```

**Note:** If the codebase uses sync APIs, use `concurrent.futures.ThreadPoolExecutor` as an alternative (the existing `search_pipeline.py` uses this pattern). Prefer `asyncio.gather` only if async retrieval functions exist; otherwise extend the existing ThreadPoolExecutor pattern.

---

## TASK 18 — Enforce candidate deduplication (Improvement 8)

### Goal

Deduplicate candidates before reranker.

### Required changes

**File:** `agent/retrieval/retrieval_pipeline.py`

Import:
```python
from agent.retrieval.reranker.deduplicator import deduplicate_candidates
```

Apply before reranker:
```python
candidates = deduplicate_candidates(candidates)
```

**Note:** The pipeline may already call `deduplicate_candidates`; ensure it is applied exactly once, immediately before the reranker stage.

---

## TASK 19 — Query type detection (Improvement 9)

### Goal

Skip expensive vector search and query expansion for symbol-style queries.

### Required changes

**File:** `agent/tools/search_candidates.py` or `agent/retrieval/retrieval_pipeline.py`

Reuse existing: `agent/retrieval/reranker/symbol_query_detector.is_symbol_query(query)`

**Rules (already implemented in symbol_query_detector):**
- CamelCase
- snake_case
- contains "."
- single token

**If `is_symbol_query(query)` returns True:**
- skip vector search
- skip query expansion

Use repo_map, grep, BM25 only for symbol queries.

---

## TASK 20 — Agent step tracing (Improvement 10)

### Goal

Emit structured step logs for debugging and analysis.

### Required changes

**File:** `agent/autonomous/agent_loop.py`

Extend agent loop logging. Each step must emit:
- step_id
- tool_name
- query
- latency
- result_count

**Write logs to:** `logs/agent_trace.jsonl`

Each line: one JSON object per step.

---

## Summary

After these 20 tasks:

- **SEARCH_CANDIDATES** becomes cheap, fast, repeatable.
- **BUILD_CONTEXT** becomes heavy, targeted.
- Agent execution becomes stable.
- Caching, normalization, test-file penalties, and context budgets improve production behavior.
- Structured logs and step tracing improve observability.
