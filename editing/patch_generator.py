"""Convert diff_planner output to structured AST patches."""

import logging

logger = logging.getLogger(__name__)


def to_structured_patches(plan: dict, instruction: str, context: dict) -> dict:
    """
    Convert diff_planner output to patch_executor format.
    plan: {changes: [{file, symbol, action, patch, reason}, ...]}
    Returns {changes: [{file, patch: {symbol, action, target_node, code}}, ...]}
    """
    raw_changes = plan.get("changes", [])
    changes: list[dict] = []

    for c in raw_changes:
        file_path = c.get("file", "")
        symbol = c.get("symbol", "")
        action = c.get("action", "modify")
        patch_text = c.get("patch", "")

        # Map action: modify/add -> insert, delete -> delete
        ast_action = "delete" if action == "delete" else "insert"
        target_node = "function_body_start" if symbol else "class_body_start"

        # Use patch text as code when it looks like code (contains newlines, def, etc.)
        # Otherwise use instruction snippet
        if _looks_like_code(patch_text):
            code = patch_text
        else:
            code = f"# {instruction[:200]}\npass  # TODO: implement"

        structured_patch = {
            "symbol": symbol or _first_symbol_from_context(file_path, context),
            "action": ast_action,
            "target_node": target_node,
            "code": code,
        }

        changes.append({"file": file_path, "patch": structured_patch})

    return {"changes": changes}


def _looks_like_code(text: str) -> bool:
    """Heuristic: does text look like Python code?"""
    if not text or len(text) < 3:
        return False
    t = text.strip()
    return (
        "def " in t
        or "class " in t
        or "return " in t
        or "import " in t
        or "=" in t
        or t.startswith("#")
        or "\n" in t
    )


def _first_symbol_from_context(file_path: str, context: dict) -> str:
    """Get first symbol for file from ranked_context or retrieved_symbols."""
    for key in ("ranked_context", "retrieved_symbols"):
        for item in context.get(key) or []:
            if isinstance(item, dict) and item.get("file") == file_path:
                sym = item.get("symbol", "")
                if sym:
                    return sym
    return ""
