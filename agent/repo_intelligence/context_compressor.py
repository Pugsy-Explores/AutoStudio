"""Context compressor: reduce oversized ranked context via function/symbol summaries."""

import logging

from agent.models.model_client import call_small_model
from config.repo_intelligence_config import MAX_CONTEXT_TOKENS

logger = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 4
_COMPRESSION_SYSTEM = """Summarize this code snippet in 1-2 sentences. Focus on: what it does, key parameters, return value. Keep it under 100 words."""

# Must survive snippet summarization / truncation (EXPLAIN grounding + typed candidates).
_GROUNDING_METADATA_KEYS = (
    "implementation_body_present",
    "retrieval_result_type",
    "candidate_kind",
    "line",
    "line_range",
    "relations",
    "enclosing_class",
    "intent_boost",
    "selection_score",
)


def _merge_grounding_metadata(source: dict, dest: dict) -> None:
    """Re-apply grounding fields after mutating snippet (defensive; dict() copy should already retain them)."""
    if not isinstance(source, dict) or not isinstance(dest, dict):
        return
    for k in _GROUNDING_METADATA_KEYS:
        if k in source:
            dest[k] = source[k]


def _verify_compression_output(before_ctx: list, after_ctx: list) -> None:
    """Index-aligned prefix rows; tail rows may be dropped by budget (logged if they carried impl body)."""
    for i, dst in enumerate(after_ctx):
        if i >= len(before_ctx):
            break
        src = before_ctx[i]
        if not isinstance(src, dict) or not isinstance(dst, dict):
            continue
        if src.get("implementation_body_present") is True and dst.get("implementation_body_present") is not True:
            logger.error(
                "[context_compressor] implementation_body_present=True lost after compression (file=%s)",
                dst.get("file") or src.get("file"),
            )
        for k in ("retrieval_result_type", "candidate_kind"):
            if k in src and src.get(k) != dst.get(k):
                logger.error(
                    "[context_compressor] grounding field %r dropped or changed after compression: "
                    "was=%s now=%s (file=%s)",
                    k,
                    src.get(k),
                    dst.get(k),
                    dst.get("file") or src.get("file"),
                )
    for j in range(len(after_ctx), len(before_ctx)):
        src = before_ctx[j]
        if isinstance(src, dict) and src.get("implementation_body_present") is True:
            logger.error(
                "[context_compressor] implementation_body_present=True row omitted by compression budget (file=%s)",
                src.get("file"),
            )


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
            row = dict(c)
            _merge_grounding_metadata(c, row)
            result.append(row)
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
                _merge_grounding_metadata(c, compressed)
                result.append(compressed)
                used += len(summary)
            except Exception as e:
                logger.warning("[context_compressor] summary failed for %s: %s", c.get("file"), e)
                truncated = snippet[:remaining - 20] + "\n..."
                compressed = dict(c)
                compressed["snippet"] = truncated
                compressed["compressed"] = True
                _merge_grounding_metadata(c, compressed)
                result.append(compressed)
                used += len(truncated)
        else:
            row = dict(c)
            _merge_grounding_metadata(c, row)
            result.append(row)
            used += snip_len
        if used >= max_chars:
            break

    _verify_compression_output(ranked_context, result)
    ratio = total_chars / used if used else 1.0
    logger.info("[context_compressor] compressed to %d snippets, %d chars, ratio=%.2f", len(result), used, ratio)
    return (result, ratio)
