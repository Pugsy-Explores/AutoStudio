"""Aggregate per-prompt metrics from trace log entries."""

from pathlib import Path

from agent.prompt_system.observability.prompt_metrics import PromptUsageMetric
from config.observability_config import get_trace_dir


def _parse_trace_files(traces_dir: Path) -> list[dict]:
    """Parse trace JSON files, yield stage summaries with prompt_name if present."""
    import json

    entries: list[dict] = []
    if not traces_dir.is_dir():
        return entries
    for p in sorted(traces_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            for stage in data.get("stages", []):
                summary = stage.get("summary") or {}
                if summary.get("prompt_name") or summary.get("prompt"):
                    tokens = summary.get("tokens", 0)
                    if "input_tokens" in summary and "output_tokens" in summary:
                        input_tokens = summary["input_tokens"]
                        output_tokens = summary["output_tokens"]
                    else:
                        tok = int(tokens) if isinstance(tokens, (int, float)) else 0
                        input_tokens = tok // 2
                        output_tokens = tok - tok // 2
                    entries.append({
                        "prompt_name": summary.get("prompt_name") or summary.get("prompt", "unknown"),
                        "version": summary.get("version", "v1"),
                        "success": summary.get("success", True),
                        "tokens": tokens,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "latency_ms": stage.get("latency_ms", 0.0),
                        "tool": summary.get("tool"),
                    })
        except (OSError, json.JSONDecodeError):
            pass
    return entries


def generate_report(project_root: str | None = None) -> list[PromptUsageMetric]:
    """
    Aggregate per-prompt metrics from trace files.
    Returns list of PromptUsageMetric. Trace stages must include prompt_name in summary for inclusion.
    """
    traces_dir = Path(get_trace_dir(project_root))
    entries = _parse_trace_files(traces_dir)

    # Group by (prompt_name, version)
    groups: dict[tuple[str, str], list[dict]] = {}
    for e in entries:
        key = (e["prompt_name"], e["version"])
        if key not in groups:
            groups[key] = []
        groups[key].append(e)

    metrics: list[PromptUsageMetric] = []
    for (name, version), items in groups.items():
        n = len(items)
        successes = sum(1 for i in items if i.get("success", True))
        total_latency = sum(i.get("latency_ms", 0.0) for i in items)
        tool_usage: dict[str, int] = {}
        for i in items:
            t = i.get("tool") or "unknown"
            tool_usage[t] = tool_usage.get(t, 0) + 1

        total_input = 0
        total_output = 0
        for i in items:
            tok = i.get("tokens", 0)
            if "input_tokens" in i and "output_tokens" in i:
                total_input += i["input_tokens"]
                total_output += i["output_tokens"]
            else:
                total_input += tok // 2 if isinstance(tok, int) else 0
                total_output += (tok - tok // 2) if isinstance(tok, int) else 0

        avg_input = total_input // n if n else 0
        avg_output = total_output // n if n else 0

        metrics.append(
            PromptUsageMetric(
                prompt_name=name,
                version=version,
                prompt_usage=n,
                success_rate=successes / n if n else 0.0,
                failure_rate=1.0 - (successes / n) if n else 0.0,
                avg_latency_ms=total_latency / n if n else 0.0,
                token_usage={"avg_input": avg_input, "avg_output": avg_output},
                tool_usage=tool_usage,
            )
        )

    return metrics
