"""
Minimal deterministic patch verification layer.

Runs before apply to reject invalid patches. No heuristics, no semantic guessing.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _resolve_for_comparison(path_str: str | None, project_root: str | None) -> Path | None:
    """Resolve path to absolute Path for comparison. Returns None if invalid."""
    if not path_str or not str(path_str).strip():
        return None
    try:
        root = Path(project_root).resolve() if project_root else Path.cwd()
        p = Path(str(path_str).strip())
        if not p.is_absolute():
            p = root / p
        return p.resolve()
    except (ValueError, OSError):
        return None


def verify_patch(
    proposal: dict,
    full_file_content: str | None,
    instruction: str,
    binding: dict | None,
    project_root: str | None = None,
) -> dict:
    """
    Deterministic verification of a patch before apply.

    Args:
        proposal: Change dict with "file" and "patch" keys.
        full_file_content: Current file content (or None if unknown).
        instruction: Task instruction (unused; for future extensibility).
        binding: Edit binding with "file" key (canonical target).
        project_root: For path normalization.

    Returns:
        {
            "valid": True/False,
            "reason": "..." (when invalid),
            "checks": {
                "has_effect": True/False,
                "targets_correct_file": True/False,
                "is_local": True/False
            }
        }
    """
    _ = instruction  # Reserved for future use
    file_path = proposal.get("file", "")
    patch = proposal.get("patch") or {}
    if not isinstance(patch, dict):
        return {
            "valid": False,
            "reason": "invalid_patch_syntax",
            "checks": {
                "has_effect": False,
                "targets_correct_file": False,
                "is_local": False,
            },
        }

    action = patch.get("action", "")
    checks = {
        "has_effect": True,
        "targets_correct_file": True,
        "is_local": True,
    }

    # --- 1. has_effect ---
    if action == "text_sub":
        old = patch.get("old", "")
        new = patch.get("new", "")
        if old == new:
            checks["has_effect"] = False
    elif action == "insert":
        code = patch.get("code", "")
        if not code or not str(code).strip():
            checks["has_effect"] = False
        elif full_file_content is not None:
            # Inserted code must NOT already exist in file
            code_stripped = str(code).strip()
            if code_stripped in full_file_content:
                checks["has_effect"] = False
    else:
        # Unknown action: cannot verify has_effect; pass through
        pass

    # --- 2. targets_correct_file ---
    binding_file = ""
    if isinstance(binding, dict) and binding:
        binding_file = binding.get("file") or ""
    if binding_file and file_path:
        proposal_resolved = _resolve_for_comparison(file_path, project_root)
        binding_resolved = _resolve_for_comparison(binding_file, project_root)
        if proposal_resolved is not None and binding_resolved is not None:
            if proposal_resolved != binding_resolved:
                checks["targets_correct_file"] = False

    # --- 3. is_local (text_sub: old must exist in full_file_content) ---
    if action == "text_sub":
        old = patch.get("old", "")
        if full_file_content is None:
            checks["is_local"] = None  # Cannot determine
        elif not old:
            checks["is_local"] = False
        else:
            checks["is_local"] = old in full_file_content
    elif action == "insert":
        # Insert is always "local" in the sense of adding at a symbol
        checks["is_local"] = True

    # Aggregate
    valid = all(
        v is True
        for v in checks.values()
        if v is not None
    )
    reason = None
    if not checks.get("has_effect"):
        reason = "no_meaningful_diff"
    elif not checks.get("targets_correct_file"):
        reason = "targets_wrong_file"
    elif checks.get("is_local") is False:
        reason = "target_not_found"

    return {
        "valid": valid,
        "reason": reason or ("ok" if valid else "verification_failed"),
        "checks": checks,
    }


def verify_patch_plan(
    patch_plan: dict,
    snapshot: dict,
    context: dict,
    project_root: str | None = None,
) -> tuple[bool, dict | None]:
    """
    Verify all changes in a patch plan. Returns (all_valid, first_failure_result).
    snapshot: dict mapping Path -> content (from _snapshot_files).
    """
    changes = patch_plan.get("changes") or []
    binding = context.get("edit_binding") or {}
    root = Path(project_root).resolve() if project_root else Path.cwd()

    for change in changes:
        file_path = change.get("file", "")
        if not file_path:
            continue
        path = (root / file_path).resolve()
        content = snapshot.get(path)
        if content is None and path.exists():
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                content = ""

        result = verify_patch(
            proposal=change,
            full_file_content=content,
            instruction=context.get("instruction") or "",
            binding=binding,
            project_root=project_root,
        )
        if not result["valid"]:
            return False, result

    return True, None
