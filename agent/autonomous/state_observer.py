"""State observer: builds ObservationBundle from repo_map, symbol_graph, trace, retrieval."""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ObservationBundle:
    """Structured observation for action selector. Goal + recent steps + repo context summary."""

    goal: str
    recent_steps: list[dict] = field(default_factory=list)  # [{action, description, success, output_summary}]
    repo_context_summary: str = ""
    ranked_context_preview: str = ""
    trace_summary: str = ""


def observe(
    goal: str,
    project_root: str,
    completed_steps: list,
    step_results: list,
    context: dict,
    max_recent_steps: int = 5,
    max_context_chars: int = 4000,
) -> ObservationBundle:
    """
    Build ObservationBundle from state. Reuses repo_map, symbol_graph, retrieval results, execution trace.
    Does NOT run retrieval; reads from context/state.
    """
    recent_steps = []
    steps_slice = completed_steps[-max_recent_steps:]
    results_slice = step_results[-max_recent_steps:]
    for i, step in enumerate(steps_slice):
        if not isinstance(step, dict):
            continue
        result = results_slice[i] if i < len(results_slice) else None
        action = (step.get("action") or "?").upper()
        desc = (step.get("description") or "")[:200]
        success = getattr(result, "success", False) if result else False
        out = getattr(result, "output", "") if result else ""
        if isinstance(out, dict):
            out_summary = str(out.get("results", out))[:150] if out else ""
        else:
            out_summary = (str(out) or "")[:150]
        recent_steps.append({
            "action": action,
            "description": desc,
            "success": success,
            "output_summary": out_summary,
        })

    repo_context_summary = _build_repo_context_summary(project_root, context)
    ranked_context_preview = _build_ranked_context_preview(context, max_chars=max_context_chars // 2)
    trace_summary = _build_trace_summary(context, max_chars=max_context_chars // 2)

    return ObservationBundle(
        goal=goal,
        recent_steps=recent_steps,
        repo_context_summary=repo_context_summary,
        ranked_context_preview=ranked_context_preview,
        trace_summary=trace_summary,
    )


def _build_repo_context_summary(project_root: str, context: dict) -> str:
    """Summarize repo_map and symbol_graph info from context."""
    parts = []
    anchor = context.get("repo_map_anchor")
    if anchor:
        parts.append(f"repo_map_anchor: {anchor}")
    candidates = context.get("repo_map_candidates") or []
    if candidates:
        parts.append(f"repo_map_candidates: {len(candidates)} items")
    files = context.get("files") or context.get("retrieved_files") or []
    if files:
        parts.append(f"retrieved_files: {', '.join(str(f) for f in files[:5])}{'...' if len(files) > 5 else ''}")
    symbols = context.get("retrieved_symbols") or []
    if symbols:
        parts.append(f"retrieved_symbols: {len(symbols)}")
    if not parts:
        try:
            from agent.retrieval.repo_map_lookup import load_repo_map
            repo_map = load_repo_map(project_root)
            if repo_map and isinstance(repo_map, dict):
                modules = repo_map.get("modules") or []
                parts.append(f"repo_map modules: {len(modules)}")
        except Exception as e:
            logger.debug("[state_observer] repo_map load skipped: %s", e)
    return "\n".join(parts) if parts else "No repo context yet."


def _build_ranked_context_preview(context: dict, max_chars: int = 2000) -> str:
    """Preview of ranked_context for action selector."""
    ranked = context.get("ranked_context") or []
    if not ranked:
        return ""
    parts = []
    total = 0
    for r in ranked[:10]:
        if not isinstance(r, dict):
            continue
        f = r.get("file") or "(no file)"
        sym = r.get("symbol") or ""
        snip = (r.get("snippet") or "").strip()[:300]
        block = f"[{f}] {sym}: {snip}..."
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts) if parts else ""


def _build_trace_summary(context: dict, max_chars: int = 2000) -> str:
    """Summary of execution trace from context."""
    tool_memories = context.get("tool_memories") or []
    if not tool_memories:
        return ""
    parts = []
    total = 0
    for m in tool_memories[-5:]:
        if not isinstance(m, dict):
            continue
        tool = m.get("tool", "")
        query = m.get("query", "")[:80]
        result_count = m.get("result_count")
        line = f"{tool}: {query}"
        if result_count is not None:
            line += f" -> {result_count} results"
        if total + len(line) > max_chars:
            break
        parts.append(line)
        total += len(line)
    return "\n".join(parts) if parts else ""
