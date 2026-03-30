"""
Context for associating the next LLM HTTP call with the prompt YAML that was resolved.

:set by: :meth:`PromptRegistry.render_prompt_parts` after loading/caching
:read by: :func:`agent.models.model_client.call_reasoning_model` (and related entrypoints)
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import NamedTuple

logger = logging.getLogger(__name__)


class PromptResolution(NamedTuple):
    """Registry key, file version label (e.g. v1 from v1.yaml), optional absolute path, display model."""

    registry_key: str
    file_version: str
    source_path: str | None
    model_name: str | None


_prompt_resolution: ContextVar[PromptResolution | None] = ContextVar(
    "_prompt_resolution", default=None
)


def bind_prompt_resolution(meta: PromptResolution | None) -> None:
    """Set which prompt file the next model call should attribute to (or clear)."""
    _prompt_resolution.set(meta)


def peek_prompt_resolution() -> PromptResolution | None:
    """Current bound resolution for this context, if any."""
    return _prompt_resolution.get()


def log_bound_prompt_resolution(task_name: str | None) -> None:
    """
    Emit the same stdout + INFO log as a bound reasoning call when ``peek_prompt_resolution()``
    is set (typically right after :meth:`PromptRegistry.render_prompt_parts`).
    No-op if nothing is bound.
    """
    pr = peek_prompt_resolution()
    if pr is None:
        return
    path = pr.source_path or "(unknown)"
    logger.info(
        "llm_call prompt_registry_key=%s prompt_file_version=%s prompt_model_name=%r source_path=%s task_name=%r",
        pr.registry_key,
        pr.file_version,
        pr.model_name,
        path,
        task_name,
    )
    print(
        "    [workflow] prompt: "
        f"registry_key={pr.registry_key} file_version={pr.file_version} "
        f"model_name={pr.model_name!r} source_path={path} task_name={task_name!r}"
    )


def log_exploration_llm_prompt_line(
    *,
    task_name: str,
    prompt_registry_key: str,
    model_name: str,
) -> None:
    """
    Single diagnostic line for :func:`agent_v2.observability.langfuse_helpers.exploration_llm_call`.

    When ``render_prompt_parts`` ran in the caller, logs the resolved YAML path via
    :func:`log_bound_prompt_resolution`. Otherwise logs registry key + model from the exploration
    call (paths marked unbound) so tests and non-registry paths still get a visible line.
    """
    if peek_prompt_resolution() is not None:
        log_bound_prompt_resolution(task_name)
        return
    logger.info(
        "llm_call prompt_registry_key=%s prompt_file_version=(unbound) prompt_model_name=%r "
        "source_path=(unbound) task_name=%r",
        prompt_registry_key,
        model_name,
        task_name,
    )
    print(
        "    [workflow] prompt: "
        f"registry_key={prompt_registry_key} file_version=(unbound) "
        f"model_name={model_name!r} source_path=(unbound) task_name={task_name!r}"
    )


_exploration_suppress_inner_prompt_log: ContextVar[bool] = ContextVar(
    "_exploration_suppress_inner_prompt_log", default=False
)


def peek_exploration_suppress_inner_prompt_log() -> bool:
    """True while :func:`exploration_llm_call` ``invoke()`` runs (suppress duplicate model_client log)."""
    return _exploration_suppress_inner_prompt_log.get()


@contextmanager
def exploration_suppress_inner_call_reasoning_prompt_log():
    """
    While active, :func:`agent.models.model_client.call_reasoning_model` (messages) skips
    :func:`log_bound_prompt_resolution` because :func:`log_exploration_llm_prompt_line` already ran.
    """
    tok = _exploration_suppress_inner_prompt_log.set(True)
    try:
        yield
    finally:
        _exploration_suppress_inner_prompt_log.reset(tok)
