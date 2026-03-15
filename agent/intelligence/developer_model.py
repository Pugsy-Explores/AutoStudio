"""Developer profile: learns preferences from accepted solutions and edit patterns."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

AGENT_MEMORY_DIR = ".agent_memory"
PROFILE_FILENAME = "developer_profile.json"

DEFAULT_PROFILE = {
    "preferred_test_framework": None,
    "logging_style": None,
    "code_style": {},
    "observed_patterns": [],
    "last_updated": None,
}


def _profile_path(project_root: str | None = None) -> Path:
    """Return path to .agent_memory/developer_profile.json."""
    root = Path(project_root or ".").resolve()
    return root / AGENT_MEMORY_DIR / PROFILE_FILENAME


def load_profile(project_root: str | None = None) -> dict:
    """Load developer profile. Returns default profile if not found."""
    path = _profile_path(project_root)
    if not path.exists():
        return dict(DEFAULT_PROFILE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return {**DEFAULT_PROFILE, **data}
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_PROFILE)


def save_profile(profile: dict, project_root: str | None = None) -> None:
    """Save developer profile to JSON."""
    import time

    path = _profile_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = dict(profile)
    profile["last_updated"] = time.time()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, default=str)
    logger.debug("[developer_model] profile saved")


def update_from_solution(
    solution: dict,
    project_root: str | None = None,
) -> None:
    """
    Update developer profile from a successful solution.
    Extracts patterns from files_modified, patch_summary.
    """
    profile = load_profile(project_root)
    observed = profile.get("observed_patterns", [])

    patch_summary = (solution.get("patch_summary") or "").strip()
    files_modified = solution.get("files_modified") or []

    if patch_summary:
        observed.append({
            "type": "patch_pattern",
            "summary": patch_summary[:200],
        })
    if files_modified:
        for fpath in files_modified[:5]:
            if "test" in fpath.lower() or "pytest" in fpath or "_test" in fpath:
                profile["preferred_test_framework"] = profile.get(
                    "preferred_test_framework"
                ) or "pytest"
                break

    # Cap observed patterns to avoid unbounded growth
    profile["observed_patterns"] = observed[-50:]
    save_profile(profile, project_root)
