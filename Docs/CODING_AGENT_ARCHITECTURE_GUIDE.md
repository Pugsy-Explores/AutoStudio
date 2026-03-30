# Coding Agent Architecture Guide

**For AI agents and developers building code-modification systems.** This guide documents common anti-patterns that cause agents to fail, the correct architecture patterns used in production systems, and how AutoStudio implements them. Use it when designing retrieval, editing, or execution pipelines—or when debugging agent behavior.

**Contents:** (0) **Model client—never use reasoning_content as content fallback** [PRIORITY], (1) Retrieval—why large context fails, (2) Code understanding—symbol graphs vs plain text, (3) Editing—structured diff/patch pipelines, (4) Fault tolerance—policy engine and retries, (5) Memory—task and repo state. Ends with the full pipeline diagram and a quick-reference checklist.

---

## 0. [PRIORITY] Mistake: Using reasoning_content When content Is Empty

**Do not** fall back to `reasoning_content` when `content` is empty.

### Why?

Reasoning models (e.g. Qwen, o1-style) emit thinking in `reasoning_content` and the actual answer in `content`. If the model never outputs to `content` (e.g. runs out of tokens during thinking), returning `reasoning_content` as if it were the answer:

- Breaks structured output consumers (JSON extraction, tool calls)
- Mixes internal reasoning with user-facing output
- Hides the real failure (empty content) and makes debugging harder

### Correct Pattern

- Return `content` only. If `content` is empty, return empty and let callers handle it (fallback, retry, or clear error).
- For tasks that require strict JSON (e.g. query rewriting), use models that output directly to `content`, or configure prompts so the model emits the answer in `content`—not in reasoning.

### AutoStudio Implementation

- **agent/models/model_client.py** — Returns `content` only. `reasoning_content` is streamed to terminal for visibility but never used as the returned value.

---

## 1. Mistake: Believing Bigger Context Windows Solve Everything

Many teams try this approach:

```
load entire repo → send to LLM → ask question
```

It seems logical. **But it fails.**

### Why?

LLMs don't fail because of lack of tokens—they fail because they **read the wrong files**.

Even when the correct files exist in the repo, models often confidently modify the wrong ones because they cannot navigate the repository structure.

### Solution

Serious assistants build **intelligent retrieval engines** instead of huge prompts.

Modern coding assistants rely on:

- **Repo index**
- **Symbol graph**
- **Retrieval**
- **Context ranking**

—not giant context windows.

> **Hybrid indexing** (AST + code graph + semantic search) is now considered the foundation for large-codebase AI systems.

---

## 2. Mistake: Treating Code Like Plain Text

A lot of systems still do this:

```
grep code → send files to LLM
```

But code has **structure**:

- Classes
- Functions
- Imports
- Call graphs
- Inheritance

Without structural understanding, agents cannot reason across modules.

### Solution

**Symbol graphs** / **code graphs** exist for this reason.

Research shows repository intelligence graphs significantly improve coding agent accuracy and speed because they give the model a **deterministic architectural map**.

### AutoStudio Implementation

- **repo_index/** — Tree-sitter parser, parallel indexing, symbol extraction, dependency edges; optional embedding index (ChromaDB)
- **repo_graph/** — SQLite storage, 2-hop expansion; `repo_map_builder` (spec format: modules, symbols, calls); `repo_map_updater` (incremental updates); `change_detector` (affected callers, risk levels)
- **agent/retrieval/** — `repo_map_lookup` (lookup_repo_map, load_repo_map); `anchor_detector` (detect_anchor: query + repo_map → symbol + confidence); `search_pipeline` (hybrid parallel: graph + vector + grep; uses repo_map anchor when confidence ≥ 0.9); `symbol_expander` (anchor → expand depth=2 → fetch bodies → rank → prune); `graph_retriever` (symbol lookup → expansion); `vector_retriever` (embedding search); `retrieval_cache` (LRU); `reranker/` (cross-encoder reranking: GPU/CPU auto-select, dedup, cache, score fusion, symbol bypass; see [RETRIEVAL_ARCHITECTURE.md](RETRIEVAL_ARCHITECTURE.md)); `context_builder_v2` (assemble_reasoning_context: FILE/SYMBOL/LINES/SNIPPET)
- **agent/execution/explain_gate.py** — `ensure_context_before_explain` (inject SEARCH before EXPLAIN when ranked_context empty; avoids wasted LLM calls)

---

## 3. Mistake: Letting LLMs Edit Files Directly

A common beginner system:

```
model outputs new file → overwrite code
```

This causes:

- Syntax errors
- Formatting issues
- Broken dependencies

### Solution

Production systems use a **structured pipeline**:

```
diff planning → patch validation → AST patching → execution
```

The edit must go through a structured pipeline.

### AutoStudio Implementation

- **editing/diff_planner.py** — `plan_diff(instruction, context)` returns planned changes; identifies affected symbols and callers from graph
- **editing/conflict_resolver.py** — same symbol, same file, semantic overlap; returns sequential groups
- **editing/patch_generator.py** — `to_structured_patches(plan, instruction, context)` → structured patches
- **editing/ast_patcher.py** — Tree-sitter AST edits (insert/replace/delete at function_body, statement, block level); preserves relative indentation
- **editing/patch_validator.py** — compile + AST reparse before write
- **editing/patch_executor.py** — apply → validate → write; rollback on invalid syntax, validation failure, or apply error; max 5 files, 200 lines per patch
- **editing/test_repair_loop.py** — run tests after patch; repair on failure (max 3 attempts); flaky detection; compile step

---

## 4. Mistake: No Tool Fault Tolerance

Agents fail constantly. Common failures:

- Tool timeout
- Bad arguments
- LLM hallucinated path
- Missing dependency

Robust agents must **absorb failure** instead of crashing.

### Solution

Production coding agents:

- Retry tools
- Fallback search
- Recover from bad arguments
- Continue pipeline

Systems in production treat tool failures as **normal events** and allow the agent to recover or continue execution.

### AutoStudio Implementation

- Policy engine
- Retry logic
- Fallback search (retrieve_graph → retrieve_vector → retrieve_grep → Serena)
- LLM-based replanner (receives failed_step and error; produces revised plan)

---

## 5. Mistake: No Architectural Memory

Agents that run statelessly tend to:

- Repeat work
- Forget discoveries
- Loop forever

### Solution

Good coding agents maintain **task memory** and **repository memory**.

Example memory layers:

- Repo index
- Symbol graph
- Task memory
- Execution traces
- Diff history

This allows:

- Resume tasks
- Avoid rediscovering code
- Track changes

Multi-agent architectures often accumulate knowledge across steps so discoveries become reusable context rather than rediscovered every time.

### AutoStudio Implementation

- **agent/memory/task_memory.py** — `save_task`, `load_task`, `list_tasks` (`.agent_memory/tasks/`)
- **agent/memory/task_index.py** — vector index for `search_similar_tasks` (optional)
- **agent/observability/trace_logger.py** — `start_trace`, `log_event`, `finish_trace` (`.agent_memory/traces/`)
- **agent/orchestrator/agent_controller.py** — `run_controller` full pipeline

---

## What a Production Coding Agent Actually Looks Like

When all layers come together, the architecture looks like this:

```
repo
  ↓
repo indexer
  ↓
symbol graph
  ↓
repo map
  ↓
graph retrieval
  ↓
context ranking
  ↓
instruction router (optional; CODE_SEARCH/EXPLAIN/INFRA skip planner)
  ↓
planner
  ↓
diff planner
  ↓
conflict resolver
  ↓
AST patch engine
  ↓
patch validator
  ↓
patch executor
  ↓
change detector
  ↓
test repair loop
  ↓
index update
  ↓
task memory
  ↓
agent controller
```

**AutoStudio product path (`agent_v2`):** the same retrieval and editing columns apply; control flow is **exploration (read-only)** → **PlannerTaskRuntime** ACT loop (**TaskPlanner** decisions → **PlannerV2** / **PlanExecutor** → **Dispatcher**) with optional **answer synthesis** and **answer validation** on the synthesize branch. See root **README.md** (mermaid) and **Docs/architecture_freeze/ARCHITECTURE_FREEZE.md**. **Eval:** `eval/` tiered harness wraps `create_runtime` and records **`PipelineCapture`** without forking the engine.

This matches modern agentic development systems where AI agents **plan**, **execute tasks through tools**, **observe results**, and **iteratively improve outputs** until the goal is achieved.

---

## Quick Reference: Architecture Checklist

| Layer | Mistake | Correct Pattern |
|-------|---------|-----------------|
| **Model client** | Use reasoning_content when content empty | Return content only; handle empty via fallback/retry |
| Retrieval | Load entire repo | Repo index + symbol graph + retrieval + context ranking |
| Code understanding | Grep as plain text | Symbol graph, call graphs, dependency edges |
| Editing | Direct file overwrite | Diff planner → patch validation → AST patching → execution |
| Resilience | Crash on tool failure | Policy engine, retries, fallbacks, replanning |
| Memory | Stateless execution | Task memory, repo index, trace logs |
