"""Unit tests for syntax validation layer."""

import unittest

from editing.syntax_validation import (
    apply_patch_in_memory,
    validate_syntax,
    validate_syntax_plan,
)
from editing.patch_verification import verify_patch


class TestRejectInvalidPythonSyntax(unittest.TestCase):
    """test_reject_invalid_python_syntax: malformed code -> rejected."""

    def test_reject_invalid_python_syntax(self):
        # text_sub that produces invalid Python
        result = validate_syntax(
            proposal={
                "file": "src/foo.py",
                "patch": {"action": "text_sub", "old": "def foo():", "new": "def foo(:"},
            },
            full_file_content="def foo():\n    return 1",
            project_root="/proj",
        )
        self.assertFalse(result["valid"])
        self.assertIsNotNone(result["error"])
        self.assertIn("syntax", result["error"].lower())

    def test_reject_broken_brace(self):
        result = validate_syntax(
            proposal={
                "file": "x.py",
                "patch": {"action": "text_sub", "old": "x = 1", "new": "x = ["},
            },
            full_file_content="x = 1",
            project_root="/proj",
        )
        self.assertFalse(result["valid"])
        self.assertIn("error", result)


class TestAcceptValidPythonPatch(unittest.TestCase):
    """test_accept_valid_python_patch: valid code -> passes."""

    def test_accept_valid_python_patch(self):
        result = validate_syntax(
            proposal={
                "file": "src/foo.py",
                "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 2"},
            },
            full_file_content="x = 1\ny = 2",
            project_root="/proj",
        )
        self.assertTrue(result["valid"])
        self.assertIsNone(result["error"])

    def test_accept_valid_insert(self):
        result = validate_syntax(
            proposal={
                "file": "src/foo.py",
                "patch": {
                    "action": "insert",
                    "code": "    return 42",
                    "target_node": "function_body_start",
                    "symbol": "foo",
                },
            },
            full_file_content="def foo():\n    pass",
            project_root="/proj",
        )
        self.assertTrue(result["valid"])


class TestSyntaxCheckedBeforeVerification(unittest.TestCase):
    """test_syntax_checked_before_verification: patch that passes verification but fails syntax."""

    def test_syntax_catches_invalid_before_verification_would_pass(self):
        # This patch would PASS verify_patch (has_effect, correct_file, is_local)
        # but would FAIL syntax (produces invalid Python)
        proposal = {
            "file": "src/foo.py",
            "patch": {"action": "text_sub", "old": "pass", "new": "pass )"},
        }
        content = "def foo():\n    pass"
        binding = {"file": "src/foo.py"}

        # Verification would pass (old exists, old!=new, correct file)
        verify_result = verify_patch(
            proposal=proposal,
            full_file_content=content,
            instruction="change",
            binding=binding,
            project_root="/proj",
        )
        self.assertTrue(verify_result["valid"], "verification passes")

        # Syntax validation rejects it
        syntax_result = validate_syntax(proposal, content, "/proj")
        self.assertFalse(syntax_result["valid"], "syntax validation rejects")
        self.assertIsNotNone(syntax_result["error"])


class TestPatchApplyFailed(unittest.TestCase):
    """When patch cannot be applied -> valid=False, error_type=patch_apply_failed."""

    def test_reject_patch_apply_failed(self):
        result = validate_syntax(
            proposal={"file": "a.py", "patch": {"action": "text_sub", "old": "z", "new": "y"}},
            full_file_content="x",
            project_root="/proj",
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["error_type"], "patch_apply_failed")
        self.assertEqual(result["error"], "patch_apply_failed")


class TestNonPythonExplicitSkip(unittest.TestCase):
    """Non-Python files: skipped=True, language=non_python."""

    def test_non_python_skipped_explicit(self):
        result = validate_syntax(
            proposal={"file": "readme.md", "patch": {"action": "text_sub", "old": "x", "new": "y"}},
            full_file_content="x",
            project_root="/proj",
        )
        self.assertTrue(result["valid"])
        self.assertTrue(result.get("skipped"))
        self.assertEqual(result.get("language"), "non_python")


class TestApplyPatchInMemory(unittest.TestCase):
    """Tests for apply_patch_in_memory."""

    def test_text_sub(self):
        out = apply_patch_in_memory(
            proposal={"file": "a.py", "patch": {"action": "text_sub", "old": "x", "new": "y"}},
            full_file_content="x",
        )
        self.assertEqual(out, "y")

    def test_text_sub_old_not_found_returns_none(self):
        out = apply_patch_in_memory(
            proposal={"file": "a.py", "patch": {"action": "text_sub", "old": "z", "new": "y"}},
            full_file_content="x",
        )
        self.assertIsNone(out)


class TestValidateSyntaxPlan(unittest.TestCase):
    """Tests for validate_syntax_plan."""

    def test_plan_rejects_invalid(self):
        from pathlib import Path
        root = "/proj"
        p = Path(root).resolve() / "src" / "foo.py"
        snapshot = {p: "def foo():\n    pass"}
        patch_plan = {
            "changes": [
                {"file": "src/foo.py", "patch": {"action": "text_sub", "old": "pass", "new": "pass )"}},
            ]
        }
        ok, result = validate_syntax_plan(patch_plan, snapshot, root)
        self.assertFalse(ok)
        self.assertIsNotNone(result)
        self.assertFalse(result["valid"])

    def test_plan_accepts_valid(self):
        from pathlib import Path
        root = "/proj"
        p = Path(root).resolve() / "src" / "foo.py"
        snapshot = {p: "x = 1"}
        patch_plan = {
            "changes": [
                {"file": "src/foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 2"}},
            ]
        }
        ok, result = validate_syntax_plan(patch_plan, snapshot, root)
        self.assertTrue(ok)
        self.assertIsNone(result)

    def test_plan_sequential_per_file(self):
        """Multiple patches to same file: applied sequentially, syntax checked on accumulated result."""
        from pathlib import Path
        root = "/proj"
        p = Path(root).resolve() / "src" / "foo.py"
        # patch1: x=1 -> x=2; patch2: x=2 -> x=3. Individually valid, collectively valid.
        snapshot = {p: "x = 1"}
        patch_plan = {
            "changes": [
                {"file": "src/foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 2"}},
                {"file": "src/foo.py", "patch": {"action": "text_sub", "old": "x = 2", "new": "x = 3"}},
            ]
        }
        ok, result = validate_syntax_plan(patch_plan, snapshot, root)
        self.assertTrue(ok)
        self.assertIsNone(result)

    def test_plan_rejects_collectively_invalid(self):
        """Individually valid patches that together produce invalid Python are rejected."""
        from pathlib import Path
        root = "/proj"
        p = Path(root).resolve() / "src" / "foo.py"
        # patch1: pass -> return 1 (valid). patch2: return 1 -> return 1 ) (invalid).
        snapshot = {p: "def f():\n    pass\nx = 1"}
        patch_plan = {
            "changes": [
                {"file": "src/foo.py", "patch": {"action": "text_sub", "old": "pass", "new": "return 1"}},
                {"file": "src/foo.py", "patch": {"action": "text_sub", "old": "return 1", "new": "return 1 )"}},
            ]
        }
        ok, result = validate_syntax_plan(patch_plan, snapshot, root)
        self.assertFalse(ok)
        self.assertEqual(result["error_type"], "syntax_error")

    def test_plan_rejects_patch_apply_failed(self):
        """Plan rejects when a patch cannot be applied."""
        from pathlib import Path
        root = "/proj"
        p = Path(root).resolve() / "src" / "foo.py"
        snapshot = {p: "x = 1"}
        patch_plan = {
            "changes": [
                {"file": "src/foo.py", "patch": {"action": "text_sub", "old": "y", "new": "z"}},
            ]
        }
        ok, result = validate_syntax_plan(patch_plan, snapshot, root)
        self.assertFalse(ok)
        self.assertEqual(result["error_type"], "patch_apply_failed")


if __name__ == "__main__":
    unittest.main()
