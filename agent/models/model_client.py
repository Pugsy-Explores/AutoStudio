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
"""

import json
import logging
import os
import sys
import threading
import time
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)


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


def _pretty_print_request(messages: list[dict]) -> None:
    """Pretty-print model request (messages) for workflow visibility."""
    print("    [workflow] model request:")
    print("    " + "─" * _PRETTY_WIDTH)
    for m in messages:
        role = (m.get("role") or "user").upper()
        content = (m.get("content") or "").strip()
        lines = content.splitlines()
        if len(lines) > _MAX_PRETTY_LINES:
            lines = lines[:_MAX_PRETTY_LINES] + [f"... ({len(lines) - _MAX_PRETTY_LINES} more lines)"]
        print(f"    [{role}]")
        for line in lines:
            print("    " + line)
        print("    " + "·" * min(_PRETTY_WIDTH, 40))
    print("    " + "─" * _PRETTY_WIDTH)


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


def _call_chat(
    endpoint: str,
    messages: list[dict],
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
    logger.info(
        "model call: endpoint=%s max_tokens=%s temperature=%s timeout=%s messages=%s",
        endpoint,
        max_tokens,
        payload["temperature"],
        timeout,
        messages,
    )
    _pretty_print_request(messages)

    def _do_call() -> str:
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
                            choices = chunk.get("choices", [])
                            if not choices:
                                continue
                            d = choices[0].get("delta") or {}
                            reasoning = d.get("reasoning_content")
                            delta = d.get("content")
                            if reasoning:
                                _stream_chunk_to_terminal(reasoning)
                            if delta:
                                content_parts.append(delta)
                                _stream_chunk_to_terminal(delta)
            print()
            print("    " + "─" * _PRETTY_WIDTH)
            content = "".join(content_parts).strip()
            if not content:
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
        stream = client.chat.completions.create(**create_kwargs)
        content_parts = []
        reasoning_parts: list[str] = []
        finish_reason = None
        print("    [workflow] model response (streaming):")
        print("    " + "─" * _PRETTY_WIDTH)
        for chunk in stream:
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
        content = "".join(content_parts).strip()
        if finish_reason == "length" and content:
            content = content + "\n[Response truncated - consider increasing max_tokens]"
            logger.warning("[model_client] finish_reason=length; response truncated")
        if not content and not reasoning_parts:
            _debug_empty_response(None)
        return content

    return _retry_with_exponential_backoff(_do_call)


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
) -> str:
    """Call the model for the given task. Returns model output text.
    When task_name is set, uses params and endpoint from models_config (task_params, task_models).
    When task_name is None, uses SMALL model endpoint.
    When system_prompt is provided, sends as system + user messages for grounding/scope.
    When prompt_name is set, runs post-call constraint validation (logs on failure).
    Guardrails: injection check on prompt before call (always when ENABLE_PROMPT_GUARDRAILS=1)."""
    _run_guardrails_pre(prompt)
    params = get_model_call_params(task_name)
    limit = max_tokens if max_tokens is not None else params.get("max_tokens") or _DEFAULT_MAX_TOKENS
    # Resolve endpoint from task_models: task_name -> model_key (SMALL, REASONING, etc.)
    if not task_name or task_name not in TASK_MODELS:
        raise ValueError(
            f"call_small_model requires task_name in task_models; got task_name={task_name!r}. "
            f"Available: {list(TASK_MODELS.keys())}"
        )
    model_key = TASK_MODELS[task_name]
    endpoint = get_endpoint_for_model(model_key)
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
    if task_name == "replanner" and debug_replanner:
        _dump_replanner_prompt_files(_sys_for_dump or None, _user_for_dump or "")
    # Stage 28: record real model call (stubs never reach here)
    model_name = get_model_name(model_key)
    base_url = endpoint.rsplit("/chat/completions", 1)[0].rstrip("/") if "/chat/completions" in endpoint else endpoint.rsplit("/", 1)[0]
    _record_model_call("small", callsite=task_name or "call_small_model", model_name=model_name, base_url=base_url)
    last_msg = ""
    tn = task_name or "SMALL_MODEL"
    for attempt in range(_GUARDRAIL_MAX_ATTEMPTS):
        temperature = 0 if attempt > 0 else params.get("temperature")
        t0 = time.perf_counter()
        response = _call_chat(
            endpoint,
            messages,
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
) -> str:
    """
    Call the reasoning model. If system_prompt is given, send as chat with system + user;
    otherwise single user message. Returns model output text.
    When task_name is set, uses params from models_config task_params for that task.
    When model_type is set (e.g. REASONING_V2), uses that model's endpoint; else uses
    task_models[task_name] from config.
    When prompt_name is set, runs post-call constraint validation (logs on failure).
    Guardrails: injection check on prompt before call (always when ENABLE_PROMPT_GUARDRAILS=1).
    """
    _run_guardrails_pre(prompt)
    from agent.models.model_config import TASK_MODELS

    params = get_model_call_params(task_name)
    limit = max_tokens if max_tokens is not None else params.get("max_tokens") or _DEFAULT_MAX_TOKENS
    model_key = model_type or (TASK_MODELS.get(task_name or "") if task_name else None) or "REASONING"
    endpoint = get_endpoint_for_model(model_key)
    _sys = None if system_prompt is None else (system_prompt[:200] + "..." if len(system_prompt) > 200 else system_prompt)
    logger.info(
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
    if task_name == "replanner" and debug_replanner:
        _dump_replanner_prompt_files(_sys_for_dump or None, _user_for_dump or "")
    # Stage 28: record real model call (stubs never reach here)
    model_name = get_model_name(model_key)
    base_url = endpoint.rsplit("/chat/completions", 1)[0].rstrip("/") if "/chat/completions" in endpoint else endpoint.rsplit("/", 1)[0]
    _record_model_call("reasoning", callsite=task_name or "call_reasoning_model", model_name=model_name, base_url=base_url)
    last_msg = ""
    tn = task_name or "REASONING"
    for attempt in range(_GUARDRAIL_MAX_ATTEMPTS):
        temperature = 0 if attempt > 0 else params.get("temperature")
        t0 = time.perf_counter()
        response = _call_chat(
            endpoint,
            messages,
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
