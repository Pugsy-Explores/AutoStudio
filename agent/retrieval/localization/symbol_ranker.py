"""Symbol ranker: score localization candidates by dependency distance, call graph, name, semantic similarity."""

import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)

WEIGHT_DEPENDENCY_DISTANCE = 0.4
WEIGHT_CALL_GRAPH = 0.25
WEIGHT_NAME_SIMILARITY = 0.2
WEIGHT_SEMANTIC_SIMILARITY = 0.15


def _tokenize(text: str) -> set[str]:
    """Extract alphanumeric tokens for matching."""
    if not text or not isinstance(text, str):
        return set()
    tokens = re.findall(r"[a-zA-Z0-9_]+", text.strip().lower())
    return {t for t in tokens if len(t) > 1}


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _tfidf_cosine(doc: str, query: str) -> float:
    """Simple TF-IDF cosine similarity (pure Python, no external model)."""
    doc_tokens = _tokenize(doc)
    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0
    if not doc_tokens:
        return 0.0
    doc_counts = Counter(doc_tokens)
    query_counts = Counter(query_tokens)
    # Simple TF: count; IDF: 1 (single doc) — effectively bag-of-words cosine
    dot = sum(doc_counts.get(t, 0) * query_counts.get(t, 0) for t in query_tokens)
    doc_norm = sum(v * v for v in doc_counts.values()) ** 0.5
    query_norm = sum(v * v for v in query_counts.values()) ** 0.5
    if doc_norm <= 0 or query_norm <= 0:
        return 0.0
    return dot / (doc_norm * query_norm)


def rank_localization_candidates(
    candidates: list[dict],
    anchor_symbol: str,
    query: str,
) -> list[dict]:
    """
    Score and rank candidates. Each candidate gets localization_score.
    Returns list sorted by localization_score descending.
    """
    if not candidates:
        return []

    query_tokens = _tokenize(query)
    anchor_tokens = _tokenize(anchor_symbol)

    scored: list[dict] = []
    for c in candidates:
        hop_distance = c.get("hop_distance", 999)
        in_path = c.get("in_execution_path", False)
        in_dep = c.get("in_dependency_nodes", False)

        # 0.4 × dependency_distance
        dep_dist_score = 1.0 / (1.0 + hop_distance)

        # 0.25 × call_graph_relevance
        if in_path:
            call_graph_score = 1.0
        elif in_dep:
            call_graph_score = 0.5
        else:
            call_graph_score = 0.0

        # 0.2 × symbol_name_similarity
        name = c.get("name") or c.get("symbol", "")
        name_tokens = _tokenize(name)
        name_score = _jaccard_similarity(name_tokens, query_tokens)
        if name_score <= 0 and anchor_tokens:
            name_score = _jaccard_similarity(name_tokens, anchor_tokens) * 0.5

        # 0.15 × semantic_similarity
        snippet = c.get("snippet", "")
        doc = snippet if snippet else f"{name} {c.get('file', '')}"
        semantic_score = min(1.0, _tfidf_cosine(doc, query))

        localization_score = (
            WEIGHT_DEPENDENCY_DISTANCE * dep_dist_score
            + WEIGHT_CALL_GRAPH * call_graph_score
            + WEIGHT_NAME_SIMILARITY * name_score
            + WEIGHT_SEMANTIC_SIMILARITY * semantic_score
        )

        scored.append({
            **c,
            "localization_score": localization_score,
        })

    scored.sort(key=lambda x: x.get("localization_score", 0.0), reverse=True)
    return scored
