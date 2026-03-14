"""Editing utilities: diff planning, AST patching, validation, and safe execution."""

from editing.ast_patcher import apply_patch, generate_code, load_ast
from editing.conflict_resolver import resolve_conflicts
from editing.merge_strategies import merge_sequential, merge_three_way
from editing.diff_planner import plan_diff
from editing.test_repair_loop import run_with_repair
from editing.patch_executor import execute_patch
from editing.patch_validator import validate_patch

__all__ = [
    "resolve_conflicts",
    "merge_sequential",
    "merge_three_way",
    "run_with_repair",
    "plan_diff",
    "execute_patch",
    "apply_patch",
    "validate_patch",
    "load_ast",
    "generate_code",
]
