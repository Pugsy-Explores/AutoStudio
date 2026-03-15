"""Thin facade over agent/retrieval/context_pruner. Plus prune_sections and sliding window."""

from agent.prompt_system.context.context_budget_manager import ContextBudget
from agent.prompt_system.context.context_summarizer import summarize_large_block

_CHARS_PER_TOKEN = 4


def prune(
    ranked: list[dict],
    budget: ContextBudget | None = None,
    max_snippets: int | None = None,
    max_chars: int | None = None,
) -> list[dict]:
    """
    Prune ranked context by snippet count and char budget.
    Delegates to agent/retrieval/context_pruner.
    If budget is provided, uses budget.allocate() to derive limits when max_snippets/max_chars not set.
    """
    from config.retrieval_config import DEFAULT_MAX_CHARS, DEFAULT_MAX_SNIPPETS

    if max_snippets is None:
        max_snippets = DEFAULT_MAX_SNIPPETS
    if max_chars is None:
        max_chars = DEFAULT_MAX_CHARS
    if budget:
        allocated = budget.allocate()
        max_chars = min(max_chars, allocated * _CHARS_PER_TOKEN)
    from agent.retrieval.context_pruner import prune_context as _prune_context

    return _prune_context(ranked, max_snippets=max_snippets, max_chars=max_chars)


def prune_sections(
    sections: dict[str, str],
    token_budget: int,
    token_counts: dict[str, int],
    model_name: str = "default",
) -> dict[str, str]:
    """
    Prune sections to fit token_budget. Priority: repo_context -> history -> skills.
    Never prunes: system, tool_schema, output_schema.
    Truncates from end of each section.
    """
    result = dict(sections)
    total = sum(token_counts.get(k, 0) for k in result if k != "total" and k != "approximate_mode")
    if total <= token_budget:
        return result

    prunable = ["repo_context", "history", "skills"]
    for key in prunable:
        if key not in result or total <= token_budget:
            continue
        val = result[key] or ""
        if not val:
            continue
        current_tokens = token_counts.get(key, 0)
        need_to_cut = total - token_budget
        cut_tokens = min(current_tokens, need_to_cut)
        if cut_tokens <= 0:
            continue
        cut_chars = cut_tokens * _CHARS_PER_TOKEN
        new_len = max(0, len(val) - cut_chars)
        if new_len < len(val):
            result[key] = val[:new_len] + "\n..."
            total -= cut_tokens
        if total <= token_budget:
            break

    return result


def apply_sliding_window(
    history: list[dict],
    model_name: str = "default",
) -> list[dict]:
    """
    Sliding conversation window: keep last N turns raw, summarize older turns.
    - history[0..N-30]: dropped
    - history[N-30..N-10]: summarized into one memory block
    - history[N-10..N]: raw (last 10 turns)
    """
    from config.agent_config import HISTORY_SUMMARY_TURNS, HISTORY_WINDOW_TURNS

    if not history or len(history) <= HISTORY_WINDOW_TURNS:
        return list(history)

    n = len(history)
    drop_count = max(0, n - HISTORY_SUMMARY_TURNS)
    summarize_end = n - HISTORY_WINDOW_TURNS

    if drop_count >= summarize_end:
        return list(history[-HISTORY_WINDOW_TURNS:])

    to_summarize = history[drop_count:summarize_end]
    raw_turns = history[summarize_end:]

    if not to_summarize:
        return list(raw_turns)

    parts = []
    for t in to_summarize:
        role = t.get("role", "unknown")
        content = t.get("content", t.get("text", ""))
        parts.append(f"{role}: {content}")
    combined = "\n\n".join(parts)
    summary = summarize_large_block(combined, max_chars=1500)

    memory_turn = {"role": "system", "content": f"[Earlier conversation summary]: {summary}"}
    return [memory_turn] + list(raw_turns)
