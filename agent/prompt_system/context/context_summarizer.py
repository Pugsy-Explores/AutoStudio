"""Summarize large code blocks when they exceed per-snippet char limit."""

from agent.models.model_client import call_small_model


def summarize_large_block(code_block: str, max_chars: int = 2000) -> str:
    """
    Summarize a code block that exceeds max_chars.
    Uses small model to produce a concise summary.
    """
    if not code_block or len(code_block) <= max_chars:
        return code_block

    prompt = f"""Summarize this code block in 2-4 sentences. Focus on: main purpose, key functions/classes, and important logic.
Keep the summary under {max_chars} characters.

Code:
{code_block[:8000]}

Summary:"""
    try:
        out = call_small_model(prompt, task_name="context_summarize", max_tokens=256)
        return (out or "").strip() or code_block[:max_chars] + "..."
    except Exception:
        return code_block[:max_chars] + "..."
