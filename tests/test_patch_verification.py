"""Unit tests for patch verification layer."""

import unittest

from editing.patch_verification import verify_patch, verify_patch_plan


class TestVerifyPatchNoop(unittest.TestCase):
    """test_reject_noop_patch: old == new -> rejected."""

    def test_reject_noop_patch(self):
        result = verify_patch(
            proposal={"file": "src/foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 1"}},
            full_file_content="x = 1\ny = 2",
            instruction="change x",
            binding={"file": "src/foo.py"},
            project_root="/proj",
        )
        self.assertFalse(result["valid"])
        self.assertEqual(result["reason"], "no_meaningful_diff")
        self.assertFalse(result["checks"]["has_effect"])


class TestVerifyPatchDuplicateInsert(unittest.TestCase):
    """test_reject_duplicate_insert: insert already exists -> rejected."""

    def test_reject_duplicate_insert(self):
        content = "def foo():\n    pass"
        result = verify_patch(
            proposal={
                "file": "src/foo.py",
                "patch": {"action": "insert", "code": "    pass", "target_node": "function_body"},
            },
            full_file_content=content,
            instruction="add pass",
            binding={"file": "src/foo.py"},
            project_root="/proj",
        )
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["has_effect"])


class TestVerifyPatchWrongFile(unittest.TestCase):
    """test_reject_wrong_file: file != binding.file -> rejected."""

    def test_reject_wrong_file(self):
        result = verify_patch(
            proposal={"file": "src/bar.py", "patch": {"action": "text_sub", "old": "x", "new": "y"}},
            full_file_content="x",
            instruction="change x to y",
            binding={"file": "src/foo.py"},
            project_root="/proj",
        )
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["targets_correct_file"])

    def test_reject_wrong_file_relative_paths(self):
        result = verify_patch(
            proposal={"file": "other/module.py", "patch": {"action": "text_sub", "old": "a", "new": "b"}},
            full_file_content="a",
            instruction="change a",
            binding={"file": "src/module.py"},
            project_root="/proj",
        )
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["targets_correct_file"])


class TestVerifyPatchValid(unittest.TestCase):
    """test_accept_valid_patch: valid edit passes."""

    def test_accept_valid_patch(self):
        result = verify_patch(
            proposal={"file": "src/foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 2"}},
            full_file_content="x = 1\ny = 2",
            instruction="change x to 2",
            binding={"file": "src/foo.py"},
            project_root="/proj",
        )
        self.assertTrue(result["valid"])
        self.assertTrue(result["checks"]["has_effect"])
        self.assertTrue(result["checks"]["targets_correct_file"])
        self.assertTrue(result["checks"]["is_local"])

    def test_accept_valid_insert(self):
        result = verify_patch(
            proposal={
                "file": "src/foo.py",
                "patch": {"action": "insert", "code": "    return 42", "target_node": "function_body"},
            },
            full_file_content="def foo():\n    pass",
            instruction="add return",
            binding={"file": "src/foo.py"},
            project_root="/proj",
        )
        self.assertTrue(result["valid"])
        self.assertTrue(result["checks"]["has_effect"])
        self.assertTrue(result["checks"]["targets_correct_file"])
        self.assertTrue(result["checks"]["is_local"])


class TestVerifyPatchIsLocal(unittest.TestCase):
    """is_local: old must exist in full_file_content for text_sub."""

    def test_reject_old_not_in_file(self):
        result = verify_patch(
            proposal={"file": "src/foo.py", "patch": {"action": "text_sub", "old": "z = 99", "new": "z = 1"}},
            full_file_content="x = 1\ny = 2",
            instruction="change z",
            binding={"file": "src/foo.py"},
            project_root="/proj",
        )
        self.assertFalse(result["valid"])
        self.assertFalse(result["checks"]["is_local"])
        self.assertEqual(result["reason"], "target_not_found")


class TestVerifyPatchPlan(unittest.TestCase):
    """verify_patch_plan: reject on first invalid change."""

    def test_plan_rejects_first_invalid(self):
        snapshot = {}
        root = "/proj"
        from pathlib import Path
        p = Path(root).resolve() / "src" / "foo.py"
        snapshot[p] = "x = 1"
        context = {"edit_binding": {"file": "src/foo.py"}, "instruction": ""}
        patch_plan = {
            "changes": [
                {"file": "src/foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 1"}},
            ]
        }
        ok, result = verify_patch_plan(patch_plan, snapshot, context, root)
        self.assertFalse(ok)
        self.assertIsNotNone(result)
        self.assertFalse(result["valid"])

    def test_plan_accepts_valid(self):
        from pathlib import Path
        root = "/proj"
        p = Path(root).resolve() / "src" / "foo.py"
        snapshot = {p: "x = 1"}
        context = {"edit_binding": {"file": "src/foo.py"}, "instruction": ""}
        patch_plan = {
            "changes": [
                {"file": "src/foo.py", "patch": {"action": "text_sub", "old": "x = 1", "new": "x = 2"}},
            ]
        }
        ok, result = verify_patch_plan(patch_plan, snapshot, context, root)
        self.assertTrue(ok)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
