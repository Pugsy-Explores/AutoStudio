"""
LLM client for router inference. OpenAI-compatible API (LiteLLM / llama-server).
Reads from agent/models/models_config.json: task_models.routing -> model endpoint.
"""

import os
from typing import Optional


def _load_router_config() -> tuple[str, str]:
    """Load base_url and api_key from models_config.json (routing task -> SMALL model)."""
    try:
        from agent.models.model_config import (
            TASK_MODELS,
            get_endpoint_for_model,
            MODEL_API_KEY,
        )
        model_key = (TASK_MODELS.get("routing") or "SMALL").upper()
        endpoint = get_endpoint_for_model(model_key)
        # endpoint is full URL e.g. http://localhost:8081/v1/chat/completions
        base_url = endpoint.rsplit("/chat/completions", 1)[0].rstrip("/") if "/chat/completions" in endpoint else endpoint
        return base_url, MODEL_API_KEY or "none"
    except ImportError:
        pass
    # Fallback: env vars, then hardcoded
    base = os.environ.get("ROUTER_LLM_BASE_URL", "http://localhost:8000/v1")
    key = os.environ.get("ROUTER_LLM_API_KEY", "none")
    return base, key


_DEFAULT_BASE_URL, _DEFAULT_API_KEY = _load_router_config()
DEFAULT_MODEL = os.environ.get("ROUTER_LLM_MODEL", "default")


def llama_chat(
    system_prompt: str,
    user_message: str,
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> str:
    """
    Send system + user message to the LLM; return raw response text.
    Uses OpenAI-compatible API (e.g. LiteLLM or llama-server).
    """
    base_url = base_url or _DEFAULT_BASE_URL
    model = model or DEFAULT_MODEL
    api_key = api_key or _DEFAULT_API_KEY

    # Use routing task params from models_config (temperature, timeout)
    try:
        from agent.models.model_config import get_model_call_params
        params = get_model_call_params("routing")
        temperature = params.get("temperature", 0.0)
        timeout = params.get("request_timeout_seconds", 600)
    except ImportError:
        temperature = 0.0
        timeout = 60

    try:
        from openai import OpenAI
    except ImportError:
        import urllib.request
        import json

        # Fallback without openai package: raw HTTP
        url = f"{base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return (content or "").strip()

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=float(max(1, timeout)))
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=temperature,
    )
    content = resp.choices[0].message.content if resp.choices else ""
    return (content or "").strip()
