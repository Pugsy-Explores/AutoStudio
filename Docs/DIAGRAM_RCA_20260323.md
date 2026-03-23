# RCA — Mermaid & ASCII Diagram Drift

**Date:** 2026-03-23

---

## Findings

### 1. run_attempt_loop Does Not Exist

- **Docs say:** run_controller → run_attempt_loop (Legacy, REACT_MODE=0)
- **Code:** `run_attempt_loop` is not defined anywhere. `agent_controller.py` only calls `run_hierarchical`.
- **Impact:** ARCHITECTURE.md Legacy diagram and AGENT_LOOP_WORKFLOW.md Phase 5 diagram document a path that is not in the codebase.

### 2. run_controller Actual Flow (Code)

```
start_trace
  → ensure_retrieval_daemon (if RERANKER/EMBEDDING_USE_DAEMON)
  → build_repo_map
  → search_similar_tasks
  → run_hierarchical(instruction, root, trace_id, similar_tasks)
       → execution_loop (ReAct)
  → save_task
  → finish_trace
```

- **REACT_ARCHITECTURE Overview:** Omits build_repo_map, search_similar_tasks, save_task, start_trace, finish_trace.

### 3. execution_loop (ReAct) — Diagram Accuracy

- **REACT_ARCHITECTURE Mermaid:** Flow is correct (RGA → LLM → validate → dispatch → obs → append → loop).
- **EDIT label:** Diagram says "validate → run_tests" but the doc EDIT path says "validate_project (syntax) → run_tests". Minor; consistent.

### 4. AGENT_LOOP_WORKFLOW.md

- **First diagram:** run_controller → run_attempt_loop. Wrong — controller never calls run_attempt_loop.
- **Section "High-level flow":** "Phase 5: Deterministic mode uses run_controller → run_attempt_loop" — incorrect for current code.
- **Step loop description:** References "execution_loop shared by run_deterministic & run_agent" and ExecutionLoopMode — the current execution_loop is ReAct-only; no mode parameter.

---

## Root Cause

Documentation was written for a design that included run_attempt_loop (Phase 5) and a mode switch. The implementation was simplified to ReAct-only: run_controller always calls run_hierarchical. Legacy path was removed from code but docs were not updated.

---

## Corrections Applied

1. **REACT_ARCHITECTURE.md:** Update Overview and add controller preamble to diagram.
2. **ARCHITECTURE.md:** Add note that Legacy diagram is design-only (not in current code).
3. **AGENT_LOOP_WORKFLOW.md:** Add banner that Phase 5/legacy diagrams are design reference; primary path is ReAct. Clarify execution_loop is ReAct-only.
