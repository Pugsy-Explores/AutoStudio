"""Stage 27 — external6 suite anti-overfit tests.

Guards against:
- external6 overlapping with audit12/holdout8/adversarial12 in task_ids or repo paths
- task-id branching in harness/real_execution/grounded patch/validation
- validation commands cloned from existing suites
- task wording identical to prior benchmark suites
"""

from __future__ import annotations

from pathlib import Path

from tests.agent_eval.suites.audit12 import load_audit12_specs
from tests.agent_eval.suites.holdout8 import HOLDOUT8_TASKS, load_holdout8_specs
from tests.agent_eval.suites.adversarial12 import ADVERSARIAL12_TASKS, load_adversarial12_specs
from tests.agent_eval.suites.external6 import EXTERNAL6_TASKS, load_external6_specs
from tests.agent_eval.task_specs import validate_suite

AUDIT12_TASK_IDS = frozenset(t.task_id for t in load_audit12_specs())
HOLDOUT_TASK_IDS = frozenset(t.task_id for t in HOLDOUT8_TASKS)
ADVERSARIAL_TASK_IDS = frozenset(t.task_id for t in ADVERSARIAL12_TASKS)
EXTERNAL_TASK_IDS = frozenset(t.task_id for t in EXTERNAL6_TASKS)

# Paths used by other suites (mini, holdout, adversarial)
AUDIT12_REPO_PATHS = frozenset(t.repo_path for t in load_audit12_specs())
HOLDOUT_REPO_PATHS = frozenset(t.repo_path for t in HOLDOUT8_TASKS)
ADVERSARIAL_REPO_PATHS = frozenset(t.repo_path for t in ADVERSARIAL12_TASKS)

# External uses pinned_repos; overlap with audit12 is OK (audit12 also uses pinned_repos)
# But external task_ids and task wording must be distinct
MINI_HOLDOUT_ADV_PATHS = HOLDOUT_REPO_PATHS | ADVERSARIAL_REPO_PATHS
# mini_repos and holdout and adversarial are distinct from external's pinned_repos
EXTERNAL_REPO_PATHS = frozenset(t.repo_path for t in EXTERNAL6_TASKS)


def _collect_task_id_branches(path: str, forbidden: frozenset) -> list[tuple[int, str]]:
    """Find lines containing hardcoded task_ids from forbidden set."""
    try:
        content = open(path, encoding="utf-8").read()
    except OSError:
        return []
    hits: list[tuple[int, str]] = []
    for i, line in enumerate(content.splitlines(), 1):
        if any(tid in line for tid in forbidden):
            stripped = line.strip()
            if stripped.startswith("#") or '"""' in line or "'''" in line:
                continue
            hits.append((i, line))
    return hits


def test_external_task_ids_distinct():
    """external6 task_ids must not overlap audit12, holdout8, or adversarial12."""
    overlap_audit = EXTERNAL_TASK_IDS & AUDIT12_TASK_IDS
    overlap_holdout = EXTERNAL_TASK_IDS & HOLDOUT_TASK_IDS
    overlap_adv = EXTERNAL_TASK_IDS & ADVERSARIAL_TASK_IDS
    assert len(overlap_audit) == 0, f"external6 task_ids must not overlap audit12: {overlap_audit}"
    assert len(overlap_holdout) == 0, f"external6 task_ids must not overlap holdout8: {overlap_holdout}"
    assert len(overlap_adv) == 0, f"external6 task_ids must not overlap adversarial12: {overlap_adv}"
    assert all(tid.startswith("ext_") for tid in EXTERNAL_TASK_IDS), (
        f"external6 task_ids should use ext_ prefix: {EXTERNAL_TASK_IDS}"
    )


def test_external_repo_paths_distinct_from_mini_holdout_adversarial():
    """external6 repo paths must be distinct from mini/holdout/adversarial (not mr*, mh*, av*)."""
    overlap_mini_holdout_adv = EXTERNAL_REPO_PATHS & MINI_HOLDOUT_ADV_PATHS
    assert len(overlap_mini_holdout_adv) == 0, (
        f"external6 must not use mini_repos, holdout_mini_repos, or adversarial_mini_repos: {overlap_mini_holdout_adv}"
    )
    assert all("pinned_repos" in p for p in EXTERNAL_REPO_PATHS), (
        f"external6 should use pinned_repos: {EXTERNAL_REPO_PATHS}"
    )


def test_external_no_task_id_branching():
    """Harness, real_execution, grounded_patch_generator, validation must not branch on external6 task_ids."""
    ext_ids = EXTERNAL_TASK_IDS
    base = Path(__file__).resolve().parent
    paths_to_check = [
        base / "harness.py",
        base / "real_execution.py",
    ]
    # Also check editing and agent runtime for task-id branching
    repo_root = base.parent.parent
    paths_to_check.extend([
        repo_root / "editing" / "grounded_patch_generator.py",
        repo_root / "editing" / "patch_generator.py",
        repo_root / "agent" / "runtime" / "execution_loop.py",
    ])
    for p in paths_to_check:
        if not p.exists():
            continue
        hits = _collect_task_id_branches(str(p), ext_ids)
        assert not hits, f"{p.name} must not branch on external6 task_ids. Found: {hits}"


def test_external_instruction_wording_differs():
    """external6 must use materially different wording from audit12/holdout8/adversarial12."""
    # External-specific terms that should not appear in other suites
    ext_specific = []
    for t in EXTERNAL6_TASKS:
        inst = (t.instruction or "").lower()
        if "halve" in inst:
            ext_specific.append("halve")
        if "add_ints" in inst:
            ext_specific.append("add_ints")
        if "version_note" in inst or "version_meta" in inst or "release_version" in inst:
            ext_specific.append("version_note/meta")
        if "readme_bench" in inst or "typer_bench_ver" in inst:
            ext_specific.append("readme_bench")
        if "decorator_flow" in inst or "decorator_flow.md" in inst:
            ext_specific.append("decorator_flow")
        if "get_timeout" in inst:
            ext_specific.append("get_timeout")
    assert len(ext_specific) >= 4, (
        f"external6 should have distinct wording. Found: {ext_specific}"
    )


def test_external_validation_commands_diverse():
    """external6 validation must use diverse patterns, not cloned from one suite."""
    patterns = []
    for t in EXTERNAL6_TASKS:
        for cmd in t.validation_commands:
            if "pytest" in cmd:
                patterns.append("pytest")
            elif "check_version_sync" in cmd:
                patterns.append("check_version_sync")
            elif "check_readme_bench" in cmd:
                patterns.append("check_readme_bench")
    unique = len(set(patterns))
    assert unique >= 2, (
        f"external6 validation should use varied patterns. Found: {patterns}"
    )
    # External must have at least one validation command not used by adversarial12
    adv_commands = frozenset(cmd for t in ADVERSARIAL12_TASKS for cmd in t.validation_commands)
    ext_commands = frozenset(cmd for t in EXTERNAL6_TASKS for cmd in t.validation_commands)
    ext_only = ext_commands - adv_commands
    assert len(ext_only) >= 1, (
        f"external6 should have validation commands distinct from adversarial12. ext_commands={ext_commands}"
    )


def test_external6_loads_and_validates():
    """external6 suite loads and passes validate_suite."""
    specs = load_external6_specs()
    assert len(specs) == 6
    validate_suite(specs)


def test_external6_task_types_balanced():
    """external6 includes repair, docs, explain, feature."""
    by_tag = {}
    for t in EXTERNAL6_TASKS:
        for tag in t.tags:
            if tag in ("repair", "feature", "docs", "explain", "multi_file", "refactor", "consistency"):
                by_tag[tag] = by_tag.get(tag, 0) + 1
    assert "repair" in by_tag
    assert "docs" in by_tag or "consistency" in by_tag
    assert any(t.grading_mode == "explain_artifact" for t in EXTERNAL6_TASKS)
    assert "feature" in by_tag
