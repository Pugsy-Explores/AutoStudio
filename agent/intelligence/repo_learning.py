"""Repo knowledge: accumulates frequent bug areas, refactor patterns, architecture constraints."""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

AGENT_MEMORY_DIR = ".agent_memory"
KNOWLEDGE_FILENAME = "repo_knowledge.json"

DEFAULT_KNOWLEDGE = {
    "frequent_bug_areas": {},
    "common_refactor_patterns": [],
    "architecture_constraints": [],
    "last_updated": None,
}


def _knowledge_path(project_root: str | None = None) -> Path:
    """Return path to .agent_memory/repo_knowledge.json."""
    root = Path(project_root or ".").resolve()
    return root / AGENT_MEMORY_DIR / KNOWLEDGE_FILENAME


def load_knowledge(project_root: str | None = None) -> dict:
    """Load repo knowledge. Returns default if not found."""
    path = _knowledge_path(project_root)
    if not path.exists():
        return dict(DEFAULT_KNOWLEDGE)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return {**DEFAULT_KNOWLEDGE, **data}
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_KNOWLEDGE)


def save_knowledge(knowledge: dict, project_root: str | None = None) -> None:
    """Save repo knowledge to JSON."""
    import time

    path = _knowledge_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    knowledge = dict(knowledge)
    knowledge["last_updated"] = time.time()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(knowledge, f, indent=2, default=str)
    logger.debug("[repo_learning] knowledge saved")


def update_from_solution(
    solution: dict,
    project_root: str | None = None,
) -> None:
    """
    Update repo knowledge from a successful solution.
    Increments frequent_bug_areas for modified files, adds refactor patterns.
    """
    knowledge = load_knowledge(project_root)
    bug_areas = knowledge.get("frequent_bug_areas", {})
    patterns = knowledge.get("common_refactor_patterns", [])

    files_modified = solution.get("files_modified") or []
    for fpath in files_modified:
        bug_areas[fpath] = bug_areas.get(fpath, 0) + 1

    patch_summary = (solution.get("patch_summary") or "").strip()
    if patch_summary:
        patterns.append({"summary": patch_summary[:200]})
    knowledge["frequent_bug_areas"] = bug_areas
    knowledge["common_refactor_patterns"] = patterns[-100:]
    save_knowledge(knowledge, project_root)
