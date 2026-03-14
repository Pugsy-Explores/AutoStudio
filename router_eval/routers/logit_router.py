"""
Logit-based router: uses token probabilities from the LLM (e.g. llama.cpp) instead of text classification.
Request max_tokens=1, temperature=0, logprobs; pick the category token with highest probability.
"""

ROUTER_NAME = "logit"

import math

from agent.prompts import get_prompt
from router_eval.utils.llama_client import DEFAULT_API_KEY, DEFAULT_BASE_URL, DEFAULT_MODEL

# Candidate categories; must match dataset
CATEGORIES = ("EDIT", "SEARCH", "DOCS", "GENERAL", "INFRA")
_CATEGORIES_SET = {c.upper() for c in CATEGORIES}

_DEFAULT_SYSTEM = get_prompt("router_logit_system", "system_prompt")


def _normalize_token(t: str) -> str:
    return (t or "").strip().upper()


def chat_with_logprobs(
    system_prompt: str,
    user_message: str,
    *,
    base_url=None,
    model=None,
    api_key=None,
) -> tuple[str, list]:
    """Call OpenAI-compatible API with logprobs; return (content, logprobs_content_list)."""
    base_url = base_url or DEFAULT_BASE_URL
    model = model or DEFAULT_MODEL
    api_key = api_key or DEFAULT_API_KEY

    from openai import OpenAI

    client = OpenAI(base_url=base_url, api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=1,
        temperature=0,
        logprobs=True,
    )

    content = ""
    logprobs_content = []
    if resp.choices:
        choice = resp.choices[0]
        if choice.message and choice.message.content is not None:
            content = choice.message.content
        if getattr(choice, "logprobs", None) and getattr(choice.logprobs, "content", None):
            logprobs_content = choice.logprobs.content or []
    return content, logprobs_content


def _get_token_logprob(item) -> tuple[str | None, float | None]:
    """Extract (token, logprob) from a content item (object or dict)."""
    token = getattr(item, "token", None) if not isinstance(item, dict) else item.get("token")
    logprob = getattr(item, "logprob", None) if not isinstance(item, dict) else item.get("logprob")
    if token is not None and logprob is not None:
        return str(token).strip(), float(logprob)
    return None, None


def pick_category_from_logprobs(content: str, logprobs_content: list) -> tuple[str, float]:
    """
    From API (content, logprobs_content), pick the category token with highest logprob.
    Returns (category, confidence). Fallback: ("GENERAL", 0.0).
    """
    best_category = "GENERAL"
    best_logprob = -float("inf")

    token_logprobs = []
    if logprobs_content:
        for item in logprobs_content:
            t, lp = _get_token_logprob(item)
            if t is not None and lp is not None:
                token_logprobs.append((t, lp))
            top = getattr(item, "top_logprobs", None) if not isinstance(item, dict) else item.get("top_logprobs")
            if top:
                for tp in top:
                    t, lp = _get_token_logprob(tp)
                    if t is not None and lp is not None:
                        token_logprobs.append((t, lp))

    for token, logprob in token_logprobs:
        norm = _normalize_token(token)
        if norm in _CATEGORIES_SET and logprob > best_logprob:
            best_logprob = logprob
            best_category = norm

    if best_logprob == -float("inf"):
        return "GENERAL", 0.0

    confidence = math.exp(best_logprob)
    confidence = max(0.0, min(1.0, confidence))
    return best_category, confidence


def route(instruction: str) -> dict:
    """
    Use token log-probabilities to choose the category. Return {"category": <best>, "confidence": <score>}.
    Confidence is exp(logprob) of the chosen category token. Fallback: GENERAL with 0.
    """
    try:
        user_message = f"Instruction: {instruction}\nCategory:"
        content, logprobs_content = chat_with_logprobs(_DEFAULT_SYSTEM, user_message)
    except Exception:
        return {"category": "GENERAL", "confidence": 0.0}

    category, confidence = pick_category_from_logprobs(content, logprobs_content)
    return {"category": category, "confidence": confidence}
