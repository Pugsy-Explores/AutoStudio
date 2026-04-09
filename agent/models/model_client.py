"""
Single entry point for all model calls. Uses llama.cpp HTTP API (OpenAI-compatible).
No other module should call models directly.

Guardrails sit at the LLM call boundary (Rule: Agent → PromptRegistry → Guardrail → Model).
- Pre-call: injection check on user content (raises PromptInjectionError if detected)
- Post-call: optional constraint validation when prompt_name provided (logs on failure)
Set ENABLE_PROMPT_GUARDRAILS=0 to disable (e.g. eval, tests).

Retries: exponential backoff on ConnectionError, TimeoutError, and transient API errors.
Configure via MODEL_RETRY_MAX_ATTEMPTS (default 5) and MODEL_RETRY_BASE_DELAY_SECONDS (default 1.0).

Stage 28: Model call audit — record_model_call() is invoked only from _call_chat (real HTTP).
Stubbed benchmark paths never reach _call_chat, so audit counts are trustworthy.

Per-call token estimates: ``estimate_tokens`` (chars/3.8) on final message contents and on streamed
completion (reasoning + content). Logged at INFO as ``[llm_tokens_est]``; API usage echoed when the
server includes usage in the stream.
"""

import contextvars
import json
import logging
import os
import re
import sys
import threading
import time
from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

# --- Token estimation (prompt after template injection; completion from streamed text) ------------

_CHARS_PER_TOKEN_EST: float = 3.8


def estimate_tokens(text: str) -> int:
    """Rough token count from UTF-8 character length (~3.8 chars/token for Latin-ish text)."""
    if not text:
        return 0
    return int(len(text) / _CHARS_PER_TOKEN_EST)


def _prompt_text_from_messages(messages: list[dict]) -> str:
    """Flatten chat messages to a single string matching payload content (post-normalization)."""
    parts: list[str] = []
    for m in messages or []:
        parts.append(str(m.get("content") or ""))
    return "".join(parts)


def _log_llm_token_estimates(
    *,
    task_name: str | None,
    model_key: str | None,
    messages: list[dict],
    completion_text: str,
    last_usage: dict[str, Any] | None,
) -> None:
    """Info log + workflow line: estimated prompt/completion tokens; API usage when server sends it."""
    prompt_text = _prompt_text_from_messages(messages)
    prompt_est = estimate_tokens(prompt_text)
    completion_est = estimate_tokens(completion_text)
    api_pt = last_usage.get("prompt_tokens") if last_usage else None
    api_ct = last_usage.get("completion_tokens") if last_usage else None
    logger.info(
        "[llm_tokens_est] task=%s model_key=%s prompt_tokens_est=%s completion_tokens_est=%s "
        "prompt_chars=%s completion_chars=%s api_prompt_tokens=%s api_completion_tokens=%s",
        task_name or "unknown",
        model_key or "unknown",
        prompt_est,
        completion_est,
        len(prompt_text),
        len(completion_text),
        api_pt,
        api_ct,
    )
    api_suffix = ""
    if api_pt is not None or api_ct is not None:
        api_suffix = f" api_prompt={api_pt} api_completion={api_ct}"
    print(
        "    [workflow] token estimate: "
        f"prompt≈{prompt_est} completion≈{completion_est}{api_suffix}"
    )


# Last chat completion usage from `_call_chat` (for Langfuse / observability). Context-local.
_last_chat_usage: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "_last_chat_usage", default=None
)


def get_last_chat_usage() -> dict[str, Any] | None:
    """Token usage from the most recent ``_call_chat`` in this context, or ``None``."""
    return _last_chat_usage.get()


def _set_last_chat_usage(u: dict[str, Any] | None) -> None:
    _last_chat_usage.set(u)


def clear_last_chat_usage() -> None:
    """Clear usage context before a new Langfuse generation (avoids stale token counts)."""
    _set_last_chat_usage(None)


def _usage_obj_to_dict(u: Any) -> dict[str, Any]:
    """Normalize OpenAI-compatible ``usage`` to plain dict for Langfuse."""
    if u is None:
        return {}
    if isinstance(u, dict):
        raw = dict(u)
    elif hasattr(u, "model_dump"):
        raw = u.model_dump()
    else:
        raw = {
            "prompt_tokens": getattr(u, "prompt_tokens", None),
            "completion_tokens": getattr(u, "completion_tokens", None),
            "total_tokens": getattr(u, "total_tokens", None),
        }
        ctd = getattr(u, "completion_tokens_details", None)
        if ctd is not None:
            if isinstance(ctd, dict):
                raw["completion_tokens_details"] = ctd
            elif hasattr(ctd, "model_dump"):
                raw["completion_tokens_details"] = ctd.model_dump()
            else:
                rt = getattr(ctd, "reasoning_tokens", None)
                if rt is not None:
                    raw.setdefault("completion_tokens_details", {})["reasoning_tokens"] = rt
    out: dict[str, Any] = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if raw.get(k) is not None:
            out[k] = raw[k]
    ctd = raw.get("completion_tokens_details")
    if isinstance(ctd, dict) and ctd.get("reasoning_tokens") is not None:
        out["reasoning_tokens"] = ctd["reasoning_tokens"]
    return out


class GuardrailError(RuntimeError):
    """Raised when guardrail validation fails after retries. Catch for structured handling."""


# ---------------------------------------------------------------------------
# Stage 28: Model call audit (thread-safe, process-local)
# ---------------------------------------------------------------------------

_MODEL_CALL_AUDIT_LOCK = threading.Lock()
_MODEL_CALL_AUDIT: dict = {
    "model_call_count": 0,
    "small_model_call_count": 0,
    "reasoning_model_call_count": 0,
    "model_call_sites": [],  # bounded list, max 50
    "model_provider": None,
    "model_base_url": None,
    "model_name_small": None,
    "model_name_reasoning": None,
}
_MAX_CALL_SITES = 50


def _record_model_call(kind: str, callsite: str, model_name: str, base_url: str) -> None:
    """Record a real model call. Called only from _call_chat (actual HTTP)."""
    with _MODEL_CALL_AUDIT_LOCK:
        _MODEL_CALL_AUDIT["model_call_count"] += 1
        if kind == "small":
            _MODEL_CALL_AUDIT["small_model_call_count"] += 1
        else:
            _MODEL_CALL_AUDIT["reasoning_model_call_count"] += 1
        if _MODEL_CALL_AUDIT["model_provider"] is None:
            _MODEL_CALL_AUDIT["model_provider"] = "openai_compatible"
        if _MODEL_CALL_AUDIT["model_base_url"] is None:
            _MODEL_CALL_AUDIT["model_base_url"] = base_url
        if kind == "small" and _MODEL_CALL_AUDIT["model_name_small"] is None:
            _MODEL_CALL_AUDIT["model_name_small"] = model_name
        if kind == "reasoning" and _MODEL_CALL_AUDIT["model_name_reasoning"] is None:
            _MODEL_CALL_AUDIT["model_name_reasoning"] = model_name
        sites = _MODEL_CALL_AUDIT["model_call_sites"]
        if len(sites) < _MAX_CALL_SITES:
            sites.append({"kind": kind, "callsite": callsite, "model_name": model_name})


def reset_model_call_audit() -> None:
    """Reset audit state. Call before each benchmark task in live_model mode."""
    with _MODEL_CALL_AUDIT_LOCK:
        _MODEL_CALL_AUDIT["model_call_count"] = 0
        _MODEL_CALL_AUDIT["small_model_call_count"] = 0
        _MODEL_CALL_AUDIT["reasoning_model_call_count"] = 0
        _MODEL_CALL_AUDIT["model_call_sites"] = []
        _MODEL_CALL_AUDIT["model_provider"] = None
        _MODEL_CALL_AUDIT["model_base_url"] = None
        _MODEL_CALL_AUDIT["model_name_small"] = None
        _MODEL_CALL_AUDIT["model_name_reasoning"] = None


def get_model_call_audit() -> dict:
    """Return a copy of the current audit state."""
    with _MODEL_CALL_AUDIT_LOCK:
        return {
            "model_call_count": _MODEL_CALL_AUDIT["model_call_count"],
            "small_model_call_count": _MODEL_CALL_AUDIT["small_model_call_count"],
            "reasoning_model_call_count": _MODEL_CALL_AUDIT["reasoning_model_call_count"],
            "model_call_sites": list(_MODEL_CALL_AUDIT["model_call_sites"]),
            "model_provider": _MODEL_CALL_AUDIT["model_provider"],
            "model_base_url": _MODEL_CALL_AUDIT["model_base_url"],
            "model_name_small": _MODEL_CALL_AUDIT["model_name_small"],
            "model_name_reasoning": _MODEL_CALL_AUDIT["model_name_reasoning"],
        }

T = TypeVar("T")

# Retry config: exponential backoff
_MODEL_RETRY_MAX = int(os.getenv("MODEL_RETRY_MAX_ATTEMPTS", "5"))
_MODEL_RETRY_BASE_DELAY = float(os.getenv("MODEL_RETRY_BASE_DELAY_SECONDS", "1.0"))


def _is_retriable_error(exc: BaseException) -> bool:
    """True if the error is transient and worth retrying."""
    err_msg = str(exc).lower()
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    if "connection" in err_msg or "timeout" in err_msg or "refused" in err_msg:
        return True
    if "500" in err_msg or "502" in err_msg or "503" in err_msg or "504" in err_msg:
        return True
    if "server_error" in err_msg or "rate limit" in err_msg:
        return True
    return False


def _retry_with_exponential_backoff(
    fn: Callable[[], T],
    max_attempts: int = _MODEL_RETRY_MAX,
    base_delay: float = _MODEL_RETRY_BASE_DELAY,
) -> T:
    """Execute fn(); on retriable error, wait with exponential backoff and retry."""
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except BaseException as e:
            last_exc = e
            if not _is_retriable_error(e) or attempt >= max_attempts - 1:
                raise
            delay = base_delay * (2**attempt)
            logger.warning(
                "[model_client] attempt %d/%d failed (%s); retrying in %.2f seconds",
                attempt + 1,
                max_attempts,
                type(e).__name__,
                delay,
            )
            print(f"Retrying request in {delay:.2f} seconds (attempt {attempt + 2}/{max_attempts})")
            time.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError("retry loop exited unexpectedly")


def _try_emit_llm_trace(
    *,
    task_name: str,
    prompt: str,
    system_prompt: str | None,
    output_text: str,
    latency_ms: int,
    model_name: str,
) -> None:
    """Phase 13 — append LLM step to active TraceEmitter when ModeManager pins context."""
    try:
        from agent_v2.runtime.trace_context import get_active_trace_emitter

        em = get_active_trace_emitter()
        if em is None:
            return
        em.record_llm(
            task_name=task_name,
            prompt=prompt,
            output_text=output_text,
            latency_ms=latency_ms,
            system_prompt=system_prompt,
            model=model_name,
        )
    except Exception:
        logger.debug("LLM trace emit skipped", exc_info=True)


_ENABLE_GUARDRAILS = os.getenv("ENABLE_PROMPT_GUARDRAILS", "1").lower() in ("1", "true", "yes")
_MODEL_CHAT_ROLE_SUPPORT = os.getenv("MODEL_CHAT_ROLE_SUPPORT", "1").lower() in (
    "1",
    "true",
    "yes",
)

# Use config from same package
from agent.models.model_config import (
    get_endpoint_for_model,
    get_model_call_params,
    get_model_name,
    MODEL_API_KEY,
    MODEL_MAX_TOKENS,
    MODEL_REQUEST_TIMEOUT,
    MODEL_TEMPERATURE,
    TASK_MODELS,
)

_DEFAULT_MAX_TOKENS = MODEL_MAX_TOKENS
_TIMEOUT = MODEL_REQUEST_TIMEOUT

_MAX_PRETTY_LINES = 40
_PRETTY_WIDTH = 72
_MAX_CONTEXT_CHARS = 400
# Full user-role text for workflow logs (section parser + small context keys omit most of it).
_MAX_USER_PROMPT_LOG_CHARS = 20000


def _dump_replanner_prompt_files(system_prompt: str | None, user_prompt: str) -> None:
    """Write replanner system/user prompts for observability (debug_replanner)."""
    out_dir = "artifacts/replanner_debug"
    os.makedirs(out_dir, exist_ok=True)
    ts = int(time.time() * 1000)
    sys_txt = system_prompt if system_prompt is not None else ""
    body = (
        "=== SYSTEM PROMPT ===\n"
        f"{sys_txt}\n\n"
        "=== USER PROMPT ===\n"
        f"{user_prompt or ''}"
    )
    last_path = os.path.join(out_dir, "last_prompt.txt")
    ts_path = os.path.join(out_dir, f"prompt_{ts}.txt")
    with open(last_path, "w", encoding="utf-8") as f:
        f.write(body)
    with open(ts_path, "w", encoding="utf-8") as f:
        f.write(body)
    logger.info("[DEBUG] replanner prompts written to %s and %s", last_path, ts_path)


# Task names that use replanner-style prompts (debug_replanner dump).
_REPLANNER_LLM_DEBUG_TASK_NAMES = frozenset({
    "PLANNER_REPLAN_PLAN",
    "PLANNER_REPLAN_ACT",
    "PLANNER_REPLAN_ORCHESTRATOR",
})


def _truncate_for_log(text: str, max_chars: int = _MAX_CONTEXT_CHARS) -> str:
    clean = " ".join((text or "").split())
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars] + "..."


def _truncate_preserve_newlines(text: str, max_chars: int) -> str:
    """Truncate without squashing newlines (for prompt-shaped log fields)."""
    t = text or ""
    if len(t) <= max_chars:
        return t
    return t[: max(0, max_chars - 3)] + "..."


# Log field values longer than this as a multi-line block under the key.
_PRETTY_PRINT_BLOCK_MIN_CHARS = 120

# Workflow stdout: only injected template variables / variable-shaped payloads (not static prompt text).
_TEMPLATE_VARIABLE_LOG_KEYS = frozenset(
    {
        "instruction",
        "context_block",
        "expected_behavior",
        "trace",
        "previous_queries",
        "failure_reason",
        "query_intent",
        "context_feedback",
        "step_summaries",
        "final_outcome",
        "candidates",
        "selected_indices",
        "selected",
    }
)

# Keys that must NOT be sourced from `_extract_json_objects` heuristics: exploration
# selector batch and similar prompts embed JSON-shaped examples; candidate code in
# `candidates_json` often contains substrings like `{"selected_indices": [0]}` that
# are not model inputs as template variables. Logging them under [workflow] model request
# is misleading (use exploration.selector_handoff + model response instead).
_JSON_BLOB_LOG_SKIP_KEYS = frozenset({"selected_indices", "selected"})


def _extract_planner_injected_variables(combined: str) -> dict[str, str]:
    """instruction + context_block from rendered planner prompts ({instruction}, {context_block} slots)."""
    out: dict[str, str] = {}
    if not (combined or "").strip():
        return out
    # 1) Instruction: single-system prompts use "USER INSTRUCTION (latest):"; split system/user
    #    prompts (e.g. planner.decision.act/models/qwen2.5-coder-7b) use "USER INSTRUCTION:" in user.
    m = re.search(
        r"USER INSTRUCTION \(latest\):\s*(.*?)(?=\n\s*-{5,}|\n\s*OUTPUT FORMAT|\Z)",
        combined,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        ins = m.group(1).strip()
        if ins:
            out["instruction"] = _truncate_preserve_newlines(ins, _MAX_USER_PROMPT_LOG_CHARS)
    if "instruction" not in out:
        m_alt = re.search(
            r"(?:^|\n)\s*USER INSTRUCTION:\s*(.*?)(?=\n\s*-{5,}|\n\s*OUTPUT FORMAT|\Z)",
            combined,
            re.DOTALL | re.IGNORECASE,
        )
        if m_alt:
            ins = m_alt.group(1).strip()
            if ins:
                out["instruction"] = _truncate_preserve_newlines(ins, _MAX_USER_PROMPT_LOG_CHARS)

    # 2) context_block: legacy — injected block starting with USER TASK INTENT before USER INSTRUCTION (latest)
    m2 = re.search(
        r"(USER TASK INTENT:[\s\S]*?)(?=\n\s*-{5,}\s*\n\s*USER INSTRUCTION \(latest\):)",
        combined,
        re.IGNORECASE,
    )
    if m2:
        cb = m2.group(1).strip()
        if cb:
            out["context_block"] = _truncate_preserve_newlines(cb, _MAX_USER_PROMPT_LOG_CHARS)

    # 3) Split user prompt: CONTEXT section (Qwen / model-specific YAML with user_prompt)
    if "context_block" not in out:
        m_ctx = re.search(
            r"(?:^|\n)\s*CONTEXT\s*\n\s*-{5,}\s*\n([\s\S]*?)(?=\n\s*-{5,}\s*\n\s*TASK\b|\Z)",
            combined,
            re.IGNORECASE,
        )
        if m_ctx:
            cb = m_ctx.group(1).strip()
            if cb:
                out["context_block"] = _truncate_preserve_newlines(cb, _MAX_USER_PROMPT_LOG_CHARS)

    # 4) Flat system prompt: {context_block} sits in the last dashed segment before USER INSTRUCTION (latest)
    if "context_block" not in out:
        needle = "USER INSTRUCTION (latest):"
        idx = combined.lower().find(needle.lower())
        if idx >= 0:
            head = combined[:idx]
            parts = [p.strip() for p in re.split(r"\n\s*-{5,}\s*\n", head) if p.strip()]
            if len(parts) >= 2:
                cb = parts[-1]
                if cb:
                    out["context_block"] = _truncate_preserve_newlines(
                        cb, _MAX_USER_PROMPT_LOG_CHARS
                    )

    return out


def _extract_section_value(text: str, section_name: str) -> str:
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        normalized = line.strip().lower()
        if normalized.startswith(section_name.lower() + ":"):
            tail = line.split(":", 1)[1].strip()
            if tail:
                return _truncate_for_log(tail)
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            out: list[str] = []
            while j < len(lines):
                cur = lines[j].strip()
                if not cur:
                    break
                if cur.endswith(":") and len(cur) < 60:
                    break
                out.append(cur)
                j += 1
            return _truncate_for_log(" ".join(out))
    return ""


def _extract_json_objects(text: str) -> list[dict[str, Any]]:
    objs: list[dict[str, Any]] = []
    if not text:
        return objs
    for m in re.finditer(r"\{[\s\S]*?\}", text):
        frag = m.group(0)
        try:
            parsed = json.loads(frag)
        except Exception:
            continue
        if isinstance(parsed, dict):
            objs.append(parsed)
    return objs


def _extract_prompt_context(messages: list[dict]) -> dict[str, str]:
    """Extract only template-variable / injected payloads for workflow logs (not full prompts)."""
    user_content = "\n\n".join(
        str(m.get("content") or "")
        for m in (messages or [])
        if str(m.get("role") or "").lower() == "user"
    )
    system_content = "\n\n".join(
        str(m.get("content") or "")
        for m in (messages or [])
        if str(m.get("role") or "").lower() == "system"
    )
    combined = (system_content + "\n\n" + user_content).strip()
    src = user_content or system_content or ""

    out: dict[str, str] = {}
    out.update(_extract_planner_injected_variables(combined))

    # Section-style prompts (non-planner): only allowlisted keys.
    if "instruction" not in out:
        instruction = _extract_section_value(src, "Instruction") or _extract_section_value(
            system_content, "Instruction"
        )
        if instruction:
            out["instruction"] = instruction
    for section_key, label in (
        ("expected_behavior", "Expected Behavior"),
        ("trace", "Trace"),
        ("previous_queries", "Previous queries (optional)"),
        ("failure_reason", "Failure reason (optional)"),
    ):
        if section_key in out:
            continue
        val = _extract_section_value(src, label) or _extract_section_value(system_content, label)
        if val:
            out[section_key] = val

    for blob in _extract_json_objects(src) + _extract_json_objects(system_content):
        for key in _TEMPLATE_VARIABLE_LOG_KEYS:
            if key in _JSON_BLOB_LOG_SKIP_KEYS:
                continue
            if key in blob and key not in out:
                try:
                    out[key] = _truncate_for_log(
                        json.dumps(blob[key], ensure_ascii=False),
                        max_chars=_MAX_USER_PROMPT_LOG_CHARS,
                    )
                except Exception:
                    out[key] = _truncate_for_log(str(blob[key]), max_chars=_MAX_USER_PROMPT_LOG_CHARS)

    filtered = {k: v for k, v in out.items() if k in _TEMPLATE_VARIABLE_LOG_KEYS and v}
    return filtered


def _pretty_print_request(task_name: str | None, context_fields: dict[str, str], *, model_key: str | None = None) -> None:
    """
    Workflow line: ``[workflow] model request`` + step/model_key, then **only** injected
    template variables (same slots as prompt ``{{...}}`` / ``{instruction}`` / ``{context_block}``),
    pretty-printed — not full system/user prompts.
    """
    indent = "    "
    cont = indent + "    "
    print(f"{indent}[workflow] model request:")
    print(indent + "─" * _PRETTY_WIDTH)
    print(f"{indent}step: {task_name or 'unknown'}")
    if model_key:
        print(f"{indent}model_key: {model_key}")
    print(indent + "─" * _PRETTY_WIDTH)
    if not context_fields:
        print(f"{indent}(no template variables extracted for log)")
        print(indent + "─" * _PRETTY_WIDTH)
        return
    preferred = ("instruction", "context_block")
    keys = [k for k in preferred if k in context_fields]
    keys.extend(sorted(k for k in context_fields if k not in keys))
    for k in keys:
        v = context_fields[k]
        if not v:
            continue
        use_block = "\n" in v or len(v) > _PRETTY_PRINT_BLOCK_MIN_CHARS
        if use_block:
            print(f"{indent}{k}:")
            for line in v.splitlines():
                print(f"{cont}{line}")
        else:
            print(f"{indent}{k}: {v}")
    print(indent + "─" * _PRETTY_WIDTH)


def _raw_response_repr(resp_obj) -> str:
    """Serialize raw response for logging (to distinguish parsing vs model issues)."""
    try:
        if isinstance(resp_obj, dict):
            return json.dumps(resp_obj, indent=2, default=str)
        if hasattr(resp_obj, "model_dump"):
            return json.dumps(resp_obj.model_dump(), indent=2, default=str)
        if hasattr(resp_obj, "dict"):
            return json.dumps(resp_obj.dict(), indent=2, default=str)
        return repr(resp_obj)
    except Exception as e:
        return f"<could not serialize: {e}>"


def _debug_empty_response(resp_obj) -> None:
    """Log response structure when content is empty to debug server/API issues."""
    if resp_obj is None:
        print("    [workflow] model response (debug): stream completed with no content")
        logger.warning("[model_client] stream completed with no content")
        return
    raw_repr = _raw_response_repr(resp_obj)
    _raw_max_log = 4000
    raw_for_log = raw_repr if len(raw_repr) <= _raw_max_log else raw_repr[:_raw_max_log] + "\n... (truncated)"
    logger.warning("[model_client] empty content — raw response:\n%s", raw_for_log)
    raw_preview = raw_repr[:500] + ("..." if len(raw_repr) > 500 else "")
    print("    [workflow] model response (raw preview):", raw_preview)
    try:
        if hasattr(resp_obj, "choices"):
            choices = resp_obj.choices or []
            resp_id = getattr(resp_obj, "id", None)
            model = getattr(resp_obj, "model", None)
            logger.warning(
                "[model_client] empty content: id=%s model=%s choices_count=%s",
                resp_id,
                model,
                len(choices),
            )
            print("    [workflow] model response (debug): id =", resp_id, "model =", model, "choices_count =", len(choices))
            if choices:
                c0 = choices[0]
                finish = getattr(c0, "finish_reason", None)
                msg = getattr(c0, "message", None)
                content_val = getattr(msg, "content", None) if msg is not None else None
                content_preview = (content_val or "")[:200] if content_val is not None else "(null)"
                print(f"    [workflow] model response (debug): finish_reason = {finish!r}")
                print(f"    [workflow] model response (debug): message.content = {content_preview!r}")
                logger.warning(
                    "[model_client] first choice: finish_reason=%s content_type=%s",
                    finish,
                    type(content_val).__name__ if content_val is not None else "NoneType",
                )
        else:
            # urllib path: resp is a dict
            choices = resp_obj.get("choices", [])
            resp_id = resp_obj.get("id")
            model = resp_obj.get("model")
            logger.warning(
                "[model_client] empty content (dict): id=%s model=%s choices_count=%s",
                resp_id,
                model,
                len(choices),
            )
            print("    [workflow] model response (debug): id =", resp_id, "model =", model, "choices_count =", len(choices))
            if choices:
                c0 = choices[0]
                finish = c0.get("finish_reason")
                msg = c0.get("message") or {}
                content_val = msg.get("content")
                content_preview = (content_val or "")[:200] if content_val is not None else "(null)"
                print(f"    [workflow] model response (debug): finish_reason = {finish!r}")
                print(f"    [workflow] model response (debug): message.content = {content_preview!r}")
    except Exception as e:
        logger.warning("[model_client] debug_empty_response failed: %s", e)
        print("    [workflow] model response (debug): failed to inspect:", e)


def _extract_content_from_response(resp_obj, is_dict: bool) -> str:
    """
    Extract content from response message.content.
    When finish_reason=length, appends truncation hint.
    """
    if is_dict:
        choices = resp_obj.get("choices", [])
        if not choices:
            return ""
        c0 = choices[0]
        msg = c0.get("message") or {}
        content = (msg.get("content") or "").strip()
        finish_reason = c0.get("finish_reason")
    else:
        choices = resp_obj.choices or []
        if not choices:
            return ""
        c0 = choices[0]
        msg = getattr(c0, "message", None)
        content = ((getattr(msg, "content", None) or "") or "").strip()
        finish_reason = getattr(c0, "finish_reason", None)

    if finish_reason == "length":
        if content:
            content = content + "\n[Response truncated - consider increasing max_tokens]"
        logger.warning("[model_client] finish_reason=length; %s", "response truncated" if content else "no content (consider increasing max_tokens)")

    return content


def _pretty_print_response(content: str, raw_response=None) -> None:
    """Pretty-print model response for workflow visibility."""
    print("    [workflow] model response:")
    print("    " + "─" * _PRETTY_WIDTH)
    out = (content or "").strip()
    if not out:
        print("    (empty)")
        if raw_response is not None:
            _debug_empty_response(raw_response)
    else:
        lines = out.splitlines()
        if len(lines) > _MAX_PRETTY_LINES:
            lines = lines[:_MAX_PRETTY_LINES] + [f"... ({len(lines) - _MAX_PRETTY_LINES} more lines)"]
        for line in lines:
            print("    " + line)
    print("    " + "─" * _PRETTY_WIDTH)


def _stream_chunk_to_terminal(text: str) -> None:
    """Write a chunk to stdout immediately for real-time streaming."""
    if text:
        sys.stdout.write(text)
        sys.stdout.flush()


def _flatten_messages_with_role_tags(messages: list[dict]) -> str:
    """
    Deterministic fallback serialization for backends that do not support role messages.
    Uses strict tags to reduce role confusion on small models.
    """
    system_parts: list[str] = []
    user_parts: list[str] = []
    other_parts: list[str] = []
    for m in messages or []:
        role = str(m.get("role") or "").strip().lower()
        content = str(m.get("content") or "")
        if role == "system":
            system_parts.append(content)
        elif role == "user":
            user_parts.append(content)
        else:
            other_parts.append(content)
    system_text = "\n\n".join(x for x in system_parts if x.strip()).strip()
    user_text = "\n\n".join(x for x in user_parts if x.strip()).strip()
    if other_parts:
        user_text = (user_text + "\n\n" if user_text else "") + "\n\n".join(
            x for x in other_parts if x.strip()
        ).strip()
    return f"[SYSTEM]\n{system_text}\n\n---\n\n[USER]\n{user_text}".strip()


def _normalize_messages_for_backend(messages: list[dict]) -> list[dict]:
    if _MODEL_CHAT_ROLE_SUPPORT:
        return messages
    return [{"role": "user", "content": _flatten_messages_with_role_tags(messages)}]


def _call_chat(
    endpoint: str,
    messages: list[dict],
    *,
    task_name: str | None = None,
    model_key: str | None = None,
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    request_timeout: Optional[int] = None,
    frequency_penalty: Optional[float] = None,
    presence_penalty: Optional[float] = None,
) -> str:
    """POST to OpenAI-compatible chat completions endpoint; return assistant content."""
    temp = temperature if temperature is not None else MODEL_TEMPERATURE
    raw_timeout = request_timeout if request_timeout is not None else _TIMEOUT
    try:
        timeout = max(1, int(raw_timeout)) if raw_timeout is not None else 120
    except (TypeError, ValueError):
        timeout = 120
    payload: dict = {
        "model": "default",
        "messages": messages,
        "temperature": temp,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if frequency_penalty is not None:
        payload["frequency_penalty"] = frequency_penalty
    if presence_penalty is not None:
        payload["presence_penalty"] = presence_penalty
    context_fields = _extract_prompt_context(messages)
    logger.info(
        "model call: endpoint=%s task=%s model_key=%s max_tokens=%s temperature=%s timeout=%s",
        endpoint,
        task_name,
        model_key,
        max_tokens,
        payload["temperature"],
        timeout,
    )
    _pretty_print_request(task_name, context_fields, model_key=model_key)

    def _do_call() -> str:
        _set_last_chat_usage(None)
        try:
            from openai import OpenAI
        except ImportError:
            import urllib.request

            payload_stream = {**payload, "stream": True}
            req = urllib.request.Request(
                endpoint,
                data=json.dumps(payload_stream).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {MODEL_API_KEY}",
                },
                method="POST",
            )
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            last_usage: dict[str, Any] | None = None
            print("    [workflow] model response (streaming):")
            print("    " + "─" * _PRETTY_WIDTH)
            done = False
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                buf = ""
                for line in resp:
                    if done:
                        break
                    buf += line.decode("utf-8", errors="replace")
                    while "\n" in buf or "\r\n" in buf:
                        sep = "\r\n" if "\r\n" in buf else "\n"
                        raw_line, buf = buf.split(sep, 1)
                        if raw_line.startswith("data: "):
                            data = raw_line[6:].strip()
                            if data == "[DONE]":
                                done = True
                                break
                            try:
                                chunk = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            u = chunk.get("usage")
                            if u:
                                last_usage = _usage_obj_to_dict(u)
                            choices = chunk.get("choices", [])
                            if not choices:
                                continue
                            d = choices[0].get("delta") or {}
                            reasoning = d.get("reasoning_content")
                            delta = d.get("content")
                            if reasoning:
                                reasoning_parts.append(reasoning)
                                _stream_chunk_to_terminal(reasoning)
                            if delta:
                                content_parts.append(delta)
                                _stream_chunk_to_terminal(delta)
            print()
            print("    " + "─" * _PRETTY_WIDTH)
            full_out = "".join(reasoning_parts) + "".join(content_parts)
            _log_llm_token_estimates(
                task_name=task_name,
                model_key=model_key,
                messages=messages,
                completion_text=full_out,
                last_usage=last_usage,
            )
            _set_last_chat_usage(last_usage)
            content = "".join(content_parts).strip()
            if not content and not reasoning_parts:
                _debug_empty_response(None)
            return content

        base_url = endpoint.rsplit("/chat/completions", 1)[0].rstrip("/")
        if not base_url.endswith("/v1"):
            base_url = endpoint.rsplit("/", 1)[0]
        # Use configured timeout; disable OpenAI client retries (we handle retries ourselves)
        client = OpenAI(
            base_url=base_url,
            api_key=MODEL_API_KEY,
            timeout=float(max(1, timeout)),
            max_retries=0,
        )
        create_kwargs: dict = {
            "model": "default",
            "messages": messages,
            "temperature": temp,
            "stream": True,
        }
        if max_tokens is not None:
            create_kwargs["max_tokens"] = max_tokens
        if frequency_penalty is not None:
            create_kwargs["frequency_penalty"] = frequency_penalty
        if presence_penalty is not None:
            create_kwargs["presence_penalty"] = presence_penalty
        try:
            stream = client.chat.completions.create(
                **create_kwargs, stream_options={"include_usage": True}
            )
        except TypeError:
            stream = client.chat.completions.create(**create_kwargs)
        content_parts = []
        reasoning_parts: list[str] = []
        finish_reason = None
        last_usage: dict[str, Any] | None = None
        print("    [workflow] model response (streaming):")
        print("    " + "─" * _PRETTY_WIDTH)
        for chunk in stream:
            u = getattr(chunk, "usage", None)
            if u is not None:
                last_usage = _usage_obj_to_dict(u)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            reasoning = (
                getattr(delta, "reasoning_content", None)
                if not isinstance(delta, dict)
                else delta.get("reasoning_content")
            )
            if reasoning:
                reasoning_parts.append(reasoning)
                _stream_chunk_to_terminal(reasoning)
            text = (
                getattr(delta, "content", None)
                if not isinstance(delta, dict)
                else delta.get("content")
            )
            if text:
                content_parts.append(text)
                _stream_chunk_to_terminal(text)
            finish_reason = getattr(chunk.choices[0], "finish_reason", None)
        print()
        print("    " + "─" * _PRETTY_WIDTH)
        full_out = "".join(reasoning_parts) + "".join(content_parts)
        _log_llm_token_estimates(
            task_name=task_name,
            model_key=model_key,
            messages=messages,
            completion_text=full_out,
            last_usage=last_usage,
        )
        content = "".join(content_parts).strip()
        if finish_reason == "length" and content:
            content = content + "\n[Response truncated - consider increasing max_tokens]"
            logger.warning("[model_client] finish_reason=length; response truncated")
        if not content and not reasoning_parts:
            _debug_empty_response(None)
        _set_last_chat_usage(last_usage)
        return content

    return _retry_with_exponential_backoff(_do_call)


def _log_bound_prompt_for_llm_call(
    task_name: Optional[str], *, _exploration_suppress_duplicate: bool = False
) -> None:
    """
    If :meth:`PromptRegistry.render_prompt_parts` ran in this context, log the resolved
    prompt file (version + absolute path) for the upcoming HTTP call.

    When ``_exploration_suppress_duplicate`` is True and exploration wrapped this call in
    :func:`agent.prompt_system.prompt_call_context.exploration_suppress_inner_call_reasoning_prompt_log`,
    skip (exploration already logged via :func:`agent.prompt_system.prompt_call_context.log_exploration_llm_prompt_line`).

    Mirrors other workflow diagnostics: ``print`` lines use the ``[workflow]`` prefix so they
    show under ``pytest -s`` without ``--log-cli-level``; ``logger.info`` remains for log aggregation.
    """
    if _exploration_suppress_duplicate:
        try:
            from agent.prompt_system.prompt_call_context import peek_exploration_suppress_inner_prompt_log

            if peek_exploration_suppress_inner_prompt_log():
                return
        except Exception:
            pass
    try:
        from agent.prompt_system.prompt_call_context import log_bound_prompt_resolution

        log_bound_prompt_resolution(task_name)
    except Exception:
        return


def _run_guardrails_pre(user_content: str) -> None:
    """Run injection check on user content before LLM call. Raises PromptInjectionError if detected."""
    if not _ENABLE_GUARDRAILS or not user_content:
        return
    try:
        from agent.prompt_system.guardrails import check_prompt_injection

        check_prompt_injection(user_content)
    except ImportError:
        pass  # guardrails not available


_GUARDRAIL_MAX_ATTEMPTS = 3


def _run_guardrails_post(
    prompt_name: Optional[str],
    response: str,
    user_content: Optional[str],
    *,
    relax_actions: bool = False,
) -> tuple[bool, str]:
    """Run constraint validation on response after LLM call. Returns (valid, msg)."""
    if not _ENABLE_GUARDRAILS or not prompt_name:
        return (True, "")
    try:
        from agent.prompt_system import get_registry

        valid, msg = get_registry().validate_response(
            prompt_name, response, user_content, relax_actions=relax_actions
        )
        return (valid, msg or "")
    except ImportError:
        return (True, "")


def call_small_model(
    prompt: str,
    max_tokens: Optional[int] = None,
    task_name: Optional[str] = None,
    system_prompt: Optional[str] = None,
    prompt_name: Optional[str] = None,
    debug_replanner: bool = False,
    model_key: Optional[str] = None,
) -> str:
    """Call the model for the given task. Returns model output text.
    When task_name is set, uses params and endpoint from models_config (task_params, task_models).
    When model_key is set, uses that model_key directly (overrides task_models).
    When neither is set, defaults to REASONING model endpoint.
    When system_prompt is provided, sends as system + user messages for grounding/scope.
    When prompt_name is set, runs post-call constraint validation (logs on failure).
    Guardrails: injection check on prompt before call (always when ENABLE_PROMPT_GUARDRAILS=1)."""
    _run_guardrails_pre(prompt)
    _log_bound_prompt_for_llm_call(task_name)
    params = get_model_call_params(task_name)
    limit = max_tokens if max_tokens is not None else params.get("max_tokens") or _DEFAULT_MAX_TOKENS
    # Use provided model_key, or lookup from task_models, or default to REASONING
    resolved_model_key = model_key or TASK_MODELS.get(task_name, "REASONING")
    endpoint = get_endpoint_for_model(resolved_model_key)
    logger.info(
        "call_small_model: endpoint=%s task=%r prompt=%r max_tokens=%s system_prompt=%s",
        endpoint,
        task_name,
        prompt,
        limit,
        bool(system_prompt),
    )
    if system_prompt:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        _sys_for_dump = system_prompt
        _user_for_dump = prompt
    else:
        messages = [{"role": "user", "content": prompt}]
        _sys_for_dump = ""
        _user_for_dump = prompt
    if task_name in _REPLANNER_LLM_DEBUG_TASK_NAMES and debug_replanner:
        _dump_replanner_prompt_files(_sys_for_dump or None, _user_for_dump or "")
    # Stage 28: record real model call (stubs never reach here)
    model_name = get_model_name(resolved_model_key)
    base_url = endpoint.rsplit("/chat/completions", 1)[0].rstrip("/") if "/chat/completions" in endpoint else endpoint.rsplit("/", 1)[0]
    _record_model_call("small", callsite=task_name or "call_small_model", model_name=model_name, base_url=base_url)
    last_msg = ""
    tn = task_name or "SMALL_MODEL"
    for attempt in range(_GUARDRAIL_MAX_ATTEMPTS):
        temperature = 0 if attempt > 0 else params.get("temperature")
        t0 = time.perf_counter()
        response = _call_chat(
            endpoint,
            _normalize_messages_for_backend(messages),
            task_name=task_name,
            model_key=resolved_model_key,
            max_tokens=limit,
            temperature=temperature,
            request_timeout=params.get("request_timeout_seconds"),
            frequency_penalty=params.get("frequency_penalty"),
            presence_penalty=params.get("presence_penalty"),
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        valid, msg = _run_guardrails_post(prompt_name, response, prompt)
        if valid:
            _try_emit_llm_trace(
                task_name=tn,
                prompt=prompt,
                system_prompt=system_prompt,
                output_text=response,
                latency_ms=latency_ms,
                model_name=model_name,
            )
            return response
        last_msg = msg or "unknown"
        logger.warning(
            "[guardrail] failure: prompt=%s attempt=%d reason=%s",
            prompt_name,
            attempt + 1,
            last_msg,
        )
        if attempt == 1 and prompt_name == "planner":
            valid, _ = _run_guardrails_post(prompt_name, response, prompt, relax_actions=True)
            if valid:
                logger.info("[guardrail] recovered via relaxed policy: prompt=%s", prompt_name)
                _try_emit_llm_trace(
                    task_name=tn,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    output_text=response,
                    latency_ms=latency_ms,
                    model_name=model_name,
                )
                return response
        if attempt >= _GUARDRAIL_MAX_ATTEMPTS - 1:
            logger.error("[guardrail] unrecoverable failure: prompt=%s reason=%s", prompt_name, last_msg)
            raise GuardrailError(f"Guardrail validation failed after retries: {last_msg}")
    logger.error("[guardrail] unrecoverable failure: prompt=%s reason=%s", prompt_name, last_msg)
    raise GuardrailError(f"Guardrail validation failed after retries: {last_msg}")


def call_reasoning_model(
    prompt: str,
    system_prompt: Optional[str] = None,
    max_tokens: Optional[int] = None,
    task_name: Optional[str] = None,
    model_type: Optional[str] = None,
    prompt_name: Optional[str] = None,
    debug_replanner: bool = False,
    model_key: Optional[str] = None,
) -> str:
    """
    Call the reasoning model. If system_prompt is given, send as chat with system + user;
    otherwise single user message. Returns model output text.
    When task_name is set, uses params from models_config task_params for that task.
    When model_key is set, uses that model_key directly (highest priority).
    When model_type is set (e.g. REASONING_V2), uses that model's endpoint; else uses
    task_models[task_name] from config.
    When prompt_name is set, runs post-call constraint validation (logs on failure).
    Guardrails: injection check on prompt before call (always when ENABLE_PROMPT_GUARDRAILS=1).
    """
    _run_guardrails_pre(prompt)
    _log_bound_prompt_for_llm_call(task_name, _exploration_suppress_duplicate=True)
    from agent.models.model_config import get_model_for_task

    params = get_model_call_params(task_name)
    limit = max_tokens if max_tokens is not None else params.get("max_tokens") or _DEFAULT_MAX_TOKENS
    # Use provided model_key, or model_type, or lookup from task_models, or default to REASONING
    resolved_model_key = model_key or model_type or get_model_for_task(task_name or "")
    endpoint = get_endpoint_for_model(resolved_model_key)
    _sys = None if system_prompt is None else (system_prompt[:200] + "..." if len(system_prompt) > 200 else system_prompt)
    logger.debug(
        "call_reasoning_model: endpoint=%s model=%s task=%r prompt=%r system_prompt=%s max_tokens=%s",
        endpoint,
        model_key,
        task_name,
        prompt,
        _sys,
        limit,
    )
    if system_prompt:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        _sys_for_dump = system_prompt
        _user_for_dump = prompt
    else:
        messages = [{"role": "user", "content": prompt}]
        _sys_for_dump = ""
        _user_for_dump = prompt
    if task_name in _REPLANNER_LLM_DEBUG_TASK_NAMES and debug_replanner:
        _dump_replanner_prompt_files(_sys_for_dump or None, _user_for_dump or "")
    # Stage 28: record real model call (stubs never reach here)
    model_name = get_model_name(resolved_model_key)
    base_url = endpoint.rsplit("/chat/completions", 1)[0].rstrip("/") if "/chat/completions" in endpoint else endpoint.rsplit("/", 1)[0]
    _record_model_call("reasoning", callsite=task_name or "call_reasoning_model", model_name=model_name, base_url=base_url)
    last_msg = ""
    tn = task_name or "REASONING"
    for attempt in range(_GUARDRAIL_MAX_ATTEMPTS):
        temperature = 0 if attempt > 0 else params.get("temperature")
        t0 = time.perf_counter()
        response = _call_chat(
            endpoint,
            _normalize_messages_for_backend(messages),
            task_name=task_name,
            model_key=resolved_model_key,
            max_tokens=limit,
            temperature=temperature,
            request_timeout=params.get("request_timeout_seconds"),
            frequency_penalty=params.get("frequency_penalty"),
            presence_penalty=params.get("presence_penalty"),
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        valid, msg = _run_guardrails_post(prompt_name, response, prompt)
        if valid:
            _try_emit_llm_trace(
                task_name=tn,
                prompt=prompt,
                system_prompt=system_prompt,
                output_text=response,
                latency_ms=latency_ms,
                model_name=model_name,
            )
            return response
        last_msg = msg or "unknown"
        logger.warning(
            "[guardrail] failure: prompt=%s attempt=%d reason=%s",
            prompt_name,
            attempt + 1,
            last_msg,
        )
        if attempt == 1 and prompt_name == "planner":
            valid, _ = _run_guardrails_post(prompt_name, response, prompt, relax_actions=True)
            if valid:
                logger.info("[guardrail] recovered via relaxed policy: prompt=%s", prompt_name)
                _try_emit_llm_trace(
                    task_name=tn,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    output_text=response,
                    latency_ms=latency_ms,
                    model_name=model_name,
                )
                return response
        if attempt >= _GUARDRAIL_MAX_ATTEMPTS - 1:
            logger.error("[guardrail] unrecoverable failure: prompt=%s reason=%s", prompt_name, last_msg)
            raise GuardrailError(f"Guardrail validation failed after retries: {last_msg}")
    logger.error("[guardrail] unrecoverable failure: prompt=%s reason=%s", prompt_name, last_msg)
    raise GuardrailError(f"Guardrail validation failed after retries: {last_msg}")


def call_reasoning_model_messages(
    messages: list[dict],
    *,
    task_name: Optional[str] = None,
    model_type: Optional[str] = None,
    max_tokens: Optional[int] = None,
    prompt_name: Optional[str] = None,
    model_key: Optional[str] = None,
) -> str:
    """
    Message-native reasoning model call. Keeps legacy wrappers untouched.
    """
    user_prompt = "\n\n".join(
        str(m.get("content") or "")
        for m in (messages or [])
        if str(m.get("role") or "").lower() == "user"
    )
    system_prompt = "\n\n".join(
        str(m.get("content") or "")
        for m in (messages or [])
        if str(m.get("role") or "").lower() == "system"
    )
    _run_guardrails_pre(user_prompt)
    _log_bound_prompt_for_llm_call(task_name, _exploration_suppress_duplicate=True)
    from agent.models.model_config import get_model_for_task

    params = get_model_call_params(task_name)
    limit = max_tokens if max_tokens is not None else params.get("max_tokens") or _DEFAULT_MAX_TOKENS
    # Use provided model_key, or model_type, or lookup from task_models, or default to REASONING
    resolved_model_key = model_key or model_type or get_model_for_task(task_name or "")
    endpoint = get_endpoint_for_model(resolved_model_key)
    model_name = get_model_name(resolved_model_key)
    base_url = endpoint.rsplit("/chat/completions", 1)[0].rstrip("/") if "/chat/completions" in endpoint else endpoint.rsplit("/", 1)[0]
    _record_model_call("reasoning", callsite=task_name or "call_reasoning_model_messages", model_name=model_name, base_url=base_url)
    last_msg = ""
    tn = task_name or "REASONING"
    backend_messages = _normalize_messages_for_backend(messages)
    for attempt in range(_GUARDRAIL_MAX_ATTEMPTS):
        temperature = 0 if attempt > 0 else params.get("temperature")
        t0 = time.perf_counter()
        response = _call_chat(
            endpoint,
            backend_messages,
            task_name=task_name,
            model_key=resolved_model_key,
            max_tokens=limit,
            temperature=temperature,
            request_timeout=params.get("request_timeout_seconds"),
            frequency_penalty=params.get("frequency_penalty"),
            presence_penalty=params.get("presence_penalty"),
        )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        valid, msg = _run_guardrails_post(prompt_name, response, user_prompt)
        if valid:
            _try_emit_llm_trace(
                task_name=tn,
                prompt=user_prompt,
                system_prompt=system_prompt or None,
                output_text=response,
                latency_ms=latency_ms,
                model_name=model_name,  # This is already using resolved_model_key
            )
            return response
        last_msg = msg or "unknown"
        if attempt >= _GUARDRAIL_MAX_ATTEMPTS - 1:
            raise GuardrailError(f"Guardrail validation failed after retries: {last_msg}")
    raise GuardrailError(f"Guardrail validation failed after retries: {last_msg}")
