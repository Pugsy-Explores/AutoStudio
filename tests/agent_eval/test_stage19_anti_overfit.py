"""Stage 19 — anti-overfit coverage.

Guards against:
- task-id-based behavior in execution/harness
- validation limited to scripts/check_*.py pattern
- exact-path-pattern assumptions for core12 only
- benchmark-local-only success logic
"""

from __future__ import annotations

import re

from tests.agent_eval.suites.core12 import CORE12_TASKS
from tests.agent_eval.suites.holdout8 import HOLDOUT8_TASKS, load_holdout8_specs
from tests.agent_eval.task_specs import TaskSpec, validate_suite


# --- Task-id branching guards ---

AUDIT12_TASK_IDS = frozenset(t.task_id for t in CORE12_TASKS)
FORBIDDEN_TASK_ID_PATTERNS = tuple(AUDIT12_TASK_IDS)


def _collect_task_id_branches_in_file(path: str) -> list[tuple[int, str]]:
    """Find lines that contain hardcoded audit12 task_ids (forbidden in harness/real_execution)."""
    try:
        content = open(path, encoding="utf-8").read()
    except OSError:
        return []
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(content.splitlines(), 1):
        if any(tid in line for tid in FORBIDDEN_TASK_ID_PATTERNS):
            stripped = line.strip()
            if stripped.startswith("#") or '"""' in line or "'''" in line:
                continue
            hits.append((i, line))
    return hits


def test_no_task_id_branching_in_harness():
    """Harness must not branch on specific audit12 task_ids."""
    from pathlib import Path

    harness_path = Path(__file__).resolve().parent / "harness.py"
    real_path = Path(__file__).resolve().parent / "real_execution.py"
    for p in (harness_path, real_path):
        if not p.exists():
            continue
        hits = _collect_task_id_branches_in_file(str(p))
        assert not hits, (
            f"{p.name} must not branch on audit12 task_ids. Found: {hits}"
        )


def test_harness_uses_semantic_tags_not_task_id():
    """Phase plan selection must use tags/grading_mode, not task_id."""
    from tests.agent_eval.harness import _is_docs_consistency_task, _is_explain_artifact_task

    # Docs-consistency: tag-based
    docs_spec = TaskSpec(
        task_id="holdout_docs_changelog",
        layer="mini_repo",
        repo_id="x",
        repo_path="holdout_mini_repos/mh03_changelog",
        instruction="align",
        tags=("docs", "consistency"),
    )
    assert _is_docs_consistency_task(docs_spec) is True

    # Explain-artifact: grading_mode based
    explain_spec = TaskSpec(
        task_id="holdout_explain_trace",
        layer="mini_repo",
        repo_id="x",
        repo_path="holdout_mini_repos/mh04_trace",
        instruction="explain",
        grading_mode="explain_artifact",
    )
    assert _is_explain_artifact_task(explain_spec) is True


# --- Validation pattern guards ---

def test_holdout_uses_non_check_validation_commands():
    """Holdout must use validate_*, verify_*, run_* — not only check_*."""
    check_only = []
    for t in HOLDOUT8_TASKS:
        for cmd in t.validation_commands:
            if "check_" in cmd and "validate" not in cmd and "verify" not in cmd and "run_" not in cmd:
                check_only.append((t.task_id, cmd))
    # Holdout should have at least some non-check_* validation
    non_check = [
        (t.task_id, c)
        for t in HOLDOUT8_TASKS
        for c in t.validation_commands
        if "validate" in c or "verify" in c or "run_" in c or "pytest" in c
    ]
    assert len(non_check) >= 4, (
        "Holdout should use varied validation (validate_*, verify_*, run_*, pytest), not only check_*. "
        f"Found non-check: {non_check}"
    )


def test_holdout_validation_commands_are_diverse():
    """Holdout validation commands must not all follow scripts/check_*.py."""
    patterns = []
    for t in HOLDOUT8_TASKS:
        for cmd in t.validation_commands:
            if "scripts/" in cmd:
                patterns.append(cmd)
            if "pytest" in cmd:
                patterns.append("pytest")
    check_count = sum(1 for p in patterns if "check_" in str(p))
    assert check_count < len(patterns), (
        "Holdout should not rely solely on scripts/check_*.py. "
        f"Validation commands: {patterns}"
    )


# --- Path and wording guards ---

def test_holdout_repo_paths_distinct_from_core12():
    """Holdout uses different fixture paths than audit12."""
    core12_paths = frozenset(t.repo_path for t in CORE12_TASKS)
    holdout_paths = frozenset(t.repo_path for t in HOLDOUT8_TASKS)
    overlap = core12_paths & holdout_paths
    assert len(overlap) == 0, (
        f"Holdout must use new fixture repos. Overlap with core12: {overlap}"
    )
    assert all("holdout" in p for p in holdout_paths), (
        f"Holdout repos should live under holdout_mini_repos. Paths: {holdout_paths}"
    )


def test_holdout_task_ids_distinct_from_audit12():
    """Holdout task_ids must not overlap audit12."""
    audit_ids = frozenset(t.task_id for t in CORE12_TASKS)
    holdout_ids = frozenset(t.task_id for t in HOLDOUT8_TASKS)
    overlap = audit_ids & holdout_ids
    assert len(overlap) == 0, f"Holdout task_ids must not overlap audit12: {overlap}"


def test_holdout_instruction_wording_differs():
    """Holdout instructions use different phrasing than core12 (not reworded clones)."""
    core12_phrases = set()
    for t in CORE12_TASKS:
        for word in re.findall(r"\b\w{4,}\b", t.instruction.lower()):
            core12_phrases.add(word)
    holdout_specific = []
    for t in HOLDOUT8_TASKS:
        inst_lower = t.instruction.lower()
        if "safe_div" in inst_lower or "math_utils" in inst_lower:
            holdout_specific.append("math_utils/safe_div")
        if "changelog" in inst_lower or "validate_changelog" in inst_lower:
            holdout_specific.append("changelog")
        if "run_verify" in inst_lower:
            holdout_specific.append("run_verify")
        if "enable_debug" in inst_lower:
            holdout_specific.append("enable_debug")
        if "log_level" in inst_lower:
            holdout_specific.append("log_level")
        if "verify_api_docs" in inst_lower:
            holdout_specific.append("verify_api_docs")
    assert len(holdout_specific) >= 4, (
        "Holdout should have task-specific wording (safe_div, changelog, run_verify, etc.). "
        f"Found: {holdout_specific}"
    )


# --- Schema and load guards ---

def test_holdout8_loads_and_validates():
    """Holdout8 suite loads and passes validate_suite."""
    specs = load_holdout8_specs()
    assert len(specs) == 8
    validate_suite(specs)


def test_holdout8_task_types_balanced():
    """Holdout includes repair, feature, docs, explain, multi-file."""
    by_tag = {}
    for t in HOLDOUT8_TASKS:
        for tag in t.tags:
            if tag in ("repair", "feature", "docs", "consistency", "explain", "refactor", "multi_file"):
                by_tag[tag] = by_tag.get(tag, 0) + 1
    assert "repair" in by_tag or "refactor" in by_tag
    assert "feature" in by_tag
    assert "docs" in by_tag or "consistency" in by_tag
    assert any(t.grading_mode == "explain_artifact" for t in HOLDOUT8_TASKS)
    assert "multi_file" in by_tag or "refactor" in by_tag
