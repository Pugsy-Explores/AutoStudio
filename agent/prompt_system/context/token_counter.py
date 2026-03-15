"""Token counting for prompt budget management. Supports tiktoken, sentencepiece, or approximate fallback."""

from __future__ import annotations

_CHARS_PER_TOKEN_APPROX = 4

# Lazy imports for optional tokenizers
_tiktoken_available: bool | None = None
_sentencepiece_available: bool | None = None


def _check_tiktoken() -> bool:
    global _tiktoken_available
    if _tiktoken_available is not None:
        return _tiktoken_available
    try:
        import tiktoken  # noqa: F401

        _tiktoken_available = True
    except ImportError:
        _tiktoken_available = False
    return _tiktoken_available


def _check_sentencepiece() -> bool:
    global _sentencepiece_available
    if _sentencepiece_available is not None:
        return _sentencepiece_available
    try:
        import sentencepiece  # noqa: F401

        _sentencepiece_available = True
    except ImportError:
        _sentencepiece_available = False
    return _sentencepiece_available


def _get_tiktoken_encoding(model_name: str):
    """Get tiktoken encoding for model. Falls back to cl100k_base for OpenAI models."""
    try:
        import tiktoken

        model_lower = (model_name or "").lower()
        if "gpt-4" in model_lower or "gpt-3.5" in model_lower or "o1" in model_lower:
            return tiktoken.encoding_for_model(model_name or "gpt-4")
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(
    text: str,
    model_name: str = "default",
    *,
    approximate_mode: bool = False,
) -> tuple[int, bool]:
    """
    Count tokens in text. Returns (count, approximate_mode_used).

    Tries: tiktoken (OpenAI) -> sentencepiece -> len(text)//4 fallback.
    approximate_mode is True when using fallback; False when using real tokenizer.
    """
    if not text:
        return (0, False)

    if _check_tiktoken():
        try:
            enc = _get_tiktoken_encoding(model_name)
            return (len(enc.encode(text)), False)
        except Exception:
            pass

    if _check_sentencepiece():
        try:
            import sentencepiece as spm

            # Use a generic model if we have one; otherwise fall through to approx
            # sentencepiece typically needs a .model file - we may not have one
            pass
        except Exception:
            pass

    # Fallback: ~4 chars per token heuristic
    return (max(0, len(text) // _CHARS_PER_TOKEN_APPROX), True)


def count_prompt_tokens(
    parts: dict[str, str],
    model_name: str = "default",
) -> dict[str, int]:
    """
    Count tokens per prompt section. parts keys: system, skills, repo_context, history, user_input.
    Returns dict with per-key counts + "total" and "approximate_mode".
    """
    result: dict[str, int] = {}
    total = 0
    any_approx = False

    for key in ("system", "skills", "repo_context", "history", "user_input"):
        val = parts.get(key) or ""
        cnt, approx = count_tokens(val, model_name)
        result[key] = cnt
        total += cnt
        if approx:
            any_approx = True

    result["total"] = total
    result["approximate_mode"] = 1 if any_approx else 0  # int for JSON-serializable
    return result
