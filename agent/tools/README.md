# Tools (`agent/tools/`)

Tool adapters exposed to the execution layer. These are the concrete implementations the dispatcher can invoke for SEARCH/READ/EDIT/INFRA steps.

## Responsibilities

- Provide thin adapters around:
  - filesystem reads/writes (under policy constraints)
  - code search and candidate gathering
  - context assembly (symbols, references, call-chain context)
  - terminal command execution (under safety policy)
  - documentation lookup adapters

## Public API

Exports from `agent/tools/__init__.py` include:

- Search: `search_code`, `search_candidates`
- Context: `build_context`, `find_referencing_symbols`, `read_symbol_body`
- Filesystem: `read_file`, `write_file`, `list_files`
- Docs: `lookup_docs`
- Terminal: `run_command`

## Invariants

- Tools must be invoked via the **dispatcher/policy engine**, not directly from LLM reasoning.
- Tools must be **trace-logged** with inputs/outputs and any failures.
- Writes must remain bounded and respect forbidden-path rules (secrets/env/keys).

