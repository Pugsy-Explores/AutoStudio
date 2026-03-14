# Coding Agent Architecture Guide

**For AI agents and developers building code-modification systems.** This guide documents five common anti-patterns that cause agents to fail, the correct architecture patterns used in production systems, and how AutoStudio implements them. Use it when designing retrieval, editing, or execution pipelines—or when debugging agent behavior.

**Contents:** (1) Retrieval—why large context fails, (2) Code understanding—symbol graphs vs plain text, (3) Editing—structured diff/patch pipelines, (4) Fault tolerance—policy engine and retries, (5) Memory—task and repo state. Ends with the full pipeline diagram and a quick-reference checklist.

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
- **repo_graph/** — SQLite storage, 2-hop expansion; `repo_map_builder` (architectural map); `change_detector` (affected callers, risk levels)
- **agent/retrieval/** — `graph_retriever` (symbol lookup → expansion); `vector_retriever` (embedding search when graph returns nothing); `retrieval_cache` (LRU)

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
- **editing/patch_generator.py** — `to_structured_patches`
- **editing/patch_executor.py** — AST patching, rollback on failure; max 5 files, 200 lines per patch
- **editing/patch_validator.py** — validate patches before execution
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
- Fallback search
- Replanning

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

This matches modern agentic development systems where AI agents **plan**, **execute tasks through tools**, **observe results**, and **iteratively improve outputs** until the goal is achieved.

---

## Quick Reference: Architecture Checklist

| Layer | Mistake | Correct Pattern |
|-------|---------|-----------------|
| Retrieval | Load entire repo | Repo index + symbol graph + retrieval + context ranking |
| Code understanding | Grep as plain text | Symbol graph, call graphs, dependency edges |
| Editing | Direct file overwrite | Diff planner → patch validation → AST patching → execution |
| Resilience | Crash on tool failure | Policy engine, retries, fallbacks, replanning |
| Memory | Stateless execution | Task memory, repo index, trace logs |
