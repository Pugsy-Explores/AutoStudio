"""
LLM client for router inference. OpenAI-compatible API (LiteLLM / llama-server).
"""

import os
from typing import Optional

# Config via env; defaults work with local llama-server or LiteLLM
DEFAULT_BASE_URL = os.environ.get("ROUTER_LLM_BASE_URL", "http://localhost:8000/v1")
DEFAULT_MODEL = os.environ.get("ROUTER_LLM_MODEL", "reasoning")
DEFAULT_API_KEY = os.environ.get("ROUTER_LLM_API_KEY", "none")


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
    base_url = base_url or DEFAULT_BASE_URL
    model = model or DEFAULT_MODEL
    api_key = api_key or DEFAULT_API_KEY

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
            "temperature": 0.0,
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
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return (content or "").strip()

    client = OpenAI(base_url=base_url, api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.0,
    )
    content = resp.choices[0].message.content if resp.choices else ""
    return (content or "").strip()
