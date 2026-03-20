"""Stage 23 — patch effectiveness guards, generator grounding, semantic RCA."""

from __future__ import annotations

from editing.patch_effectiveness import assess_text_sub, module_append_is_meaningful
from editing.patch_executor import execute_patch
from tests.agent_eval.semantic_rca import classify_wrong_patch_root_cause


def test_noop_text_sub_rejected(tmp_path):
    p = tmp_path / "noop.py"
    p.write_text("hello\n", encoding="utf-8")
    r = execute_patch(
        {
            "changes": [
                {"file": "noop.py", "patch": {"action": "text_sub", "old": "hello", "new": "hello"}},
            ]
        },
        project_root=str(tmp_path),
    )
    assert r.get("success") is False
    assert r.get("patch_reject_reason") == "no_effect_change"


def test_old_equals_new_rejected(tmp_path):
    p = tmp_path / "t_noop.py"
    src = "x = 1\n"
    p.write_text(src, encoding="utf-8")
    line = src.strip()
    r = execute_patch(
        {"changes": [{"file": "t_noop.py", "patch": {"action": "text_sub", "old": line, "new": line}}]},
        project_root=str(tmp_path),
    )
    assert r.get("success") is False
    assert r.get("patch_reject_reason") == "no_effect_change"


def test_module_append_no_meaningful_content_rejected(tmp_path):
    p = tmp_path / "m.py"
    p.write_text("x = 1\n", encoding="utf-8")
    r = execute_patch(
        {
            "changes": [
                {
                    "file": str(p),
                    "patch": {
                        "symbol": "",
                        "action": "insert",
                        "target_node": "module_append",
                        "code": "\n# only a comment\n",
                    },
                }
            ]
        },
        project_root=str(tmp_path),
    )
    assert r.get("success") is False
    assert r.get("patch_reject_reason") in ("no_meaningful_diff", "unchanged_target_region")


def test_grounded_text_sub_accepted(tmp_path):
    p = tmp_path / "g.py"
    p.write_text("def f():\n    return a * b\n", encoding="utf-8")
    r = execute_patch(
        {
            "changes": [
                {
                    "file": "g.py",
                    "patch": {"action": "text_sub", "old": "return a * b", "new": "return a / b"},
                }
            ]
        },
        project_root=str(tmp_path),
    )
    assert r.get("success") is True
    assert p.read_text(encoding="utf-8") == "def f():\n    return a / b\n"
    pe = r.get("patch_effectiveness") or {}
    assert pe.get("patch_effective_change") is True
    assert (pe.get("meaningful_diff_line_count") or 0) >= 1


def test_before_after_snippet_unchanged_region():
    ok, reason, new_src, extra = assess_text_sub(
        source_before="def x():\n    return 1\n",
        old="return 1",
        new="return 1",
    )
    assert ok is False
    assert reason == "no_effect_change"
    ok2, reason2, _, _ = assess_text_sub(
        source_before="a\nb\nc\n",
        old="x",
        new="y",
    )
    assert ok2 is True or reason2 is None  # old not in source — executor rejects later


def test_module_append_meaningful_helper():
    ok, _ = module_append_is_meaningful("\ndef new_fn():\n    return 1\n", "x=1\n")
    assert ok is True
    ok2, r2 = module_append_is_meaningful("\n# c\n", "x=1\n")
    assert ok2 is False


def test_semantic_rca_classifier_no_effect_and_weakly_grounded():
    cause = classify_wrong_patch_root_cause(
        success=False,
        structural_success=False,
        validation_passed=False,
        failure_bucket="edit_grounding_failure",
        loop_snapshot={
            "edit_telemetry": {
                "patch_reject_reason": "no_effect_change",
                "patch_apply_ok": False,
            }
        },
        validation_logs=[],
        instruction="fix",
    )
    assert cause == "no_effect_change"

    cause2 = classify_wrong_patch_root_cause(
        success=False,
        structural_success=False,
        validation_passed=False,
        failure_bucket=None,
        loop_snapshot={
            "edit_telemetry": {
                "patch_reject_reason": "weakly_grounded_patch",
                "edit_failure_reason": "weakly_grounded_patch",
                "patch_apply_ok": False,
            }
        },
        validation_logs=[],
        instruction="fix",
    )
    assert cause2 == "weakly_grounded_patch"


def test_no_task_id_branching_in_patch_generator():
    from editing import patch_generator as pg

    src = open(pg.__file__, encoding="utf-8").read()
    assert "adv_repair" not in src
    assert "adversarial12" not in src

