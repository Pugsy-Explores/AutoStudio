"""
Minimal syntax validation layer. Runs before patch_verification.
Ensures generated patches produce syntactically valid code.
No heuristics, no execution, pure syntax only.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

from editing.ast_patcher import apply_patch, load_ast_from_source

logger = logging.getLogger(__name__)

_PYTHON_EXTENSIONS = frozenset({".py", ".pyi"})


def apply_patch_in_memory(
    proposal: dict,
    full_file_content: str | None,
    project_root: str | None = None,
) -> str | None:
    """
    Apply proposed patch to a temporary copy of file content. No disk write.
    Returns patched content string, or None if patch cannot be applied.
    """
    patch = proposal.get("patch") or {}
    if not isinstance(patch, dict):
        return None

    action = patch.get("action", "")
    file_path = proposal.get("file", "")

    # text_sub: simple string replace
    if action == "text_sub":
        old = patch.get("old", "")
        new = patch.get("new", "")
        if full_file_content is None or old not in full_file_content:
            return None
        return full_file_content.replace(old, new, 1)

    # AST-based patches: insert, replace, delete, module_append
    if action in ("insert", "replace", "delete"):
        content = full_file_content if full_file_content is not None else ""
        source_bytes = content.encode("utf-8")

        loaded = load_ast_from_source(source_bytes)
        if loaded is None:
            return None
        tree, src_bytes = loaded
        try:
            new_bytes = apply_patch(tree, src_bytes, patch)
            return new_bytes.decode("utf-8", errors="replace")
        except (ValueError, Exception):
            return None

    return None


def validate_syntax(
    proposal: dict,
    full_file_content: str | None,
    project_root: str | None = None,
) -> dict:
    """
    Validate that applying the patch produces syntactically valid Python.
    Returns {valid: bool, error: str|None, error_type: str|None, file: str, skipped?: bool, language?: str}.
    """
    file_path = proposal.get("file", "")
    patched = apply_patch_in_memory(proposal, full_file_content, project_root)

    # Cannot apply in memory -> reject (keeps invariant: syntax layer = "can this produce runnable code?")
    if patched is None:
        return {
            "valid": False,
            "error": "patch_apply_failed",
            "error_type": "patch_apply_failed",
            "file": file_path,
        }

    # Non-Python files: skip syntax check (explicit so logs are unambiguous)
    suffix = Path(file_path).suffix.lower() if file_path else ""
    if suffix not in _PYTHON_EXTENSIONS:
        return {
            "valid": True,
            "error": None,
            "error_type": None,
            "file": file_path,
            "skipped": True,
            "language": "non_python",
        }

    # Python: ast.parse
    try:
        ast.parse(patched)
        return {"valid": True, "error": None, "error_type": None, "file": file_path}
    except SyntaxError as e:
        return {
            "valid": False,
            "error": str(e),
            "error_type": "syntax_error",
            "file": file_path,
        }


def validate_syntax_plan(
    patch_plan: dict,
    snapshot: dict,
    project_root: str | None = None,
) -> tuple[bool, dict | None]:
    """
    Validate syntax for all changes in patch plan.
    Applies changes sequentially per file (patch1 -> patch2 -> patch3) so collectively
    valid patches are checked on the accumulated result, not independently.
    Returns (all_valid, first_failure_result).
    snapshot: dict mapping Path -> content (from _snapshot_files).
    """
    changes = patch_plan.get("changes") or []
    root = Path(project_root).resolve() if project_root else Path.cwd()
    content_map: dict[Path, str | None] = {}  # per-file content after each applied change

    for change in changes:
        file_path = change.get("file", "")
        if not file_path:
            continue
        path = (root / file_path).resolve()

        # Use accumulated content for this file, else snapshot, else read from disk
        content = content_map.get(path)
        if content is None:
            content = snapshot.get(path)
        if content is None and path.exists():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                content = ""

        patched = apply_patch_in_memory(change, content, project_root)
        if patched is None:
            return False, {
                "valid": False,
                "error": "patch_apply_failed",
                "error_type": "patch_apply_failed",
                "file": file_path,
            }

        # Non-Python: skip syntax, but record for next change to same file
        suffix = Path(file_path).suffix.lower() if file_path else ""
        if suffix not in _PYTHON_EXTENSIONS:
            content_map[path] = patched
            continue

        # Python: ast.parse on accumulated result
        try:
            ast.parse(patched)
        except SyntaxError as e:
            return False, {
                "valid": False,
                "error": str(e),
                "error_type": "syntax_error",
                "file": file_path,
            }

        content_map[path] = patched

    return True, None
