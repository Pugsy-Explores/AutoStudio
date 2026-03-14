"""
Single entry point for all model calls. Uses llama.cpp HTTP API (OpenAI-compatible).
No other module should call models directly.
"""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Use config from same package
from agent.models.model_config import (
    get_model_call_params,
    MODEL_API_KEY,
    MODEL_MAX_TOKENS,
    MODEL_REQUEST_TIMEOUT,
    MODEL_TEMPERATURE,
    REASONING_MODEL_ENDPOINT,
    SMALL_MODEL_ENDPOINT,
)

_DEFAULT_MAX_TOKENS = MODEL_MAX_TOKENS
_TIMEOUT = MODEL_REQUEST_TIMEOUT

_MAX_PRETTY_LINES = 40
_PRETTY_WIDTH = 72


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


def _call_chat(
    endpoint: str,
    messages: list[dict],
    max_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    request_timeout: Optional[int] = None,
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
    logger.info(
        "model call: endpoint=%s max_tokens=%s temperature=%s timeout=%s messages=%s",
        endpoint,
        max_tokens,
        payload["temperature"],
        timeout,
        messages,
    )
    _pretty_print_request(messages)
    try:
        from openai import OpenAI
    except ImportError:
        import urllib.request
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {MODEL_API_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = (data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
        _pretty_print_response(content, data if not content else None)
        return content

    base_url = endpoint.rsplit("/chat/completions", 1)[0].rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = endpoint.rsplit("/", 1)[0]
    # Use configured timeout so connection/read don't fall back to client default (e.g. 60s)
    client = OpenAI(base_url=base_url, api_key=MODEL_API_KEY, timeout=float(max(1, timeout)))
    try:
        create_kwargs: dict = {
            "model": "default",
            "messages": messages,
            "temperature": temp,
        }
        if max_tokens is not None:
            create_kwargs["max_tokens"] = max_tokens
        resp = client.chat.completions.create(**create_kwargs)
        content = (resp.choices[0].message.content if resp.choices else "") or ""
        content = content.strip()
        _pretty_print_response(content, resp if not content else None)
        return content
    except Exception as e:
        err_msg = str(e)
        if "500" in err_msg or "Compute error" in err_msg or "server_error" in err_msg:
            raise RuntimeError(
                f"Model API error: {e}. "
                f"Check that the model server is running at {endpoint} and MODEL_API_KEY is set if required."
            ) from e
        raise


def call_small_model(
    prompt: str,
    max_tokens: Optional[int] = None,
    task_name: Optional[str] = None,
) -> str:
    """Call the small (fast/cheap) model with a single user prompt. Returns model output text.
    When task_name is set, uses params from models_config task_params for that task."""
    params = get_model_call_params(task_name)
    limit = max_tokens if max_tokens is not None else params.get("max_tokens") or _DEFAULT_MAX_TOKENS
    logger.info(
        "call_small_model: endpoint=%s task=%r prompt=%r max_tokens=%s",
        SMALL_MODEL_ENDPOINT,
        task_name,
        prompt,
        limit,
    )
    messages = [{"role": "user", "content": prompt}]
    return _call_chat(
        SMALL_MODEL_ENDPOINT,
        messages,
        max_tokens=limit,
        temperature=params.get("temperature"),
        request_timeout=params.get("request_timeout_seconds"),
    )


def call_reasoning_model(
    prompt: str,
    system_prompt: Optional[str] = None,
    max_tokens: Optional[int] = None,
    task_name: Optional[str] = None,
) -> str:
    """
    Call the reasoning model. If system_prompt is given, send as chat with system + user;
    otherwise single user message. Returns model output text.
    When task_name is set, uses params from models_config task_params for that task.
    """
    params = get_model_call_params(task_name)
    limit = max_tokens if max_tokens is not None else params.get("max_tokens") or _DEFAULT_MAX_TOKENS
    _sys = None if system_prompt is None else (system_prompt[:200] + "..." if len(system_prompt) > 200 else system_prompt)
    logger.info(
        "call_reasoning_model: endpoint=%s task=%r prompt=%r system_prompt=%s max_tokens=%s",
        REASONING_MODEL_ENDPOINT,
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
    else:
        messages = [{"role": "user", "content": prompt}]
    return _call_chat(
        REASONING_MODEL_ENDPOINT,
        messages,
        max_tokens=limit,
        temperature=params.get("temperature"),
        request_timeout=params.get("request_timeout_seconds"),
    )
