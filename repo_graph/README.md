# Repo Graph Module (`repo_graph/`)

**Symbol graph** storage (SQLite), queries, **repo map** generation, and change-impact helpers.

## Responsibilities

- **`build_graph`** — persist symbols/edges from `repo_index`.
- **`GraphStorage`** — persistent store.
- **`find_symbol`**, **`expand_neighbors`** — graph navigation.
- **`build_repo_map`** — high-level `repo_map.json` for retrieval orientation.

## Artifacts

Default paths under `config/repo_graph_config.py`, e.g.:

- `.symbol_graph/symbols.json`
- `.symbol_graph/index.sqlite`
- `.symbol_graph/repo_map.json`

## Integration

- **Upstream:** `repo_index/` produces raw symbols/edges.
- **Downstream:** `agent/retrieval/` uses graph expansion, anchors, and repo map.

## Public API

See `repo_graph/__init__.py` exports: `build_graph`, `GraphStorage`, `build_repo_map`, `detect_change_impact`, etc.
