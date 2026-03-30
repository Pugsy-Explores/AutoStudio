"""Phase 1 tool exposure invariants (Docs/agent_v2_phase1_tool_contract_audit.md)."""

import unittest

from agent_v2.runtime.phase1_tool_exposure import (
    ALLOWED_PLAN_STEP_ACTIONS,
    PHASE_1_PLANNER_TOOL_IDS,
    PLANNER_ACT_TOOL_IDS,
    PLANNER_TOOL_TO_PLAN_STEP_ACTION,
    PLAN_STEP_TO_LEGACY_REACT_ACTION,
)


class TestPhase1ToolExposure(unittest.TestCase):
    def test_act_tools_match_mapping_keys(self):
        self.assertEqual(PLANNER_ACT_TOOL_IDS, frozenset(PLANNER_TOOL_TO_PLAN_STEP_ACTION))

    def test_planner_tool_ids_union(self):
        self.assertEqual(
            PHASE_1_PLANNER_TOOL_IDS,
            PLANNER_ACT_TOOL_IDS | {"explore", "none"},
        )

    def test_allowed_plan_step_actions(self):
        self.assertEqual(
            ALLOWED_PLAN_STEP_ACTIONS,
            frozenset(PLANNER_TOOL_TO_PLAN_STEP_ACTION.values()) | {"finish"},
        )

    def test_legacy_react_covers_non_shell_plan_actions(self):
        for pa in PLANNER_TOOL_TO_PLAN_STEP_ACTION.values():
            if pa == "shell":
                self.assertNotIn(pa, PLAN_STEP_TO_LEGACY_REACT_ACTION)
            else:
                self.assertIn(pa, PLAN_STEP_TO_LEGACY_REACT_ACTION)


if __name__ == "__main__":
    unittest.main()
