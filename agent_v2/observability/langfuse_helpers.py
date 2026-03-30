"""
Shared Langfuse generation attachment (Phase 12.6.G).

When a dedicated child span cannot host ``.generation()`` (missing span, SDK error),
fall back to the next parent (e.g. ``exploration`` or root trace) so LLM calls stay visible.

Use :func:`langfuse_generation_input_with_prompt` for every LLM generation so Langfuse
``input`` includes truncated ``prompt``, ``prompt_chars``, and ``prompt_truncated``.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Callable

from agent.prompt_system.prompt_call_context import (
    exploration_suppress_inner_call_reasoning_prompt_log,
    log_exploration_llm_prompt_line,
)

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


# --- Exploration: single traced LLM entry (LANGFUSE_EXPLORATION_TRACING_PLAN) ---

# Canonical ``stage`` values for Langfuse generation ``input`` (filtering / dashboards).
EXPLORATION_LLM_STAGE_VALUES = frozenset(
    {"query_intent", "select", "scope", "analyze", "synthesis"}
)

_exploration_llm_invoke_count: ContextVar[int] = ContextVar(
    "exploration_llm_invoke_count", default=0
)
_exploration_llm_generation_ended_count: ContextVar[int] = ContextVar(
    "exploration_llm_generation_ended_count", default=0
)


def reset_exploration_llm_counters() -> None:
    """Test / harness: zero counters for the **current** logical context.

    Values live in :class:`contextvars.ContextVar` (not module globals): each asyncio
    Task and each thread sees its own counter, so parallel or nested exploration runs
    do not share counts.
    """
    _exploration_llm_invoke_count.set(0)
    _exploration_llm_generation_ended_count.set(0)


def get_exploration_llm_counters() -> tuple[int, int]:
    """Returns (invoke_count, langfuse_generation_end_count) for the current context."""
    return _exploration_llm_invoke_count.get(), _exploration_llm_generation_ended_count.get()


def _bump_exploration_llm_invoke() -> None:
    _exploration_llm_invoke_count.set(_exploration_llm_invoke_count.get() + 1)


def _bump_exploration_llm_generation_ended() -> None:
    _exploration_llm_generation_ended_count.set(
        _exploration_llm_generation_ended_count.get() + 1
    )


def exploration_llm_call(
    *parents: Any,
    name: str,
    prompt: str,
    prompt_registry_key: str,
    invoke: Callable[[], str],
    stage: str,
    model_name: str | None = None,
    input_extra: dict[str, Any] | None = None,
    on_complete: Callable[[str], tuple[dict[str, Any], dict[str, Any]]] | None = None,
    failure_output_extra: dict[str, Any] | None = None,
) -> str:
    """
    Single entry for exploration LLM calls: Langfuse generation + invoke + end with usage.

    ``prompt_registry_key`` is mandatory on every generation (merged into ``input`` extra).
    ``stage`` must be one of :data:`EXPLORATION_LLM_STAGE_VALUES` (stored on generation input).
    ``model_name`` is always stored on generation input (defaults to ``unknown_model``).
    ``on_complete`` runs after a successful ``invoke()``; it must return
    ``(output_dict, metadata_dict)`` for ``langfuse_generation_end_with_usage``.
    If ``on_complete`` raises, the generation is ended with an error output.
    ``failure_output_extra`` is merged into error outputs (e.g. ``synthesis_success: false``).
    """
    key = (prompt_registry_key or "").strip()
    if not key:
        raise ValueError("prompt_registry_key is mandatory for exploration_llm_call")
    st = (stage or "").strip()
    if st not in EXPLORATION_LLM_STAGE_VALUES:
        raise ValueError(
            f"stage must be one of {sorted(EXPLORATION_LLM_STAGE_VALUES)}, got {stage!r}"
        )
    mn = (model_name or "").strip() or "unknown_model"
    merge_extra: dict[str, Any] = {"prompt_registry_key": key}
    if input_extra:
        merge_extra.update(input_extra)
    merge_extra["stage"] = st
    merge_extra["model_name"] = mn
    log_exploration_llm_prompt_line(
        task_name=name,
        prompt_registry_key=key,
        model_name=mn,
    )
    gen = try_langfuse_generation(
        *parents,
        name=name,
        input=langfuse_generation_input_with_prompt(prompt, extra=merge_extra),
    )
    _bump_exploration_llm_invoke()

    def _err_out(exc: Exception) -> dict[str, Any]:
        o: dict[str, Any] = {"error": str(exc)[:2000]}
        if failure_output_extra:
            o.update(failure_output_extra)
        return o

    try:
        with exploration_suppress_inner_call_reasoning_prompt_log():
            raw = invoke()
    except Exception as e:
        if gen is not None:
            try:
                langfuse_generation_end_with_usage(
                    gen,
                    output=_err_out(e),
                    metadata={"ok": False, "stage": st},
                )
            except Exception:
                pass
            else:
                _bump_exploration_llm_generation_ended()
        raise
    try:
        if on_complete is not None:
            out, meta = on_complete(raw)
        else:
            out = {"response": raw[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]}
            meta = {}
    except Exception as e:
        if gen is not None:
            try:
                langfuse_generation_end_with_usage(
                    gen,
                    output=_err_out(e),
                    metadata={"ok": False, "stage": st},
                )
            except Exception:
                pass
            else:
                _bump_exploration_llm_generation_ended()
        raise
    if gen is not None:
        meta_out = dict(meta)
        meta_out.setdefault("stage", st)
        try:
            langfuse_generation_end_with_usage(gen, output=out, metadata=meta_out)
        except Exception:
            pass
        else:
            _bump_exploration_llm_generation_ended()
    return raw
