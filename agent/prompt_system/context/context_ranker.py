"""Thin facade over agent/retrieval/context_ranker."""


def rank_context(query: str, candidates: list[dict]) -> list[dict]:
    """
    Rank candidates by hybrid score (LLM + symbol + filename + reference).
    Delegates to agent/retrieval/context_ranker.rank_context.
    """
    from agent.retrieval.context_ranker import rank_context as _rank_context

    return _rank_context(query, candidates)


def rank_and_limit(
    query: str,
    candidates: list[dict],
    *,
    max_files: int = 5,
    max_snippets: int = 10,
    max_code_lines: int = 300,
) -> list[dict]:
    """
    Rank candidates then apply hard limits: max unique files, max snippets, max lines per snippet.
    """
    ranked = rank_context(query, candidates)

    # Limit by unique files: allow at most max_files distinct files; keep adding snippets until max_snippets
    seen_files: set[str] = set()
    by_file: list[dict] = []
    for c in ranked:
        f = c.get("file") or ""
        if f and f not in seen_files:
            if len(seen_files) >= max_files:
                continue  # already at max_files unique files, skip new file
            seen_files.add(f)
        by_file.append(c)
        if len(by_file) >= max_snippets:
            break

    limited = by_file

    # Truncate per-snippet to max_code_lines
    result: list[dict] = []
    for c in limited:
        snip = c.get("snippet") or ""
        lines = snip.splitlines()
        if len(lines) > max_code_lines:
            truncated = "\n".join(lines[:max_code_lines]) + "\n..."
            out = dict(c)
            out["snippet"] = truncated
            result.append(out)
        else:
            result.append(dict(c))

    return result
