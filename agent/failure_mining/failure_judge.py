"""Optional LLM-based failure type labeling via model client (small model)."""

import json
import logging

from agent.failure_mining.failure_extractor import FailureRecord
from agent.failure_mining.failure_taxonomy import FAILURE_TYPES

logger = logging.getLogger(__name__)

JUDGE_SYSTEM = """You classify agent failure types from trajectory summaries.
Respond with a single JSON object: {"failure_type": "..."}.
Choose exactly one from: retrieval_miss, wrong_file_localization, incorrect_patch, syntax_error_patch, test_failure, tool_error, timeout, hallucinated_api, premature_completion, hallucinated_symbol, loop_failure.
If unclear, use "unknown"."""


def _build_judge_prompt(record: FailureRecord, goal: str = "") -> str:
    """Build prompt for failure judge from record."""
    return f"""Goal: {goal}

Failure record:
- failing_step: {record.failing_step[:300]}
- step_type: {record.step_type}
- trajectory_length: {record.trajectory_length}
- status: {record.status}

Classify the failure_type. Respond with JSON only."""


def label_failure(
    record: FailureRecord,
    goal: str = "",
) -> str:
    """
    Use small model to classify failure_type when heuristic extraction yielded unknown.
    Returns the classified failure_type (from FAILURE_TYPES or "unknown").
    """
    from agent.models.model_client import call_small_model

    prompt = _build_judge_prompt(record, goal)
    try:
        out = call_small_model(
            prompt,
            task_name="failure_judge",
            system_prompt=JUDGE_SYSTEM,
            max_tokens=64,
        )
        out = (out or "").strip()
        idx = out.find("{")
        if idx >= 0:
            end = out.rfind("}")
            if end > idx:
                obj = json.loads(out[idx : end + 1])
                ft = str(obj.get("failure_type", "unknown")).strip().lower()
                if ft in FAILURE_TYPES:
                    return ft
    except Exception as e:
        logger.warning("[failure_judge] label failed: %s", e)
    return "unknown"


def relabel_unknown_records(
    records: list[FailureRecord],
    trajectory_goals: dict[str, str] | None = None,
) -> list[FailureRecord]:
    """
    For records with failure_type="unknown", call LLM to re-label.
    trajectory_goals: optional dict task_id -> goal for context.
    Returns new list with updated failure_type where applicable.
    """
    goals = trajectory_goals or {}
    out: list[FailureRecord] = []
    for r in records:
        if r.failure_type != "unknown":
            out.append(r)
            continue
        goal = goals.get(r.task_id, "")
        new_ft = label_failure(r, goal)
        out.append(
            FailureRecord(
                task_id=r.task_id,
                attempt=r.attempt,
                failure_type=new_ft,
                failing_step=r.failing_step,
                retry_strategy=r.retry_strategy,
                prompt_tokens=r.prompt_tokens,
                repo_tokens=r.repo_tokens,
                trajectory_length=r.trajectory_length,
                step_type=r.step_type,
                status=r.status,
            )
        )
    return out
