"""Context compressor: reduce oversized ranked context via function/symbol summaries."""

import logging

from agent.models.model_client import call_small_model
from config.repo_intelligence_config import MAX_CONTEXT_TOKENS

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4
_COMPRESSION_SYSTEM = """Summarize this code snippet in 1-2 sentences. Focus on: what it does, key parameters, return value. Keep it under 100 words."""


def compress_context(
    ranked_context: list[dict],
    repo_summary: dict | None = None,
    task_goal: str = "",
    max_tokens: int | None = None,
) -> tuple[list[dict], float]:
    """
    When total snippet chars exceed budget, replace oversized snippets with summaries.
    Returns (compressed list, compression_ratio). Ratio = chars_in/chars_out when compressed, else 1.0.
    """
    max_tokens = max_tokens or MAX_CONTEXT_TOKENS
    max_chars = max_tokens * _CHARS_PER_TOKEN

    if not ranked_context:
        return ([], 1.0)

    total_chars = sum(len(c.get("snippet") or "") for c in ranked_context)
    if total_chars <= max_chars:
        return (list(ranked_context), 1.0)

    logger.info(
        "[context_compressor] compressing: %d chars > %d budget",
        total_chars,
        max_chars,
    )
    result: list[dict] = []
    used = 0
    for c in ranked_context:
        snippet = c.get("snippet") or ""
        snip_len = len(snippet)
        if used + snip_len <= max_chars:
            result.append(dict(c))
            used += snip_len
            continue
        if used >= max_chars:
            break
        remaining = max_chars - used
        if snip_len > remaining and snip_len > 200:
            try:
                summary = call_small_model(
                    f"Summarize:\n\n{snippet[:1500]}",
                    max_tokens=min(128, remaining // _CHARS_PER_TOKEN),
                    task_name="query_rewrite",
                    system_prompt=_COMPRESSION_SYSTEM,
                )
                summary = (summary or "").strip() or f"[{c.get('symbol', '')} in {c.get('file', '')}]"
                compressed = dict(c)
                compressed["snippet"] = summary
                compressed["compressed"] = True
                result.append(compressed)
                used += len(summary)
            except Exception as e:
                logger.warning("[context_compressor] summary failed for %s: %s", c.get("file"), e)
                truncated = snippet[:remaining - 20] + "\n..."
                compressed = dict(c)
                compressed["snippet"] = truncated
                compressed["compressed"] = True
                result.append(compressed)
                used += len(truncated)
        else:
            result.append(dict(c))
            used += snip_len
        if used >= max_chars:
            break

    ratio = total_chars / used if used else 1.0
    logger.info("[context_compressor] compressed to %d snippets, %d chars, ratio=%.2f", len(result), used, ratio)
    return (result, ratio)
