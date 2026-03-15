"""Apply validated patches safely with rollback on failure."""

import ast
import logging
import os
from pathlib import Path

from editing.ast_patcher import apply_patch, generate_code, load_ast, load_ast_from_source
from editing.patch_validator import validate_patch

logger = logging.getLogger(__name__)

MAX_FILES_PER_EDIT = 5
MAX_PATCH_LINES = 200


def _resolve_path(file_path: str, project_root: str | None) -> Path:
    """Resolve file path to absolute Path."""
    root = project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    root_path = Path(root).resolve()
    p = Path(file_path)
    if not p.is_absolute():
        p = root_path / file_path
    return p.resolve()


def execute_patch(patch_plan: dict, project_root: str | None = None) -> dict:
    """
    Apply validated patches safely.
    patch_plan: { "changes": [ {"file": str, "patch": dict}, ... ] }
    patch: {symbol, action, target_node, code}
    Returns {success, files_modified?, patches_applied?, error?, reason?, file?}
    """
    changes = patch_plan.get("changes", [])
    if not changes:
        return {"success": True, "files_modified": [], "patches_applied": 0}

    # Safeguard: count unique files (multiple patches per file allowed)
    unique_files = len({c.get("file", "") for c in changes if c.get("file")})
    if unique_files > MAX_FILES_PER_EDIT:
        return {
            "success": False,
            "error": "safeguard_exceeded",
            "reason": f"max files exceeded ({unique_files} > {MAX_FILES_PER_EDIT})",
            "file": changes[0].get("file", ""),
        }
    for c in changes:
        patch = c.get("patch", {})
        code = patch.get("code", "")
        if code and code.count("\n") >= MAX_PATCH_LINES:
            return {
                "success": False,
                "error": "safeguard_exceeded",
                "reason": f"max patch size exceeded ({code.count(chr(10)) + 1} lines > {MAX_PATCH_LINES})",
                "file": c.get("file", ""),
            }

    originals: dict[str, str] = {}
    patched_content: dict[str, str] = {}

    for change in changes:
        file_path = change.get("file", "")
        patch = change.get("patch", {})
        if not file_path or not patch:
            continue

        abs_path = _resolve_path(file_path, project_root)
        abs_path_str = str(abs_path)
        logger.info("[patch_executor] applying patch file=%s", abs_path)

        try:
            if not abs_path.exists():
                return {
                    "success": False,
                    "error": "patch_failed",
                    "reason": f"file not found: {abs_path}",
                    "file": abs_path_str,
                }

            # Use patched content if we've already applied patches to this file
            if abs_path_str in patched_content:
                current_source = patched_content[abs_path_str]
                loaded = load_ast_from_source(current_source)
            else:
                original = abs_path.read_text(encoding="utf-8")
                originals[abs_path_str] = original
                loaded = load_ast(str(abs_path))

            if loaded is None:
                return {
                    "success": False,
                    "error": "patch_failed",
                    "reason": f"failed to parse: {abs_path}",
                    "file": abs_path_str,
                }

            tree, source_bytes = loaded
            new_bytes = apply_patch(tree, source_bytes, patch)
            new_code = generate_code(tree, new_bytes)

            # Phase 4: ast.parse pre-check for Python files before validation
            if abs_path.suffix.lower() == ".py" and new_code:
                try:
                    ast.parse(new_code)
                except SyntaxError as e:
                    logger.warning("[patch_executor] ast.parse pre-check failed: %s", e)
                    return {
                        "success": False,
                        "error": "patch_failed",
                        "reason": f"Python syntax error: {e}",
                        "file": abs_path_str,
                    }

            result = validate_patch(abs_path_str, new_code)
            if not result.get("valid", True):
                logger.warning("[patch_executor] validation failed for %s: %s", abs_path, result.get("errors"))
                logger.info("[patch_executor] rollback triggered")
                for path, content in originals.items():
                    Path(path).write_text(content, encoding="utf-8")
                return {
                    "success": False,
                    "error": "patch_failed",
                    "reason": "; ".join(result.get("errors", ["validation failed"])),
                    "file": abs_path_str,
                }

            logger.info("[patch_executor] validation passed")
            patched_content[abs_path_str] = new_code

        except ValueError as e:
            logger.warning("[patch_executor] apply_patch error: %s", e)
            logger.info("[patch_executor] rollback triggered")
            for path, content in originals.items():
                Path(path).write_text(content, encoding="utf-8")
            return {
                "success": False,
                "error": "patch_failed",
                "reason": str(e),
                "file": abs_path_str,
            }
        except Exception as e:
            logger.exception("[patch_executor] unexpected error: %s", e)
            logger.info("[patch_executor] rollback triggered")
            for path, content in originals.items():
                Path(path).write_text(content, encoding="utf-8")
            return {
                "success": False,
                "error": "patch_failed",
                "reason": str(e),
                "file": abs_path_str,
            }

    # All valid: write files
    for abs_path_str, new_code in patched_content.items():
        Path(abs_path_str).write_text(new_code, encoding="utf-8")

    files_modified = list(patched_content.keys())
    logger.info("[patch_executor] files_modified=%d", len(files_modified))
    return {
        "success": True,
        "files_modified": files_modified,
        "patches_applied": len(changes),
    }
