# Repo Index Module (`repo_index/`)

Scan, parse, and extract **symbols and edges** from the repository into structured artifacts consumed by **`repo_graph/`** and **`agent/retrieval/`**.

## Responsibilities

- Discover source files (Python-focused).
- Parse ASTs; extract classes, functions, methods, locations, docstrings.
- Emit dependency edges for graph build.
- Write **`symbols.json`**, SQLite via `repo_graph.build_graph`, optional embeddings.

## Entrypoints

- **API:** `repo_index/indexer.py` — `scan_repo`, `index_repo`, `update_index_for_file`
- **CLI:** `python -m repo_index.index_repo .`

## Dependencies

- **Uses:** parsers, `repo_graph` for graph persistence.
- **Used by:** indexing jobs, retrieval, repo map generation.

## Extension

Add language support or extractors in indexer modules; keep outputs compatible with **`repo_graph`** schema.
