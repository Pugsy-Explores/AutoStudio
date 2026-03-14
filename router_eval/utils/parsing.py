"""
Parse router model outputs: category only, category+confidence, primary+secondary+confidence.
"""

import re
from typing import Any

# Categories must match dataset.py; avoid circular import by defining here
CATEGORIES = ("EDIT", "SEARCH", "EXPLAIN", "INFRA", "GENERAL")

_CATEGORIES_SET = set(c.upper() for c in CATEGORIES)


def parse_category(response: str) -> str:
    """
    Extract a single category token from model response.
    Returns first known category word found, else first word, else fallback.
    """
    if not response or not response.strip():
        return "GENERAL"
    text = response.strip().upper()
    words = re.findall(r"[A-Za-z]+", text)
    for w in words:
        if w in _CATEGORIES_SET:
            return w
    if words:
        w = words[0].upper()
        return w if w in _CATEGORIES_SET else "GENERAL"
    return "GENERAL"


def parse_category_confidence(response: str) -> dict[str, Any]:
    """
    Parse lines like "EDIT 0.92" or "SEARCH 0.81" -> {category, confidence}.
    """
    out: dict[str, Any] = {"category": "GENERAL", "confidence": 0.5}
    text = (response or "").strip()
    # Pattern: WORD number
    match = re.search(r"([A-Za-z]+)\s+([0-9]*\.?[0-9]+)", text)
    if match:
        cat = match.group(1).upper()
        if cat in _CATEGORIES_SET:
            out["category"] = cat
        try:
            conf = float(match.group(2))
            out["confidence"] = max(0.0, min(1.0, conf))
        except ValueError:
            pass
    else:
        out["category"] = parse_category(response)
    return out


def parse_dual(response: str) -> dict[str, Any]:
    """
    Parse "PRIMARY SECONDARY CONFIDENCE" e.g. "EDIT SEARCH 0.82"
    -> {primary, secondary, confidence}.
    """
    out: dict[str, Any] = {
        "primary": "GENERAL",
        "secondary": "GENERAL",
        "confidence": 0.5,
    }
    text = (response or "").strip().upper()
    words = re.findall(r"[A-Za-z]+", text)
    nums = re.findall(r"[0-9]*\.?[0-9]+", text)
    cats_found = [w for w in words if w in _CATEGORIES_SET]
    if len(cats_found) >= 2:
        out["primary"] = cats_found[0]
        out["secondary"] = cats_found[1]
    elif len(cats_found) == 1:
        out["primary"] = cats_found[0]
        out["secondary"] = cats_found[0]
    if nums:
        try:
            out["confidence"] = max(0.0, min(1.0, float(nums[0])))
        except ValueError:
            pass
    return out


def parse_critic_response(response: str, predicted_category: str) -> str:
    """
    Parse critic reply: "YES" -> keep predicted_category; "NO <correct_category>" -> correct_category.
    """
    text = (response or "").strip().upper()
    if text.startswith("YES"):
        return predicted_category
    if text.startswith("NO"):
        rest = text[2:].strip()
        words = re.findall(r"[A-Za-z]+", rest)
        for w in words:
            if w in _CATEGORIES_SET:
                return w
    return predicted_category
