"""
Majority vote over router predictions; optional confidence averaging.
"""

from collections import Counter
from typing import Any


def majority_vote(categories: list[str]) -> str:
    """Return the most frequent category; tie-break by first occurrence."""
    if not categories:
        return "GENERAL"
    counts = Counter(categories)
    best_count = max(counts.values())
    for c in categories:
        if counts[c] == best_count:
            return c
    return categories[0]


def majority_vote_with_confidence(predictions: list[dict[str, Any]]) -> tuple[str, float]:
    """
    predictions: list of {category, confidence}.
    Returns (majority_category, average_confidence).
    """
    if not predictions:
        return "GENERAL", 0.5
    cats = [p.get("category", "GENERAL") for p in predictions]
    confs = [p.get("confidence", 0.5) for p in predictions]
    cat = majority_vote(cats)
    avg_conf = sum(confs) / len(confs) if confs else 0.5
    return cat, avg_conf
