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
    MODEL_API_KEY,
    REASONING_MODEL_ENDPOINT,
    SMALL_MODEL_ENDPOINT,
)

_DEFAULT_MAX_TOKENS = int(os.environ.get("MODEL_MAX_TOKENS", "1024"))
_TIMEOUT = int(os.environ.get("MODEL_REQUEST_TIMEOUT", "120"))

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


def _pretty_print_response(content: str) -> None:
    """Pretty-print model response for workflow visibility."""
    print("    [workflow] model response:")
    print("    " + "─" * _PRETTY_WIDTH)
    out = (content or "").strip()
    if not out:
        print("    (empty)")
    else:
        lines = out.splitlines()
        if len(lines) > _MAX_PRETTY_LINES:
            lines = lines[:_MAX_PRETTY_LINES] + [f"... ({len(lines) - _MAX_PRETTY_LINES} more lines)"]
        for line in lines:
            print("    " + line)
    print("    " + "─" * _PRETTY_WIDTH)


def _call_chat(endpoint: str, messages: list[dict], max_tokens: int = _DEFAULT_MAX_TOKENS) -> str:
    """POST to OpenAI-compatible chat completions endpoint; return assistant content."""
    payload = {
        "model": "default",
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    logger.info(
        "model call: endpoint=%s max_tokens=%s temperature=%s messages=%s",
        endpoint,
        max_tokens,
        payload["temperature"],
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
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        content = (content or "").strip()
        _pretty_print_response(content)
        return content

    base_url = endpoint.rsplit("/chat/completions", 1)[0].rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = endpoint.rsplit("/", 1)[0]
    client = OpenAI(base_url=base_url, api_key=MODEL_API_KEY)
    try:
        resp = client.chat.completions.create(
            model="default",
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.0,
        )
        content = resp.choices[0].message.content if resp.choices else ""
        content = (content or "").strip()
        _pretty_print_response(content)
        return content
    except Exception as e:
        err_msg = str(e)
        if "500" in err_msg or "Compute error" in err_msg or "server_error" in err_msg:
            raise RuntimeError(
                f"Model API error: {e}. "
                f"Check that the model server is running at {endpoint} and MODEL_API_KEY is set if required."
            ) from e
        raise


def call_small_model(prompt: str, max_tokens: Optional[int] = None) -> str:
    """Call the small (fast/cheap) model with a single user prompt. Returns model output text."""
    max_tokens = max_tokens or _DEFAULT_MAX_TOKENS
    logger.info(
        "call_small_model: endpoint=%s prompt=%r max_tokens=%s",
        SMALL_MODEL_ENDPOINT,
        prompt,
        max_tokens,
    )
    messages = [{"role": "user", "content": prompt}]
    return _call_chat(SMALL_MODEL_ENDPOINT, messages, max_tokens=max_tokens)


def call_reasoning_model(
    prompt: str,
    system_prompt: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> str:
    """
    Call the reasoning model. If system_prompt is given, send as chat with system + user;
    otherwise single user message. Returns model output text.
    """
    max_tokens = max_tokens or _DEFAULT_MAX_TOKENS
    _sys = None if system_prompt is None else (system_prompt[:200] + "..." if len(system_prompt) > 200 else system_prompt)
    logger.info(
        "call_reasoning_model: endpoint=%s prompt=%r system_prompt=%s max_tokens=%s",
        REASONING_MODEL_ENDPOINT,
        prompt,
        _sys,
        max_tokens,
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
        max_tokens=max_tokens,
    )
