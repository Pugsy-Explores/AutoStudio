"""Query expansion: generate 5-8 search variants from a single query. Deterministic token splitting."""

import re

STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can",
})
ALLOWED_PATTERN = re.compile(r"[A-Za-z0-9_\.]+")
MAX_TOKENS = 4


def _tokenize(text: str) -> list[str]:
    """Extract tokens (alphanumeric, underscore, dot)."""
    if not text or not text.strip():
        return []
    return ALLOWED_PATTERN.findall(text.strip())


def _filter_expansion(exp: str) -> bool:
    """Task 16: Remove expansions with spaces, >4 tokens, punctuation, stopwords."""
    if not exp or len(exp) < 2:
        return False
    tokens = _tokenize(exp)
    if len(tokens) > MAX_TOKENS:
        return False
    # Reconstruct and check for extra chars (spaces, punctuation)
    reconstructed = "".join(tokens)
    if reconstructed != exp.replace(" ", "").replace("_", "").replace(".", ""):
        return False
    lower = exp.lower()
    for sw in STOPWORDS:
        if lower == sw or lower.startswith(sw + "_") or lower.endswith("_" + sw):
            return False
    return True


def generate_query_expansions(query: str) -> list[str]:
    """
    Generate 5-8 search query variants. Deterministic token splitting.
    Example: "expand graph implementation" -> ["expand", "expand_graph", "graph_expand", ...]
    """
    if not query or not query.strip():
        return []

    tokens = _tokenize(query)
    if not tokens:
        return [query.strip()]

    seen: set[str] = set()
    expansions: list[str] = []

    # Original
    orig = query.strip()
    if orig and orig not in seen:
        seen.add(orig)
        expansions.append(orig)

    # Individual tokens (filter short)
    for t in tokens:
        if len(t) >= 2 and t not in seen:
            seen.add(t)
            expansions.append(t)

    # Bigrams: token1_token2, token2_token1
    for i in range(len(tokens)):
        for j in range(len(tokens)):
            if i != j and len(tokens[i]) >= 2 and len(tokens[j]) >= 2:
                v1 = f"{tokens[i]}_{tokens[j]}"
                v2 = f"{tokens[j]}_{tokens[i]}"
                if v1 not in seen:
                    seen.add(v1)
                    expansions.append(v1)
                if v2 not in seen:
                    seen.add(v2)
                    expansions.append(v2)

    # CamelCase: Token -> Token (first letter upper)
    for t in tokens:
        if len(t) >= 2:
            c = t[0].upper() + t[1:].lower()
            if c not in seen:
                seen.add(c)
                expansions.append(c)

    # Class.method style
    if len(tokens) >= 2:
        c0 = tokens[0][0].upper() + tokens[0][1:].lower()
        m1 = tokens[1].lower()
        v = f"{c0}.{m1}"
        if v not in seen:
            seen.add(v)
            expansions.append(v)

    # Apply safe filter (Task 16)
    filtered = [e for e in expansions if _filter_expansion(e)]
    if not filtered:
        filtered = [query.strip()] if query.strip() else []
    return filtered[:8]
