# Repo Graph Module (`repo_graph/`)

Symbol graph storage + queries + repo map generation. This module provides the **structural backbone** for retrieval expansion and architectural understanding by building a graph of symbols and their relationships.

## Responsibilities

- **Graph build**: write a symbol/edge graph into a persistent store (SQLite).
- **Graph storage/query**: lookup symbols, expand neighbors, and compute change impact.
- **Repo map**: generate a high-level architectural map (`repo_map.json`) from the symbol graph.

## Public API (package exports)

`repo_graph/__init__.py` exports:

- `build_graph(...)` (`repo_graph/graph_builder.py`)
- `GraphStorage` (`repo_graph/graph_storage.py`)
- `find_symbol(...)`, `expand_neighbors(...)` (`repo_graph/graph_query.py`)
- `build_repo_map(...)`, `build_repo_map_from_storage(...)` (`repo_graph/repo_map_builder.py`)
- `update_repo_map_for_file(...)` (`repo_graph/repo_map_updater.py`)
- `detect_change_impact(...)` (`repo_graph/change_detector.py`)

## Key data artifacts

Default paths are configured in `config/repo_graph_config.py` and typically live under:

- `.symbol_graph/symbols.json`
- `.symbol_graph/index.sqlite`
- `.symbol_graph/repo_map.json`

The repo map is consumed as a “high-level orientation” layer (e.g. to seed retrieval and to avoid hallucinating structure).

## How it fits the system

- **Indexing** (`repo_index/`) extracts symbols + dependency edges from source code.
- **Graph build** (`repo_graph/graph_builder.py`) persists them into SQLite for fast lookup/expansion.
- **Retrieval** can expand candidate context via symbol neighbors and call edges.

## Extension points

- **New edge types**: add in graph schema/storage and ensure query functions treat them deterministically.
- **Repo map schema**: prefer additive changes; preserve backward compatibility (the module already supports spec + legacy formats).

