"""Compose prompt with optional skill and repo context."""

from agent.prompt_system.context import (
    apply_sliding_window,
    compress,
    count_prompt_tokens,
    rank_and_limit,
    PromptBudgetManager,
)

_CHARS_PER_TOKEN_ESTIMATE = 4


def build_context(
    base_instructions: str,
    skill_block: str | None = None,
    repo_context: str | None = None,
) -> str:
    """
    Compose: base_prompt + skill_block + repo_context_block.
    Used when combining a PromptTemplate with a skill and/or retrieved context.
    """
    parts = [base_instructions]
    if skill_block and skill_block.strip():
        parts.append("\n\n---\n\n")
        parts.append(skill_block.strip())
    if repo_context and repo_context.strip():
        parts.append("\n\n---\n\n")
        parts.append("REPOSITORY CONTEXT:\n\n")
        parts.append(repo_context.strip())
    return "".join(parts)


def _format_snippets(ranked: list[dict]) -> str:
    """Format ranked snippets into repo_context string."""
    lines = []
    for c in ranked:
        f = c.get("file") or ""
        s = c.get("symbol") or ""
        snip = c.get("snippet") or ""
        if f or s:
            lines.append(f"# {f}" + (f" ({s})" if s else ""))
        lines.append(snip)
        lines.append("")
    return "\n".join(lines).strip()


def _format_history(history: list[dict]) -> str:
    """Format history list into string."""
    parts = []
    for t in history:
        role = t.get("role", "unknown")
        content = t.get("content", t.get("text", ""))
        parts.append(f"{role}: {content}")
    return "\n\n".join(parts)


def build_context_budgeted(
    base_instructions: str,
    candidates: list[dict],
    query: str,
    skill_block: str | None = None,
    history: list[dict] | None = None,
    user_input: str = "",
    model_name: str = "default",
    prompt_name: str | None = None,
) -> tuple[str, "BudgetReport"]:
    """
    Build prompt with full budget pipeline:
    rank_and_limit -> apply_sliding_window -> compress (conditional) -> count -> enforce_budget -> assemble.
    Returns (composed_str, BudgetReport).
    """
    from agent.prompt_system.context.prompt_budget_manager import BudgetReport

    history = history or []
    history_windowed = apply_sliding_window(history, model_name)
    ranked = rank_and_limit(query, candidates)
    repo_context_str = _format_snippets(ranked)
    # Cheap estimate (len/4) for compression gate — avoid expensive count_tokens/LLM unless needed
    repo_estimate = len(repo_context_str) // _CHARS_PER_TOKEN_ESTIMATE
    ranked, compression_ratio = compress(ranked, repo_estimate, model_name=model_name)
    repo_context_str = _format_snippets(ranked)
    compression_triggered = compression_ratio > 1.0

    sections = {
        "system": base_instructions,
        "skills": skill_block or "",
        "repo_context": repo_context_str,
        "history": _format_history(history_windowed),
        "user_input": user_input,
    }
    token_counts = count_prompt_tokens(sections, model_name)
    manager = PromptBudgetManager()
    parts, report = manager.enforce_budget(sections, token_counts, model_name, prompt_name)
    report.compression_triggered = compression_triggered

    if report.use_fallback and report.fallback_key:
        from agent.prompt_system import get_registry

        try:
            compact = get_registry().get(report.fallback_key)
            if compact:
                base_instructions = compact.instructions
                parts["system"] = base_instructions
        except Exception:
            pass

    composed = build_context(
        parts.get("system", base_instructions),
        skill_block=parts.get("skills") or None,
        repo_context=parts.get("repo_context") or None,
    )
    if parts.get("history"):
        composed += "\n\n---\n\nCONVERSATION HISTORY:\n\n" + (parts.get("history") or "")
    if user_input:
        composed += "\n\n---\n\nUSER:\n\n" + user_input

    return (composed, report)
