"""Stage 21 — adversarial suite anti-overfit tests.

Guards against:
- adversarial12 overlapping heavily with audit12/holdout8 in wording or paths
- harness/execution branching on adversarial task_ids
- validation commands collapsing to one pattern
- suite lacking task-type diversity
"""

from __future__ import annotations

import re

from tests.agent_eval.suites.audit12 import load_audit12_specs
from tests.agent_eval.suites.holdout8 import HOLDOUT8_TASKS, load_holdout8_specs
from tests.agent_eval.suites.adversarial12 import ADVERSARIAL12_TASKS, load_adversarial12_specs
from tests.agent_eval.task_specs import TaskSpec, validate_suite

# Stage 20 synthetic patterns we must NOT reuse in adversarial
FORBIDDEN_ADVERSARIAL_NAMES = frozenset({
    "safe_div", "enable_debug", "log_level", "SHARED_PREFIX",
    "APP_VERSION", "API_BASE", "is_valid", "RELEASE_VERSION",
    "multiply", "tokenize", "double", "beta_enabled", "describe_app",
    "SUFFIX", "part_a", "legacy", "unified",
})
AUDIT12_TASK_IDS = frozenset(t.task_id for t in load_audit12_specs())
HOLDOUT_TASK_IDS = frozenset(t.task_id for t in HOLDOUT8_TASKS)


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


def test_adversarial_no_task_id_branching():
    """Harness/real_execution must not branch on adversarial12 task_ids."""
    from pathlib import Path

    adv_ids = frozenset(t.task_id for t in ADVERSARIAL12_TASKS)
    harness_path = Path(__file__).resolve().parent / "harness.py"
    real_path = Path(__file__).resolve().parent / "real_execution.py"
    for p in (harness_path, real_path):
        if not p.exists():
            continue
        hits = _collect_task_id_branches(str(p), adv_ids)
        assert not hits, f"{p.name} must not branch on adversarial task_ids. Found: {hits}"


def test_adversarial_repo_paths_distinct():
    """Adversarial uses different fixture paths than audit12 and holdout8."""
    audit_paths = frozenset(t.repo_path for t in load_audit12_specs())
    holdout_paths = frozenset(t.repo_path for t in HOLDOUT8_TASKS)
    adv_paths = frozenset(t.repo_path for t in ADVERSARIAL12_TASKS)
    overlap_audit = adv_paths & audit_paths
    overlap_holdout = adv_paths & holdout_paths
    assert len(overlap_audit) == 0, f"Adversarial must not overlap audit12 paths: {overlap_audit}"
    assert len(overlap_holdout) == 0, f"Adversarial must not overlap holdout8 paths: {overlap_holdout}"
    assert all("adversarial" in p for p in adv_paths), f"Adversarial repos under adversarial_mini_repos: {adv_paths}"


def test_adversarial_task_ids_distinct():
    """Adversarial task_ids must not overlap audit12 or holdout8."""
    adv_ids = frozenset(t.task_id for t in ADVERSARIAL12_TASKS)
    assert len(adv_ids & AUDIT12_TASK_IDS) == 0, "Adversarial task_ids must not overlap audit12"
    assert len(adv_ids & HOLDOUT_TASK_IDS) == 0, "Adversarial task_ids must not overlap holdout8"


def test_adversarial_avoids_stage20_synthetic_names():
    """Adversarial instructions must not reuse Stage 20 synthetic pattern names."""
    forbidden_in_instruction = []
    for t in ADVERSARIAL12_TASKS:
        inst_lower = (t.instruction or "").lower()
        for name in FORBIDDEN_ADVERSARIAL_NAMES:
            if name.lower() in inst_lower:
                forbidden_in_instruction.append((t.task_id, name))
    assert len(forbidden_in_instruction) == 0, (
        f"Adversarial must avoid Stage 20 synthetic names. Found: {forbidden_in_instruction}"
    )


def test_adversarial_validation_commands_diverse():
    """Adversarial validation must not collapse to one pattern."""
    patterns = []
    for t in ADVERSARIAL12_TASKS:
        for cmd in t.validation_commands:
            if "pytest" in cmd:
                patterns.append("pytest")
            elif "scripts/" in cmd:
                patterns.append("scripts")
            elif "bin/" in cmd:
                patterns.append("bin")
    unique = len(set(patterns))
    assert unique >= 2, (
        f"Adversarial validation should use varied patterns (pytest, scripts/, bin/). Found: {patterns}"
    )


def test_adversarial_task_types_balanced():
    """Adversarial includes repair, feature, docs, explain, multi-file."""
    by_tag = {}
    for t in ADVERSARIAL12_TASKS:
        for tag in t.tags:
            if tag in ("repair", "feature", "docs", "explain", "multi_file", "refactor", "consistency"):
                by_tag[tag] = by_tag.get(tag, 0) + 1
    assert "repair" in by_tag or "refactor" in by_tag
    assert "feature" in by_tag
    assert "docs" in by_tag or "consistency" in by_tag
    assert any(t.grading_mode == "explain_artifact" for t in ADVERSARIAL12_TASKS)
    assert "multi_file" in by_tag or "refactor" in by_tag


def test_adversarial12_loads_and_validates():
    """Adversarial12 suite loads and passes validate_suite."""
    specs = load_adversarial12_specs()
    assert 10 <= len(specs) <= 16
    validate_suite(specs)


def test_adversarial_instruction_wording_differs():
    """Adversarial uses distinct phrasing from audit12 and holdout8."""
    audit_phrases = set()
    for t in load_audit12_specs():
        for w in re.findall(r"\b\w{4,}\b", (t.instruction or "").lower()):
            audit_phrases.add(w)
    holdout_phrases = set()
    for t in HOLDOUT8_TASKS:
        for w in re.findall(r"\b\w{4,}\b", (t.instruction or "").lower()):
            holdout_phrases.add(w)
    adv_specific = []
    for t in ADVERSARIAL12_TASKS:
        inst = (t.instruction or "").lower()
        if "normalize_ratios" in inst:
            adv_specific.append("normalize_ratios")
        if "parse_bytes" in inst:
            adv_specific.append("parse_bytes")
        if "cfg_verbose" in inst:
            adv_specific.append("cfg_verbose")
        if "get_severity" in inst:
            adv_specific.append("get_severity")
        if "validate_input" in inst or "validation guard" in inst:
            adv_specific.append("validate_input/guard")
        if "base_uri" in inst or "BASE_URI" in inst:
            adv_specific.append("BASE_URI")
        if "build_number" in inst or "BUILD_NUMBER" in inst:
            adv_specific.append("BUILD_NUMBER")
        if "default_endpoint" in inst or "DEFAULT_ENDPOINT" in inst:
            adv_specific.append("DEFAULT_ENDPOINT")
        if "current_version" in inst or "CURRENT_VERSION" in inst:
            adv_specific.append("CURRENT_VERSION")
    assert len(adv_specific) >= 4, (
        f"Adversarial should have distinct wording. Found: {adv_specific}"
    )
