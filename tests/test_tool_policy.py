"""Tool policy layer (planning mode) — Docs/agent_v2_tool_policy_layer.md."""

import json
import unittest

from agent_v2.planner.planner_v2 import PlannerV2
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.schemas.plan import PlannerEngineOutput, PlannerEngineStepSpec
from agent_v2.runtime.tool_policy import (
    ACT_MODE_TOOL_POLICY,
    PLAN_MODE_ALLOWED_SHELL_FIRST,
    PLAN_MODE_TOOL_POLICY,
    ToolPolicyViolationError,
    apply_tool_policy,
    first_shell_argv0_token,
    shell_command_has_forbidden_substrings,
    shell_first_token_allowed,
)
from agent_v2.validation.plan_validator import PlanValidationError


def _minimal_expl():
    from tests.test_planner_v2 import _minimal_exploration

    return _minimal_exploration()


class TestShellTokenParsing(unittest.TestCase):
    def test_basename(self):
        self.assertEqual(first_shell_argv0_token("/usr/bin/grep foo"), "grep")
        self.assertEqual(first_shell_argv0_token("ls -la"), "ls")

    def test_allowlist(self):
        allow = PLAN_MODE_ALLOWED_SHELL_FIRST
        self.assertTrue(shell_first_token_allowed("rg pattern", allow))
        self.assertTrue(shell_first_token_allowed("grep -r x .", allow))
        self.assertFalse(shell_first_token_allowed("rm -rf /", allow))
        self.assertFalse(shell_first_token_allowed("chmod +x a", allow))

    def test_forbidden_substrings(self):
        self.assertTrue(shell_command_has_forbidden_substrings("ls && rm -rf /"))
        self.assertTrue(shell_command_has_forbidden_substrings("grep x; rm y"))
        self.assertTrue(shell_command_has_forbidden_substrings("cat a | sh"))
        self.assertTrue(shell_command_has_forbidden_substrings("echo `id`"))
        self.assertFalse(shell_command_has_forbidden_substrings("ls -la src"))


class TestApplyToolPolicy(unittest.TestCase):
    def test_plan_mode_rejects_edit(self):
        eng = PlannerEngineOutput(
            decision="act",
            tool="edit",
            reason="x",
            step=PlannerEngineStepSpec(action="edit", input="patch"),
        )
        with self.assertRaises(ToolPolicyViolationError) as ctx:
            apply_tool_policy(eng, PLAN_MODE_TOOL_POLICY)
        self.assertIn("not allowed", str(ctx.exception).lower())
        self.assertEqual(ctx.exception.policy_tool, "edit")

    def test_plan_mode_rejects_rm_shell(self):
        eng = PlannerEngineOutput(
            decision="act",
            tool="run_shell",
            reason="x",
            step=PlannerEngineStepSpec(action="shell", input="rm -f a"),
        )
        with self.assertRaises(ToolPolicyViolationError):
            apply_tool_policy(eng, PLAN_MODE_TOOL_POLICY)

    def test_plan_mode_rejects_ls_and_rm_chain(self):
        eng = PlannerEngineOutput(
            decision="act",
            tool="run_shell",
            reason="x",
            step=PlannerEngineStepSpec(action="shell", input="ls && rm -rf /"),
        )
        with self.assertRaises(ToolPolicyViolationError) as ctx:
            apply_tool_policy(eng, PLAN_MODE_TOOL_POLICY)
        self.assertIn("chaining", str(ctx.exception).lower())

    def test_act_mode_allows_edit(self):
        eng = PlannerEngineOutput(
            decision="act",
            tool="edit",
            reason="x",
            step=PlannerEngineStepSpec(action="edit", input="patch"),
        )
        apply_tool_policy(eng, ACT_MODE_TOOL_POLICY)

    def test_plan_mode_allows_ls(self):
        eng = PlannerEngineOutput(
            decision="act",
            tool="run_shell",
            reason="x",
            step=PlannerEngineStepSpec(action="shell", input="ls -la src"),
        )
        apply_tool_policy(eng, PLAN_MODE_TOOL_POLICY)


class TestPlannerV2ToolPolicyIntegration(unittest.TestCase):
    def test_plan_mode_edit_in_planner_fails(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "edit",
                    "reason": "patch",
                    "query": "",
                    "step": {"action": "edit", "input": "x"},
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=pol)
        with self.assertRaises(ToolPolicyViolationError) as ctx:
            planner.plan("Implement feature", _minimal_expl())
        self.assertIn("tool policy", str(ctx.exception).lower())

    def test_tool_policy_violation_does_not_trigger_llm_repair(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
        calls: list[int] = []

        def gen(_: str) -> str:
            calls.append(1)
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "edit",
                    "reason": "patch",
                    "query": "",
                    "step": {"action": "edit", "input": "x"},
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=pol)
        with self.assertRaises(ToolPolicyViolationError):
            planner.plan("Implement feature", _minimal_expl())
        self.assertEqual(len(calls), 1)

    def test_plan_mode_shell_rm_fails(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "run_shell",
                    "reason": "bad",
                    "query": "",
                    "step": {"action": "shell", "input": "rm -rf /tmp/x"},
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=pol)
        with self.assertRaises(PlanValidationError):
            planner.plan("x", _minimal_expl())

    def test_plan_mode_search_code_passes(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "search_code",
                    "reason": "find",
                    "query": "",
                    "step": {"action": "search", "input": "foo"},
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=pol)
        doc = planner.plan("find foo", _minimal_expl())
        self.assertEqual(doc.engine.tool, "search_code")

    def test_plan_mode_run_tests_passes(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "run_tests",
                    "reason": "t",
                    "query": "",
                    "step": {"action": "run_tests", "input": ""},
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=pol)
        doc = planner.plan("x", _minimal_expl())
        self.assertEqual(doc.steps[0].action, "run_tests")

    def test_act_mode_policy_allows_edit(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "edit",
                    "reason": "patch",
                    "query": "",
                    "step": {
                        "action": "edit",
                        "input": "change foo",
                        "metadata": {"path": "src/foo.py"},
                    },
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=pol, tool_policy=ACT_MODE_TOOL_POLICY)
        doc = planner.plan("Write code", _minimal_expl())
        self.assertEqual(doc.engine.tool, "edit")


if __name__ == "__main__":
    unittest.main()
