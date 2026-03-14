"""Context pruner: limit ranked context by snippets count and char budget, deduplicate."""

import logging

logger = logging.getLogger(__name__)

DEFAULT_MAX_SNIPPETS = 6
DEFAULT_MAX_CHARS = 8000


def prune_context(
    ranked_context: list[dict],
    max_snippets: int = DEFAULT_MAX_SNIPPETS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[dict]:
    """
    Prune ranked context: keep top snippets, respect limits, prefer symbol over file, deduplicate.
    - Stop when max_snippets or max_chars reached
    - Prefer symbol snippets over file snippets when scores tie (symbols first in input order)
    - Deduplicate by (file, symbol)
    """
    if not ranked_context:
        return []
    seen: set[tuple[str, str]] = set()
    result: list[dict] = []
    total_chars = 0
    # Process in ranked order; dedup by (file, symbol)
    for c in ranked_context:
        if len(result) >= max_snippets:
            break
        file_path = c.get("file") or ""
        symbol = c.get("symbol") or ""
        key = (file_path, symbol)
        if key in seen:
            continue
        snippet = c.get("snippet") or ""
        snip_len = len(snippet)
        if total_chars + snip_len > max_chars:
            break
        seen.add(key)
        result.append(dict(c))
        total_chars += snip_len
    logger.info("[context_pruner] selected %d snippets", len(result))
    return result
