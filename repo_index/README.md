# Repo Index Module (`repo_index/`)

Repository scanning + parsing + symbol extraction. This module produces the **raw structured facts** (symbols and dependency edges) that power the symbol graph (`repo_graph/`) and higher-level retrieval.

## Responsibilities

- **Scan** the repository for source files (currently Python-focused).
- **Parse** files into syntax trees.
- **Extract symbols** (classes, functions, methods, etc.) with locations and docstrings.
- **Extract dependencies/edges** between symbols for graph construction.
- **Write artifacts** used by downstream systems:
  - `symbols.json` (symbol records)
  - `index.sqlite` (symbol graph; built via `repo_graph.build_graph`)
  - optional embedding index (when dependencies are available)

## Key entrypoints

- **Programmatic**: `repo_index/indexer.py`
  - `scan_repo(root_dir)` → list of symbol records
  - `index_repo(root_dir, ...)` → (symbols, db_path)
  - `update_index_for_file(file_path, root_dir?)` → incremental update for one file
- **CLI**: `repo_index/index_repo.py`
  - `python -m repo_index.index_repo . --verbose`

## Ignore rules

Indexing can honor `.gitignore` patterns (default behavior). Use `--no-gitignore` to include ignored files in indexing runs when debugging.

## Performance model

- Parallel parsing is controlled by `INDEX_PARALLEL_WORKERS` (default from env; capped).
- Indexing is designed to be “fast enough” for local repos; avoid turning it into a long-running service inside this module (daemonization belongs elsewhere).

## Integration points

- Builds `index.sqlite` by calling into `repo_graph.graph_builder.build_graph(...)`.
- Retrieval uses index artifacts for symbol lookups, graph expansion, and repo map generation.

## Extension points

- **Multi-language support**: add parsers/extractors in a way that preserves deterministic outputs and doesn’t break existing Python extraction.
- **Embedding index**: keep optional and failure-tolerant; downstream retrieval must remain correct without embeddings.

