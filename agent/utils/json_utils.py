"""
Minimal safe JSON parsing with mechanical repair only.
No heuristic repair, no guessing. If uncertain → fail.
"""

import json
import logging

logger = logging.getLogger(__name__)


def safe_json_loads(text: str) -> tuple[dict | list | None, str | None, bool]:
    """
    Attempt to parse JSON with minimal safe repair.
    Returns (data, error, repaired) where only one of data/error is not None.
    repaired is True when step 3 (brace repair) succeeded.
    """
    if not text or not isinstance(text, str):
        return None, "empty", False

    cleaned = text.strip()

    # --- Step 1: Extract JSON block if wrapped in markdown ---
    if "```" in cleaned:
        parts = cleaned.split("```")
        for p in parts:
            if "{" in p and "}" in p:
                cleaned = p.strip()
                # Skip optional language tag (e.g. "json") before the JSON
                idx = cleaned.find("{")
                if idx > 0:
                    cleaned = cleaned[idx:]
                break

    # --- Step 2: Try normal parse ---
    try:
        obj = json.loads(cleaned)
        return obj, None, False
    except json.JSONDecodeError:
        pass

    # --- Step 3: Minimal repair (balanced braces only) ---
    try:
        open_braces = cleaned.count("{")
        close_braces = cleaned.count("}")

        if open_braces > close_braces:
            cleaned = cleaned + ("}" * (open_braces - close_braces))

        obj = json.loads(cleaned)
        return obj, None, True
    except json.JSONDecodeError as e:
        return None, str(e), False
