"""Build compact execution trace rows from runtime state."""

import logging

_LOG = logging.getLogger(__name__)


def extract_target(step: dict) -> str | None:
    """Extract a best-effort target from a history step."""
    args = step.get("args", {})

    if not isinstance(args, dict):
        return None

    # common cases
    if "path" in args:
        return args["path"]

    if "file" in args:
        return args["file"]

    if "query" in args:
        return args["query"]

    if "command" in args:
        return args["command"]

    return None


def build_trace(state) -> list[dict]:
    """Return compact rows: step/action/target/success/error."""
    rows = []

    history = state.history or []
    results = state.step_results or []

    if len(history) != len(results):
        _LOG.warning(
            "trace mismatch: history=%s step_results=%s",
            len(history),
            len(results),
        )

    max_len = min(len(history), len(results))
    for i in range(max_len):
        step = history[i]
        result = results[i]
        rows.append(
            {
                "step": i + 1,
                "action": step.get("action"),
                "target": extract_target(step),
                "success": result.get("success"),
                "error": result.get("error"),
            }
        )

    # Preserve visibility of a trailing FINISH step when the loop exits
    # before appending a matching step_result.
    if len(history) > len(results):
        last = history[-1]
        if isinstance(last, dict) and (last.get("action") or "").lower() == "finish":
            rows.append(
                {
                    "step": len(history),
                    "action": last.get("action"),
                    "target": extract_target(last),
                    "success": True,
                    "error": None,
                }
            )

    return rows
