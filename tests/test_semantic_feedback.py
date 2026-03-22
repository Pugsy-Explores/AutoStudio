"""Unit tests for semantic feedback extraction and semantic iteration."""

import unittest

from editing.semantic_feedback import (
    extract_semantic_feedback,
    format_semantic_feedback_for_instruction,
    extract_previous_patch,
    patch_signature,
    normalize_failure_signature,
    format_previous_attempt_for_instruction,
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
        self.assertEqual(reason, "patch_unchanged")

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
