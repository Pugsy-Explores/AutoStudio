"""Unit tests for semantic feedback extraction and semantic iteration."""

import unittest

from editing.semantic_feedback import (
    extract_semantic_feedback,
    format_semantic_feedback_for_instruction,
    extract_previous_patch,
    patch_signature,
    summarize_patch_action,
    normalize_failure_signature,
    format_previous_attempt_for_instruction,
    derive_failure_explanation,
    format_causal_feedback_for_retry,
    format_stateful_feedback_for_retry,
    check_structural_improvement,
)


class TestExtractSemanticFeedback(unittest.TestCase):
    def test_passed_returns_empty(self):
        r = extract_semantic_feedback({"passed": True})
        self.assertTrue(r["tests_passed"])
        self.assertEqual(r["failure_summary"], "")
        self.assertEqual(r["failing_tests"], [])

    def test_failure_extracts_failing_tests(self):
        out = "FAILED tests/test_foo.py::test_bar - AssertionError: assert 1 == 2"
        r = extract_semantic_feedback({"passed": False, "stdout": out, "stderr": ""})
        self.assertFalse(r["tests_passed"])
        self.assertIn("test_bar", r["failure_summary"])
        self.assertEqual(len(r["failing_tests"]), 1)
        t = r["failing_tests"][0]
        self.assertEqual(t["name"], "tests/test_foo.py::test_bar")
        self.assertIn("assert 1 == 2", t["error"])
        self.assertEqual(t["expected"], "2")
        self.assertEqual(t["actual"], "1")

    def test_fallback_when_no_failed_line(self):
        r = extract_semantic_feedback({"passed": False, "stdout": "something broke", "stderr": ""})
        self.assertFalse(r["tests_passed"])
        self.assertGreaterEqual(len(r["failing_tests"]), 1)
        self.assertIn("unknown", r["failing_tests"][0]["name"] or "unknown")


class TestFormatForInstruction(unittest.TestCase):
    def test_includes_improvement_constraint(self):
        fb = {
            "tests_passed": False,
            "failure_summary": "x failed",
            "failing_tests": [{"name": "t1", "error": "e1", "expected": None, "actual": None}],
        }
        text = format_semantic_feedback_for_instruction(fb)
        self.assertIn("SEMANTIC_FEEDBACK", text)
        self.assertIn("Do NOT repeat previous patch", text)
        self.assertIn("Modify the implementation to fix", text)
        self.assertIn("x failed", text)
        self.assertIn("t1", text)

    def test_passed_returns_empty(self):
        text = format_semantic_feedback_for_instruction({"tests_passed": True})
        self.assertEqual(text, "")


class TestSemanticIteration(unittest.TestCase):
    def test_extract_previous_patch_text_sub(self):
        plan = {"changes": [{"file": "a.py", "patch": {"action": "text_sub", "old": "x", "new": "y"}}]}
        p = extract_previous_patch(plan)
        self.assertEqual(p["old"], "x")
        self.assertEqual(p["new"], "y")
        self.assertEqual(p["file"], "a.py")

    def test_extract_previous_patch_insert(self):
        plan = {"changes": [{"file": "a.py", "patch": {"action": "insert", "code": "return 1", "symbol": "foo"}}]}
        p = extract_previous_patch(plan)
        self.assertEqual(p["old"], "")
        self.assertEqual(p["new"], "return 1")
        self.assertEqual(p["symbol"], "foo")

    def test_patch_signature_differ(self):
        p1 = {"old": "x", "new": "y", "file": "a.py", "symbol": None}
        p2 = {"old": "x", "new": "z", "file": "a.py", "symbol": None}
        self.assertNotEqual(patch_signature(p1), patch_signature(p2))

    def test_patch_signature_same(self):
        p = {"old": "x", "new": "y", "file": "a.py", "symbol": None}
        self.assertEqual(patch_signature(p), patch_signature(p))

    def test_normalize_failure_signature(self):
        s = normalize_failure_signature("  Foo  Bar  \n  Baz  ")
        self.assertIn("foo", s)
        self.assertIn("bar", s)

    def test_check_structural_improvement_unchanged_rejects(self):
        prev = {"old": "x", "new": "y", "file": "a.py", "symbol": None}
        new_plan = {"changes": [{"file": "a.py", "patch": {"action": "text_sub", "old": "x", "new": "y"}}]}
        changed, same_target, reason = check_structural_improvement(new_plan, prev, {"file": "a.py"})
        self.assertFalse(changed)
        self.assertEqual(reason, "patch_unchanged_repeat")

    def test_check_structural_improvement_changed_passes(self):
        prev = {"old": "x", "new": "y", "file": "a.py", "symbol": None}
        new_plan = {"changes": [{"file": "a.py", "patch": {"action": "text_sub", "old": "x", "new": "z"}}]}
        changed, same_target, reason = check_structural_improvement(new_plan, prev, {"file": "a.py"})
        self.assertTrue(changed)
        self.assertTrue(same_target)
        self.assertEqual(reason, "")

    def test_format_previous_attempt(self):
        prev_patch = {"old": "x", "new": "y", "file": "a.py", "symbol": None}
        prev_failure = {"failure_summary": "assert failed", "failing_tests": []}
        text = format_previous_attempt_for_instruction(prev_patch, prev_failure)
        self.assertIn("PREVIOUS_ATTEMPT", text)
        self.assertIn("OLD:", text)
        self.assertIn("NEW:", text)
        self.assertIn("FAILURE:", text)
        self.assertIn("x", text)
        self.assertIn("y", text)
        self.assertIn("MUST meaningfully differ", text)

    def test_reject_identical_patch_on_retry(self):
        """Identical patch produces patch_unchanged_repeat; triggers retry path."""
        prev = {"old": "return 1", "new": "return 2", "file": "foo.py", "symbol": None}
        identical_plan = {"changes": [{"file": "foo.py", "patch": {"action": "text_sub", "old": "return 1", "new": "return 2"}}]}
        changed, _, reason = check_structural_improvement(identical_plan, prev, {"file": "foo.py"})
        self.assertFalse(changed)
        self.assertEqual(reason, "patch_unchanged_repeat")


class TestCausalFailureFeedback(unittest.TestCase):
    """Causal failure feedback: derive_failure_explanation, format_causal_feedback_for_retry."""

    def test_derive_failure_explanation_patch_unchanged(self):
        ctx = {"patch_validation_debug": {"reason": "patch_unchanged"}}
        out = derive_failure_explanation(ctx, patch_result={"patch_reject_reason": "patch_unchanged"})
        self.assertIn("did not modify", out)
        self.assertIn("old == new", out)

    def test_derive_failure_explanation_patch_apply_failed(self):
        out = derive_failure_explanation(
            {}, patch_result={"patch_reject_reason": "patch_apply_failed"}
        )
        self.assertIn("OLD snippet", out)
        self.assertIn("does not exist", out)

    def test_derive_failure_explanation_wrong_target_file(self):
        out = derive_failure_explanation(
            {}, patch_result={"patch_reject_reason": "wrong_target_file"}
        )
        self.assertIn("different file", out)

    def test_derive_failure_explanation_weakly_grounded(self):
        out = derive_failure_explanation(
            {}, patch_result={"patch_reject_reason": "weakly_grounded_patch"}
        )
        self.assertIn("not grounded", out)

    def test_derive_failure_explanation_test_failure(self):
        sf = {"tests_passed": False, "failure_summary": "assert 1 == 2"}
        out = derive_failure_explanation({}, semantic_feedback=sf)
        self.assertIn("Tests failed", out)
        self.assertIn("assert 1 == 2", out)

    def test_format_causal_feedback_for_retry_contains_requirement(self):
        prev = {"old": "x", "new": "y", "file": "a.py", "symbol": None}
        text = format_causal_feedback_for_retry(prev, "Your patch did not modify the file.")
        self.assertIn("PREVIOUS_ATTEMPT", text)
        self.assertIn("Failure:", text)
        self.assertIn("Your patch did not modify", text)
        self.assertIn("REQUIREMENT", text)
        self.assertIn("DIFFERENT patch", text)
        self.assertIn("resolve the above failure", text)
        self.assertIn("Do NOT repeat", text)

    def test_failure_explanation_present_in_prompt(self):
        """format_causal_feedback injects failure_explanation into retry prompt."""
        prev = {"old": "return 1", "new": "return 1", "file": "foo.py"}
        exp = "Your patch did not modify the file (old == new)."
        text = format_causal_feedback_for_retry(prev, exp)
        self.assertIn(exp, text)
        self.assertIn("old:", text)
        self.assertIn("new:", text)


class TestStatefulFailureFeedback(unittest.TestCase):
    """Stateful failure representation: failure_state, no_progress_repeat, stagnation."""

    def test_reject_repeated_patch_signature(self):
        """check_structural_improvement rejects patch whose sig is in attempted_patches."""
        sig = "a.py||x->y"
        attempted = [sig]
        new_plan = {"changes": [{"file": "a.py", "patch": {"action": "text_sub", "old": "x", "new": "y"}}]}
        changed, _, reason = check_structural_improvement(
            new_plan, None, {}, attempted_patches=attempted
        )
        self.assertFalse(changed)
        self.assertEqual(reason, "no_progress_repeat")

    def test_retry_includes_failure_state(self):
        """format_stateful_feedback_for_retry includes FAILURE_STATE and action summaries."""
        failures = ["Your patch did not modify the file."]
        attempted_actions = ["Edited code in foo.py: x -> y"]
        text = format_stateful_feedback_for_retry(failures, attempted_actions, stagnation_count=1)
        self.assertIn("FAILURE_STATE", text)
        self.assertIn("Known failures:", text)
        self.assertIn("Previous attempts", text)
        self.assertIn("Edited code in foo.py", text)
        self.assertIn("Stagnation count: 1", text)
        self.assertIn("REQUIREMENT", text)
        self.assertIn("different from previous attempts", text)
        self.assertIn("Avoid identical patches; modifying same location is allowed if needed", text)

    def test_derive_failure_explanation_no_progress_repeat(self):
        out = derive_failure_explanation(
            {}, patch_result={"patch_reject_reason": "no_progress_repeat"}
        )
        self.assertIn("repeated", out)
        self.assertIn("previously attempted", out)

    def test_action_summary_generation(self):
        """summarize_patch_action produces human-readable summaries."""
        patch_with_symbol = {"file": "a.py", "symbol": "double", "old": "return n", "new": "return n * 2"}
        out = summarize_patch_action(patch_with_symbol)
        self.assertIn("double", out)
        self.assertIn("a.py", out)
        self.assertIn("return n", out)
        self.assertIn("return n * 2", out)
        patch_no_symbol = {"file": "b.py", "symbol": "", "old": "x = 1", "new": "x = 2"}
        out2 = summarize_patch_action(patch_no_symbol)
        self.assertIn("Edited code", out2)
        self.assertIn("b.py", out2)
        self.assertIn("x = 1", out2)
        self.assertIn("x = 2", out2)

    def test_retry_prompt_uses_action_summary(self):
        """Retry prompt contains action summaries, not raw signatures."""
        actions = ["Edited halve in bench_math.py: return n -> return n // 2"]
        text = format_stateful_feedback_for_retry([], actions, stagnation_count=0)
        self.assertIn("Edited halve", text)
        self.assertIn("return n // 2", text)
        self.assertNotIn("bench_math.py||", text)

    def test_action_summary_replaces_signature_in_prompt(self):
        """Previous attempts section shows human-readable summaries."""
        actions = ["Edited code in foo.py: x = 1 -> x = 2"]
        text = format_stateful_feedback_for_retry(["fail"], actions, 0)
        self.assertIn("- Previous attempts:", text)
        self.assertIn("Edited code in foo.py", text)
        self.assertNotIn("foo.py||x", text)
