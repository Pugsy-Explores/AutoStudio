"""CLI helpers for agent_v2 runtime."""

import json


VALID_MODES = {"act", "plan", "plan_legacy", "deep_plan", "plan_execute"}


def parse_mode(argv: list[str]) -> tuple[str, list[str]]:
    mode = "act"
    remaining: list[str] = []
    for arg in argv:
        if arg.startswith("--mode="):
            mode = arg.split("=", 1)[1].strip() or "act"
            continue
        remaining.append(arg)
    if mode not in VALID_MODES:
        raise ValueError(f"Invalid mode '{mode}'. Valid modes: {sorted(VALID_MODES)}")
    return mode, remaining


def _state_from_run_result(run_result) -> object:
    if isinstance(run_result, dict) and "state" in run_result:
        return run_result["state"]
    return run_result


def format_output(run_result) -> dict:
    """
    Stable JSON shape for CLI/API: status, trace, result (history), plan, metadata.
    """
    state = _state_from_run_result(run_result)
    status = "unknown"
    trace_payload = None
    if isinstance(run_result, dict):
        status = str(run_result.get("status", "unknown"))
        tr = run_result.get("trace")
        if tr is not None:
            trace_payload = tr.model_dump(mode="json") if hasattr(tr, "model_dump") else tr
    out = {
        "status": status,
        "trace": trace_payload,
        "result": getattr(state, "history", []),
        "plan": getattr(state, "current_plan", None),
        "metadata": getattr(state, "metadata", {}),
    }
    return out


def print_formatted_output(run_result) -> None:
    print(json.dumps(format_output(run_result), indent=2, default=str))
