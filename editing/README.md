# Editing Module (`editing/`)

Safe, validated code-editing pipeline. This module converts “what to change” into **bounded, validated patches**, applies them via AST-aware patching when possible, and provides rollback-safe execution.

## Responsibilities

- **Diff planning**: propose minimal, bounded edits.
- **AST patching**: apply structured patches to parsed syntax trees (primary path).
- **Patch validation**: reject unsafe or invalid patch plans.
- **Patch execution**: apply patches with safeguards (max files/lines), reject forbidden paths, and rollback on failure.
- **Repair loop**: integrate edit→test→fix retry strategies (when invoked via runtime loop).

## Public API (package exports)

The package exports a curated surface in `editing/__init__.py`:

- `plan_diff(...)` (`editing/diff_planner.py`)
- `validate_patch(...)` (`editing/patch_validator.py`)
- `execute_patch(...)` (`editing/patch_executor.py`)
- AST helpers: `load_ast`, `apply_patch`, `generate_code` (`editing/ast_patcher.py`)
- Merge/conflict utilities: `resolve_conflicts`, `merge_sequential`, `merge_three_way`
- Repair helper: `run_with_repair` (`editing/test_repair_loop.py`)

## Safety model (must-haves)

`editing/patch_executor.py` enforces:

- **File scope safety**: paths must resolve inside `project_root`.
- **Forbidden targets**: refuses obvious secret/env patterns (`.env`, `secrets/`, keys, credentials).
- **Budget limits**: maximum unique files per edit and maximum patch size (lines).
- **Validation-first**: validate patch output before writing; on validation failure, rollback to original content.

This module is one of the core safety layers; avoid bypassing it.

## Integration points

- Called from agent execution for EDIT steps (via dispatcher/policy engine).
- Works with runtime loop (`agent/runtime/`) to validate syntax before tests and to rollback deterministically.

## Extension points

- **New patch validators**: extend `patch_validator.py` (keep behavior deterministic and explainable).
- **Additional languages**: add syntax/validator hooks carefully; preserve existing Python AST path.
- **Merge strategies**: implement in `merge_strategies.py` without changing the public API.

