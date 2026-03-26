"""
Shared Langfuse generation attachment (Phase 12.6.G).

When a dedicated child span cannot host ``.generation()`` (missing span, SDK error),
fall back to the next parent (e.g. ``exploration`` or root trace) so LLM calls stay visible.

Use :func:`langfuse_generation_input_with_prompt` for every LLM generation so Langfuse
``input`` includes truncated ``prompt``, ``prompt_chars``, and ``prompt_truncated``.
"""
from __future__ import annotations

from typing import Any

# Max prompt characters stored on Langfuse generation ``input`` (matches ``planner_v2``).
LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS = 12000


def langfuse_generation_end_with_usage(
    gen: Any,
    *,
    output: Any = None,
    metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> None:
    """
    End a Langfuse generation and attach token usage from the last OpenAI-compatible
    ``_call_chat`` (``get_last_chat_usage``), when present.
    """
    if gen is None:
        return
    try:
        from agent.models.model_client import get_last_chat_usage

        u = get_last_chat_usage() or {}
    except Exception:
        u = {}
    usage: dict[str, Any] = {}
    if u.get("prompt_tokens") is not None:
        usage["input"] = u["prompt_tokens"]
    if u.get("completion_tokens") is not None:
        usage["output"] = u["completion_tokens"]
    if u.get("total_tokens") is not None:
        usage["total"] = u["total_tokens"]
    meta = dict(metadata or {})
    rt = u.get("reasoning_tokens")
    if rt is not None:
        meta["reasoning_tokens"] = rt
        ct = u.get("completion_tokens")
        if ct is not None:
            try:
                meta["output_content_tokens"] = max(0, int(ct) - int(rt))
            except (TypeError, ValueError):
                pass
    extra = dict(kwargs)
    if "metadata" in extra and isinstance(extra["metadata"], dict):
        meta.update(extra["metadata"])
        del extra["metadata"]
    try:
        end_kw: dict[str, Any] = dict(extra)
        if output is not None:
            end_kw["output"] = output
        if usage:
            end_kw["usage"] = usage
        if meta:
            end_kw["metadata"] = meta
        gen.end(**end_kw)
    except Exception:
        try:
            gen.end(output=output, metadata=metadata, **kwargs)
        except Exception:
            pass


def langfuse_generation_input_with_prompt(
    prompt: str,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build Langfuse generation ``input`` with truncated ``prompt``, full ``prompt_chars``,
    and ``prompt_truncated``, merged with optional metadata (counts, step ids, etc.).
    """
    cap = LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS
    n = len(prompt)
    out: dict[str, Any] = dict(extra or {})
    out["prompt"] = prompt[:cap]
    out["prompt_chars"] = n
    out["prompt_truncated"] = n > cap
    return out


def try_langfuse_generation(
    *parents: Any,
    name: str,
    input: dict[str, Any],
) -> Any:
    """
    Create a Langfuse generation on the first parent that supports ``.generation``.

    Tries each non-None parent in order; dedupes by ``id(parent)``.
    Returns ``None`` if all attempts fail (noop handles, missing method, SDK errors).
    """
    seen: set[int] = set()
    for parent in parents:
        if parent is None:
            continue
        pid = id(parent)
        if pid in seen:
            continue
        seen.add(pid)
        if not hasattr(parent, "generation"):
            continue
        try:
            g = parent.generation(name, input=input)
            try:
                from agent.models.model_client import clear_last_chat_usage

                clear_last_chat_usage()
            except Exception:
                pass
            return g
        except Exception:
            continue
    return None


def lf_span_end_output(span: Any, *, output: dict[str, Any] | None = None) -> None:
    """
    End a Langfuse observation span with optional structured ``output``.

    Use for **tool** spans (search, expand, etc.) so traces show inputs/outputs like generations.
    Falls back to ``span.end()`` without output if the SDK rejects ``output=``.
    """
    if span is None:
        return
    try:
        if output is not None:
            span.end(output=output)
        else:
            span.end()
    except Exception:
        try:
            span.end()
        except Exception:
            pass
