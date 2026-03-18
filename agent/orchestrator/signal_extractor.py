from typing import List, TypedDict


class EvaluationSignals(TypedDict):
    has_successful_step: bool
    has_files_modified: bool
    has_patches: bool
    actions_executed: List[str]


def extract_signals(state) -> EvaluationSignals:
    results = state.step_results or []

    return {
        "has_successful_step": any(getattr(r, "success", False) for r in results),
        "has_files_modified": any(getattr(r, "files_modified", None) for r in results),
        "has_patches": any(getattr(r, "patch_size", 0) for r in results),
        "actions_executed": [getattr(r, "action", "") for r in results],
    }

