"""Extract FailureRecords from trajectories with loop and hallucination detection."""

import re
from dataclasses import dataclass

from agent.failure_mining.failure_taxonomy import FAILURE_TYPES


@dataclass
class FailureRecord:
    """One failure/success record from a trajectory attempt."""

    task_id: str
    attempt: int
    failure_type: str  # from FAILURE_TYPES
    failing_step: str
    retry_strategy: str | None
    prompt_tokens: int
    repo_tokens: int
    trajectory_length: int
    step_type: str
    status: str  # "success" | "failure"

# Critic diagnosis failure_type -> our FAILURE_TYPES
_CRITIC_TO_TAXONOMY = {
    "retrieval_miss": "retrieval_miss",
    "bad_patch": "incorrect_patch",
    "missing_dependency": "wrong_file_localization",
    "timeout": "timeout",
    "bad_plan": "premature_completion",
    "unknown": "unknown",
}

# Keyword inference for trace text
_KEYWORD_PATTERNS = [
    (r"retrieval_miss|wrong file|irrelevant|not found|no results", "retrieval_miss"),
    (r"wrong_file|localization|wrong location", "wrong_file_localization"),
    (r"patch failed|could not apply|conflict|invalid patch", "incorrect_patch"),
    (r"syntax error|parse error|indentation", "syntax_error_patch"),
    (r"test fail|assertion|pytest", "test_failure"),
    (r"tool error|dispatch failed|policy", "tool_error"),
    (r"timeout|max_runtime|max_steps", "timeout"),
    (r"hallucinated|api not found", "hallucinated_api"),
    (r"premature|stopped early", "premature_completion"),
]

# Python identifier pattern (snake_case, PascalCase) min 3 chars
_SYMBOL_PATTERN = re.compile(r"\b([A-Z][a-zA-Z0-9_]{2,}|[a-z][a-z0-9_]{2,})\b")


def _detect_loop(steps: list[dict]) -> bool:
    """True if identical step action repeats >= 3 times consecutively."""
    if len(steps) < 3:
        return False
    actions = [s.get("action") or "" for s in steps]
    i = 0
    while i <= len(actions) - 3:
        a = actions[i]
        if a and actions[i + 1] == a and actions[i + 2] == a:
            return True
        i += 1
    return False


def _extract_symbols_from_text(text: str) -> list[str]:
    """Extract candidate symbol names (PascalCase, snake_case) from text."""
    if not text:
        return []
    return list(set(_SYMBOL_PATTERN.findall(text)))


def _check_hallucinated_symbol(
    steps: list[dict],
    project_root: str | None,
) -> bool:
    """
    True if patch/reasoning references a symbol not in repo graph.
    Uses step descriptions. Skips if project_root is None or graph missing.
    """
    if not project_root:
        return False
    try:
        from config.repo_graph_config import INDEX_SQLITE, SYMBOL_GRAPH_DIR
        from pathlib import Path
        from repo_graph.graph_query import find_symbol
        from repo_graph.graph_storage import GraphStorage

        root = Path(project_root).resolve()
        db_path = root / SYMBOL_GRAPH_DIR / INDEX_SQLITE
        if not db_path.exists():
            return False

        storage = GraphStorage(str(db_path))
        all_text = " ".join(
            (s.get("description") or "") for s in steps
        )
        symbols = _extract_symbols_from_text(all_text)
        for sym in symbols:
            if find_symbol(sym, storage) is None:
                return True
        return False
    except Exception:
        return False


def _infer_failure_type_from_trace(
    steps: list[dict],
    evaluation: dict | None,
    diagnosis: dict | None,
) -> str:
    """Infer failure_type from trace when diagnosis is absent or unknown."""
    reason = (evaluation or {}).get("reason", "")
    ev_status = (evaluation or {}).get("status", "")
    text = f"{reason} {ev_status}".lower()
    for pattern, ft in _KEYWORD_PATTERNS:
        if re.search(pattern, text, re.I):
            return ft
    # Check step descriptions
    for s in (steps or []):
        desc = (s.get("description") or "").lower()
        for pattern, ft in _KEYWORD_PATTERNS:
            if re.search(pattern, desc, re.I):
                return ft
    return "unknown"


def extract_records(
    trajectories: list[dict],
    project_root: str | None = None,
) -> list[FailureRecord]:
    """
    Convert trajectories into FailureRecords.
    One record per attempt (success or failure).
    """
    records: list[FailureRecord] = []
    for traj in trajectories:
        task_id = traj.get("task_id", "")
        status = traj.get("status", "failure")
        attempts = traj.get("attempts", [])

        for att in attempts:
            steps = att.get("steps", [])
            trajectory_length = len(steps)
            diagnosis = att.get("diagnosis")
            strategy = att.get("strategy")
            evaluation = att.get("evaluation") or {}

            # step_type: action of last step, or first failed step
            step_type = "unknown"
            failing_step = ""
            for s in reversed(steps):
                action = s.get("action")
                if action:
                    step_type = str(action)
                    failing_step = (s.get("description") or "")[:200]
                    break

            # Token counts from evaluation if available
            prompt_tokens = evaluation.get("prompt_tokens", 0) or 0
            repo_tokens = evaluation.get("repo_tokens", 0) or 0

            # failure_type resolution (order of precedence)
            diagnosis_ft = (diagnosis or {}).get("failure_type", "")
            if diagnosis_ft:
                failure_type = _CRITIC_TO_TAXONOMY.get(
                    diagnosis_ft.lower(), diagnosis_ft.lower()
                )
                if failure_type not in FAILURE_TYPES:
                    failure_type = "unknown"
            else:
                failure_type = "unknown"

            # Loop detection overrides only when no diagnosis or diagnosis is unknown
            if failure_type == "unknown" and _detect_loop(steps):
                failure_type = "loop_failure"

            # Hallucination detection overrides (only for failures)
            if status == "failure" and failure_type not in ("loop_failure",):
                if _check_hallucinated_symbol(steps, project_root):
                    failure_type = "hallucinated_symbol"

            # Keyword inference if still unknown
            if failure_type == "unknown":
                failure_type = _infer_failure_type_from_trace(
                    steps, evaluation, diagnosis
                )
                if failure_type not in FAILURE_TYPES:
                    failure_type = "unknown"

            records.append(
                FailureRecord(
                    task_id=task_id,
                    attempt=att.get("attempt", 0),
                    failure_type=failure_type,
                    failing_step=failing_step,
                    retry_strategy=strategy,
                    prompt_tokens=prompt_tokens,
                    repo_tokens=repo_tokens,
                    trajectory_length=trajectory_length,
                    step_type=step_type,
                    status=status,
                )
            )

    return records
