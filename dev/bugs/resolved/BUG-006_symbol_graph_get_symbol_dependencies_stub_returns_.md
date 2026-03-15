# Bug ID
BUG-006

# Title
symbol_graph get_symbol_dependencies stub returns empty instead of querying index.sqlite

# Area
retrieval

# Severity
medium

# Description
agent/retrieval/symbol_graph.py::get_symbol_dependencies() was a stub that detected index.sqlite exists but always returned [] with a TODO Phase 2 comment. Callers expecting precomputed dependencies (calls, imports, referenced_by) received no results.

# Steps to Reproduce
1. Index a repo with index_repo()
2. Call get_symbol_dependencies("StepExecutor", project_root)
3. Observe empty list returned despite index.sqlite containing graph data

# Expected Behavior
Function should query GraphStorage (index.sqlite), return list of dicts {file, symbol, snippet, type} for outgoing (calls) and incoming (referenced_by) neighbors.

# Actual Behavior
Returned [] because implementation was not wired to SQLite.

# Root Cause
Stub left as TODO Phase 2; GraphStorage backend existed but symbol_graph module did not use it.

# Fix
Implemented get_symbol_dependencies() to instantiate GraphStorage(index_path), resolve symbol via get_symbol_by_name (with get_symbols_like fallback), query get_neighbors(direction="out") and get_neighbors(direction="in"), and return structured results compatible with context builder.

# Status
resolved
