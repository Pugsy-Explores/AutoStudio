"""Apply validated patches safely with rollback on failure."""

import ast
import logging
import os
from pathlib import Path

from editing.ast_patcher import apply_patch, generate_code, load_ast, load_ast_from_source
from editing.patch_effectiveness import MAX_SNIPPET_LEN, assess_after_content_change, assess_text_sub
from editing.patch_validator import validate_patch

logger = logging.getLogger(__name__)

MAX_FILES_PER_EDIT = 5
MAX_PATCH_LINES = 200

FORBIDDEN_PATH_PATTERNS = (
    ".env",
    ".env.",
    "secrets/",
    ".key",
    ".pem",
    "credentials",
    "config/secrets",
)

_INDEX_ARTIFACT_NAMES = frozenset({"index.sqlite", "repo_map.json", "symbols.json"})


def _is_non_source_edit_target(resolved: Path) -> bool:
    """Block edits to index outputs, DBs, and anything under .symbol_graph."""
    try:
        parts_lower = {p.lower() for p in resolved.parts}
    except (ValueError, OSError):
        return True
    if ".symbol_graph" in parts_lower:
        return True
    name = resolved.name.lower()
    if name in _INDEX_ARTIFACT_NAMES:
        return True
    if name.endswith((".sqlite", ".db")):
        return True
    return False


def _resolve_path(file_path: str, project_root: str | None) -> Path:
    """Resolve file path to absolute Path and ensure it is inside project root."""
    root = project_root or os.environ.get("SERENA_PROJECT_DIR") or os.getcwd()
    root_path = Path(root).resolve()
    p = Path(file_path)
    if not p.is_absolute():
        p = root_path / file_path
    resolved = p.resolve()
    try:
        resolved.relative_to(root_path)
    except ValueError as e:
        raise ValueError(f"Path {resolved} is outside project root") from e
    return resolved


def _is_forbidden_path(file_path: str) -> bool:
    """Return True if the path looks like a secrets/env path that must not be edited."""
    lower = file_path.lower()
    if any(pattern in lower for pattern in FORBIDDEN_PATH_PATTERNS):
        return True
    return False


def _preflight_validate_patch(patch: dict, file_path: str, abs_path: Path) -> tuple[bool, str | None]:
    """
    Validate patch schema and basic sanity before apply.
    Returns (valid, reject_reason). reject_reason is None when valid.
    """
    if not patch or not isinstance(patch, dict):
        return False, "empty_patch"
    action = patch.get("action")
    if not action:
        return False, "invalid_patch_syntax"
    if action == "text_sub":
        old = patch.get("old", "")
        new = patch.get("new", "")
        if not str(old).strip():
            return False, "empty_patch"
        return True, None
    if action in ("insert", "replace", "delete"):
        if action != "delete" and not str(patch.get("code", "")).strip():
            return False, "empty_patch"
        target_node = patch.get("target_node", "")
        if target_node == "module_append":
            return True, None
        if target_node not in (
            "function_body_start", "function_body", "class_body_start", "class_body",
            "statement", "statement_after", "if_block", "try_block", "with_block", "for_block",
        ):
            return False, "invalid_patch_syntax"
        return True, None
    return False, "invalid_patch_syntax"


def _merge_effectiveness_telemetry(steps: list[dict], last: dict | None = None) -> dict:
    """Aggregate per-step effectiveness for JSON telemetry (bounded)."""
    all_s = list(steps)
    if last:
        all_s.append(last)
    if not all_s:
        return {
            "patch_effective_change": True,
            "patch_effective_reason": None,
            "changed_region_detected": False,
            "target_region_before": None,
            "target_region_after": None,
            "meaningful_diff_line_count": 0,
            "rejected_for_noop_or_unchanged": False,
            "patch_effectiveness_steps": [],
        }
    total_lines = sum(int(s.get("meaningful_diff_line_count") or 0) for s in all_s)
    tail = all_s[-1]
    return {
        "patch_effective_change": all(s.get("patch_effective_change") for s in all_s),
        "patch_effective_reason": tail.get("patch_effective_reason"),
        "changed_region_detected": any(s.get("changed_region_detected") for s in all_s),
        "target_region_before": (tail.get("target_region_before") or "")[:MAX_SNIPPET_LEN] if tail else None,
        "target_region_after": (tail.get("target_region_after") or "")[:MAX_SNIPPET_LEN] if tail else None,
        "meaningful_diff_line_count": total_lines,
        "rejected_for_noop_or_unchanged": any(s.get("rejected_for_noop_or_unchanged") for s in all_s),
        "patch_effectiveness_steps": all_s[:20],
    }


def _classify_patch_failure(reason: str) -> str:
    r = (reason or "").lower()
    if "symbol not found" in r:
        return "symbol_not_found"
    if "target is directory" in r or "is a directory" in r:
        return "target_is_directory"
    if "empty_patch" in r:
        return "empty_patch"
    if "patch_anchor" in r or ("not found" in r and "symbol" not in r):
        return "patch_anchor_not_found"
    if "validation" in r or "syntax error" in r:
        return "patch_apply_conflict"
    return "patch_apply_conflict"


def execute_patch(patch_plan: dict, project_root: str | None = None) -> dict:
    """
    Apply validated patches safely.
    patch_plan: { "changes": [ {"file": str, "patch": dict}, ... ] }
    patch: {symbol, action, target_node, code}
    Returns {success, files_modified?, patches_applied?, error?, reason?, file?}
    """
    changes = patch_plan.get("changes", [])
    if not changes:
        return {
            "success": True,
            "files_modified": [],
            "patches_applied": 0,
            "patch_parse_ok": None,
            "patch_apply_ok": True,
            "patch_reject_reason": None,
            "failure_reason_code": None,
            "patch_effectiveness": _merge_effectiveness_telemetry([]),
        }

    # Safeguard: count unique files (multiple patches per file allowed)
    unique_files = len({c.get("file", "") for c in changes if c.get("file")})
    if unique_files > MAX_FILES_PER_EDIT:
        return {
            "success": False,
            "error": "safeguard_exceeded",
            "reason": f"max files exceeded ({unique_files} > {MAX_FILES_PER_EDIT})",
            "file": changes[0].get("file", ""),
            "patch_parse_ok": False,
            "patch_apply_ok": False,
            "patch_reject_reason": "safeguard_exceeded",
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
                "patch_parse_ok": False,
                "patch_apply_ok": False,
                "patch_reject_reason": "safeguard_exceeded",
            }
        action = patch.get("action")
        if action == "delete" and not patch.get("target_node"):
            return {
                "success": False,
                "error": "forbidden_delete",
                "reason": "Cannot delete entire file",
                "file": c.get("file", ""),
                "patch_parse_ok": False,
                "patch_apply_ok": False,
                "patch_reject_reason": "forbidden_delete",
            }

    originals: dict[str, str] = {}
    patched_content: dict[str, str] = {}
    applied_step_count = 0
    effectiveness_steps: list[dict] = []

    for change in changes:
        file_path = change.get("file", "")
        patch = change.get("patch", {})
        if not file_path or not patch:
            continue

        if _is_forbidden_path(file_path):
            return {
                "success": False,
                "error": "forbidden_path",
                "reason": f"Refusing to modify forbidden path: {file_path}",
                "file": file_path,
                "patch_parse_ok": False,
                "patch_apply_ok": False,
                "patch_reject_reason": "non_source_target",
            }

        try:
            abs_path = _resolve_path(file_path, project_root)
        except ValueError as e:
            return {
                "success": False,
                "error": "path_outside_repo",
                "reason": str(e),
                "file": file_path,
                "patch_parse_ok": False,
                "patch_apply_ok": False,
                "patch_reject_reason": "target_not_found",
            }
        abs_path_str = str(abs_path)
        logger.info("[patch_executor] applying patch file=%s", abs_path)

        preflight_ok, preflight_reason = _preflight_validate_patch(patch, file_path, abs_path)
        if not preflight_ok:
            return {
                "success": False,
                "error": "patch_failed",
                "reason": f"preflight rejected: {preflight_reason}",
                "file": abs_path_str,
                "failure_reason_code": preflight_reason or "invalid_patch_syntax",
                "patch_parse_ok": False,
                "patch_apply_ok": False,
                "patch_reject_reason": preflight_reason or "invalid_patch_syntax",
            }

        try:
            if not abs_path.exists():
                return {
                    "success": False,
                    "error": "patch_failed",
                    "reason": f"file not found: {abs_path}",
                    "file": abs_path_str,
                    "failure_reason_code": "target_not_found",
                    "patch_parse_ok": False,
                    "patch_apply_ok": False,
                    "patch_reject_reason": "target_not_found",
                }
            if abs_path.is_dir():
                return {
                    "success": False,
                    "error": "patch_failed",
                    "reason": f"target is directory: {abs_path}",
                    "file": abs_path_str,
                    "failure_reason_code": "target_is_directory",
                    "patch_parse_ok": False,
                    "patch_apply_ok": False,
                    "patch_reject_reason": "target_is_directory",
                }

            if _is_non_source_edit_target(abs_path):
                return {
                    "success": False,
                    "error": "patch_failed",
                    "reason": f"refusing to edit non-source or index artifact: {abs_path}",
                    "file": abs_path_str,
                    "failure_reason_code": "non_source_target",
                    "patch_parse_ok": False,
                    "patch_apply_ok": False,
                    "patch_reject_reason": "non_source_target",
                }

            # Non-Python files only support text_sub; skip AST path
            if abs_path.suffix.lower() not in (".py", ".pyi") and patch.get("action") != "text_sub":
                continue
            if patch.get("action") == "text_sub":
                old = patch.get("old", "")
                new = patch.get("new", "")
                if not str(old).strip():
                    return {
                        "success": False,
                        "error": "patch_failed",
                        "reason": "text_sub requires non-empty old",
                        "file": abs_path_str,
                        "failure_reason_code": "empty_patch",
                        "patch_parse_ok": False,
                        "patch_apply_ok": False,
                        "patch_reject_reason": "empty_patch",
                    }
                if abs_path_str not in originals:
                    originals[abs_path_str] = abs_path.read_text(encoding="utf-8")
                src = patched_content.get(abs_path_str) or originals[abs_path_str]
                ok_eff, eff_reason, new_src_eff, eff_extra = assess_text_sub(
                    source_before=src, old=old, new=new
                )
                if not ok_eff and eff_reason is not None:
                    rpt = (eff_extra or {}).get("patch_effectiveness_step") or {}
                    return {
                        "success": False,
                        "error": "patch_failed",
                        "reason": f"patch effectiveness rejected: {eff_reason}",
                        "file": abs_path_str,
                        "failure_reason_code": eff_reason,
                        "patch_parse_ok": True,
                        "patch_apply_ok": False,
                        "patch_reject_reason": eff_reason,
                        "patch_effectiveness": _merge_effectiveness_telemetry(effectiveness_steps, rpt),
                    }
                if old not in src:
                    return {
                        "success": False,
                        "error": "patch_failed",
                        "reason": f"text_sub old snippet not found in {abs_path}",
                        "file": abs_path_str,
                        "failure_reason_code": "target_not_found",
                        "patch_parse_ok": False,
                        "patch_apply_ok": False,
                        "patch_reject_reason": "target_not_found",
                    }
                new_src = new_src_eff if new_src_eff is not None else src.replace(old, new, 1)
                step_rpt = (eff_extra or {}).get("patch_effectiveness_step")
                if step_rpt:
                    effectiveness_steps.append(step_rpt)
                if abs_path.suffix.lower() == ".py" and new_src.strip():
                    try:
                        ast.parse(new_src)
                    except SyntaxError as e:
                        logger.warning("[patch_executor] text_sub ast.parse failed: %s", e)
                        return {
                            "success": False,
                            "error": "patch_failed",
                            "reason": f"Python syntax error after text_sub: {e}",
                            "file": abs_path_str,
                            "failure_reason_code": "invalid_patch_syntax",
                            "patch_parse_ok": False,
                            "patch_apply_ok": False,
                            "patch_reject_reason": "invalid_patch_syntax",
                        }
                patched_content[abs_path_str] = new_src
                applied_step_count += 1
                continue

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
                    "failure_reason_code": "invalid_patch_syntax",
                    "patch_parse_ok": False,
                    "patch_apply_ok": False,
                    "patch_reject_reason": "invalid_patch_syntax",
                }

            if abs_path_str in patched_content:
                ast_source_before = patched_content[abs_path_str]
            else:
                ast_source_before = originals[abs_path_str]

            eff_module_append_code = patch.get("code") if patch.get("target_node") == "module_append" else None
            tree, source_bytes = loaded
            try:
                new_bytes = apply_patch(tree, source_bytes, patch)
            except ValueError as e_sym:
                err_sym = str(e_sym)
                if "Symbol not found" in err_sym and patch.get("target_node") != "module_append":
                    loaded_fb = (
                        load_ast_from_source(patched_content[abs_path_str])
                        if abs_path_str in patched_content
                        else load_ast(str(abs_path))
                    )
                    if loaded_fb is None:
                        raise
                    t2, sb2 = loaded_fb
                    fb_patch = {
                        "symbol": "",
                        "action": "insert",
                        "target_node": "module_append",
                        "code": patch.get("code", ""),
                    }
                    eff_module_append_code = fb_patch.get("code")
                    logger.info("[patch_executor] symbol miss; retry file-anchored module_append")
                    new_bytes = apply_patch(t2, sb2, fb_patch)
                else:
                    raise
            new_code = generate_code(tree, new_bytes)

            ok_ast, ast_reason, ast_extra = assess_after_content_change(
                source_before=ast_source_before,
                source_after=new_code,
                patch_kind="structured",
                old_text=None,
                module_append_code=eff_module_append_code,
            )
            if not ok_ast:
                rpt = (ast_extra or {}).get("patch_effectiveness_step") or {}
                return {
                    "success": False,
                    "error": "patch_failed",
                    "reason": f"patch effectiveness rejected: {ast_reason}",
                    "file": abs_path_str,
                    "failure_reason_code": ast_reason,
                    "patch_parse_ok": True,
                    "patch_apply_ok": False,
                    "patch_reject_reason": ast_reason,
                    "patch_effectiveness": _merge_effectiveness_telemetry(effectiveness_steps, rpt),
                }
            ast_step = (ast_extra or {}).get("patch_effectiveness_step")
            if ast_step:
                effectiveness_steps.append(ast_step)

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
                        "failure_reason_code": "invalid_patch_syntax",
                        "patch_parse_ok": False,
                        "patch_apply_ok": False,
                        "patch_reject_reason": "invalid_patch_syntax",
                    }

            # validate_patch uses compile() — Python only; skip for .md and other non-Python
            if abs_path.suffix.lower() in (".py", ".pyi"):
                result = validate_patch(abs_path_str, new_code)
            else:
                result = {"valid": True, "errors": []}
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
                    "failure_reason_code": "patch_apply_conflict",
                    "patch_parse_ok": True,
                    "patch_apply_ok": False,
                    "patch_reject_reason": "patch_apply_conflict",
                }

            logger.info("[patch_executor] validation passed")
            patched_content[abs_path_str] = new_code
            applied_step_count += 1

        except ValueError as e:
            logger.warning("[patch_executor] apply_patch error: %s", e)
            logger.info("[patch_executor] rollback triggered")
            for path, content in originals.items():
                Path(path).write_text(content, encoding="utf-8")
            try:
                from agent.observability.metrics import record_metric
                record_metric("patch_failure", 1.0, project_root=project_root, append_jsonl=False)
            except Exception:
                pass
            return {
                "success": False,
                "error": "patch_failed",
                "reason": str(e),
                "file": abs_path_str,
                "failure_reason_code": _classify_patch_failure(str(e)),
                "patch_parse_ok": False,
                "patch_apply_ok": False,
                "patch_reject_reason": _classify_patch_failure(str(e)),
            }
        except Exception as e:
            logger.exception("[patch_executor] unexpected error: %s", e)
            logger.info("[patch_executor] rollback triggered")
            for path, content in originals.items():
                Path(path).write_text(content, encoding="utf-8")
            try:
                from agent.observability.metrics import record_metric
                record_metric("patch_failure", 1.0, project_root=project_root, append_jsonl=False)
            except Exception:
                pass
            return {
                "success": False,
                "error": "patch_failed",
                "reason": str(e),
                "file": abs_path_str,
                "failure_reason_code": _classify_patch_failure(str(e)),
                "patch_parse_ok": False,
                "patch_apply_ok": False,
                "patch_reject_reason": _classify_patch_failure(str(e)),
            }

    # All valid: write files
    for abs_path_str, new_code in patched_content.items():
        Path(abs_path_str).write_text(new_code, encoding="utf-8")

    files_modified = list(patched_content.keys())
    logger.info("[patch_executor] files_modified=%d", len(files_modified))
    try:
        from agent.observability.metrics import record_metric
        record_metric("patch_success", 1.0, project_root=project_root, append_jsonl=False)
    except Exception:
        pass
    return {
        "success": True,
        "files_modified": files_modified,
        "patches_applied": applied_step_count,
        "patch_parse_ok": True,
        "patch_apply_ok": True,
        "patch_reject_reason": None,
        "failure_reason_code": None,
        "patch_effectiveness": _merge_effectiveness_telemetry(effectiveness_steps),
    }
