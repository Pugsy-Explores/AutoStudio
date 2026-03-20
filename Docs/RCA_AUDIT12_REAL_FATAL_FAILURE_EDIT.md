# RCA: audit12 Real Mode — FATAL_FAILURE on EDIT and Graph Builder Warning

**Date:** 2025-03-20  
**Command:** `python3 -m tests.agent_eval.runner --execution-mode real --suite audit12 --output artifacts/agent_eval_runs/audit12_after_docs_retrieval_hardening`  
**Observed:** `[graph_builder] edges provided but none added`, `[execution_loop] FATAL_FAILURE, stopping (step_id=2 action=EDIT)` (×3)

---

## Executive Summary

Two distinct issues appear in the run output. Both are **known patterns** from prior audits (STAGE12_2). The EDIT FATAL_FAILURE is the primary blocker for repair tasks; the graph_builder warning is a secondary indexing quality signal.

| Issue | Severity | Root Cause | Status |
|-------|----------|------------|--------|
| FATAL_FAILURE on EDIT (step 2) | **Blocker** | Patch executor "Symbol not found" → exhausted retries → FATAL | Known; retrieval/context chain |
| graph_builder "edges provided but none added" | Degraded | Edge name resolution mismatch | Known; indexer/edge format |

---

## 1. FATAL_FAILURE on EDIT (step_id=2)

### Observed

```
[execution_loop] FATAL_FAILURE, stopping (step_id=2 action=EDIT)
```
(Repeated for multiple repair tasks.)

### Root Cause Chain

1. **Query rewriter stub** (`real_execution.offline_llm_stubs`) returns `{"steps": []}` for retrieval query rewriting. Search uses weak or empty queries.

2. **Retrieval** returns hits that may include:
   - Directory paths or `.symbol_graph` roots (filtered later, but can affect ranking)
   - Wrong files or symbols when reranker/BM25 fail (RecursionError fallback to retriever-score ordering)

3. **Context for diff_planner** is weak: `ranked_context` / `retrieved_symbols` do not reliably contain the correct file + symbol for the repair instruction.

4. **diff_planner** produces a patch targeting a symbol (e.g. `multiply` in `src/calc/ops.py`). The patch specifies `target_node` for AST anchoring.

5. **patch_executor** → **ast_patcher.apply_patch** fails with `"Symbol not found"` when the target symbol cannot be located in the parsed AST (wrong anchor, wrong file, or symbol name mismatch).

6. **Policy engine** (`_execute_edit`):
   - EDIT policy: `max_attempts=2`, `retry_on=["symbol_not_found"]`, `mutation="symbol_retry"`
   - `symbol_retry(step)` returns `[dict(step)]` — **same step twice** (no actual mutation)
   - Both attempts fail with the same error
   - Returns `"edit failed after retries"`

7. **classify_result** sees `"after retries"` in error → `ResultClassification.FATAL_FAILURE` (line 114 policy_engine.py).

8. **execution_loop** stops on FATAL_FAILURE; no replan.

### Why Retries Don't Help

`symbol_retry` in `mutation_strategies.py` is a placeholder:

```python
def symbol_retry(step: dict[str, Any]) -> list[dict[str, Any]]:
    # For now return the step once; future: copy with small symbol/path variants
    return [dict(step)]
```

Both attempts use the **identical** step. No symbol/path variant is tried. Retries are redundant.

### References

- `Docs/STAGE12_2_FIRST_EXECUTION_QUALITY_AUDIT.md` — same pattern: "Symbol not found", FATAL_FAILURE, zero patches
- `editing/patch_executor.py` — returns `"symbol_not_found"`; logs `[patch_executor] apply_patch error: Symbol not found`
- `agent/execution/policy_engine.py` — EDIT policy, classify_result "after retries" → FATAL
- `agent/execution/mutation_strategies.py` — symbol_retry no-op

---

## 2. graph_builder "edges provided but none added"

### Observed

```
[graph_builder] edges provided but none added (name resolution may have failed)
```

### Root Cause

- `repo_index.indexer` produces `symbols` and `edges` from `extract_symbols` and `extract_edges`.
- `graph_builder.build_graph` adds nodes from `symbols`, then resolves `source_symbol` / `target_symbol` in edges to node IDs via `name_to_id`.
- Resolution uses: `name_to_id.get(name)` or `name_to_id.get(name.split(".")[-1])` for short-name fallback.
- When **all** edges fail to resolve (e.g. edge names use different conventions than symbol names), `edge_count == 0` and the warning is logged.

**Typical mismatches:**
- Edges: `source_symbol` = module stem (e.g. `ops`), `target_symbol` = `module.func` or dotted import
- Symbols: `symbol_name` = `multiply` or `ops.multiply` (qualified)
- Name resolution may fail if casing, qualification, or module-vs-symbol conventions differ.

### Impact

- Graph expansion (callers, callees, references) is empty for that workspace.
- Retrieval falls back to other stages (vector, BM25, grep). Not fatal, but degrades context quality.

### References

- `repo_graph/graph_builder.py` lines 34–49
- `repo_index/dependency_extractor.py` — edge format
- `repo_index/symbol_extractor.py` — symbol_name format

---

## Recommendations

### 1. Improve Query Rewriter Stub (High Impact)

Replace `_stub_reasoning_json` with a stub that returns **task-specific search queries** derived from the planner step description. Example: for "Repair src/calc/ops.py so multiply(2,3)==6", return `{"steps": [{"query": "multiply function in ops.py"}]}`. This gives retrieval usable queries instead of empty steps.

**File:** `tests/agent_eval/real_execution.py`

### 2. Implement symbol_retry Mutation (Medium Impact)

Make `symbol_retry` produce **variants** of the step:
- Try `target_node` = `module_append` when symbol-level patch fails
- Try short symbol name (e.g. `multiply` vs `ops.multiply`) if patch specifies qualified name
- Try alternate files from `ranked_context` when first file fails

**File:** `agent/execution/mutation_strategies.py`

### 3. Normalize Graph Edge/Symbol Names (Medium Impact)

Align `dependency_extractor` edge names with `symbol_extractor` symbol names:
- Use consistent qualification (e.g. `file_path:SymbolName`)
- Ensure `name_to_id` indexes both qualified and short names for all symbols
- Log unresolved edge names for debugging

**Files:** `repo_graph/graph_builder.py`, `repo_index/dependency_extractor.py`

### 4. Add Fallback for "Symbol not found" (Lower Impact)

When `apply_patch` fails with "Symbol not found", consider:
- Falling back to line-based or text-based patch application for simple edits
- Or returning RETRYABLE with a hint so the policy engine can try a different mutation

**File:** `editing/patch_executor.py`

### 5. Fix RecursionError (Pre-import numpy)

Already addressed in `Docs/RCA_AUDIT12_RECURSION_AND_STUBS.md`. Ensures BM25 and reranker work, improving retrieval quality.

---

## Verification

After changes, re-run:

```bash
python3 -m tests.agent_eval.runner --execution-mode real --suite audit12 --output artifacts/agent_eval_runs/audit12_after_docs_retrieval_hardening
```

**Success criteria:**
- `patches_applied` > 0 for repair tasks (core12_mini_repair_calc, core12_mini_repair_parse, etc.)
- No FATAL_FAILURE on EDIT for tasks that have valid repair targets
- graph_builder warning reduced or eliminated when edges resolve
