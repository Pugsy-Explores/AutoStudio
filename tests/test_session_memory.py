"""SessionMemory — intent anchor heuristics and update hooks."""

import unittest

from agent_v2.runtime.session_memory import (
    RECENT_STEPS_MAX,
    SessionMemory,
    derive_intent_anchor_from_user_text,
)


class TestSessionMemory(unittest.TestCase):
    def test_vague_message_retains_anchor(self) -> None:
        mem = SessionMemory()
        mem.record_user_turn("Fix auth bug in middleware")
        self.assertIn("middleware", mem.intent_anchor.target.lower())
        mem.record_user_turn("do it")
        self.assertIn("middleware", mem.intent_anchor.target.lower())

    def test_derive_anchor_extracts_error_entity(self) -> None:
        a = derive_intent_anchor_from_user_text("Fix KeyError in token validation")
        self.assertEqual(a.entity, "KeyError")

    def test_recent_steps_hard_cap_fifo(self) -> None:
        mem = SessionMemory()
        n_extra = 3
        for i in range(RECENT_STEPS_MAX + n_extra):
            mem.record_executor_event(
                decision_kind="act",
                tool="search_code",
                summary=f"step-{i}",
            )
        self.assertEqual(len(mem.recent_steps), RECENT_STEPS_MAX)
        last_idx = RECENT_STEPS_MAX + n_extra - 1
        self.assertEqual(mem.recent_steps[-1].summary, f"step-{last_idx}")

    def test_explore_streak_increments_only_on_explore(self) -> None:
        mem = SessionMemory()
        mem.record_planner_output(decision="explore", tool="explore")
        self.assertEqual(mem.explore_streak, 1)
        mem.record_planner_output(decision="explore", tool="explore")
        self.assertEqual(mem.explore_streak, 2)
        mem.record_planner_output(decision="act", tool="search_code")
        self.assertEqual(mem.explore_streak, 0)

    def test_explore_decisions_total_accumulates_across_act(self) -> None:
        mem = SessionMemory()
        mem.record_planner_output(decision="explore", tool="explore")
        mem.record_planner_output(decision="act", tool="search_code")
        mem.record_planner_output(decision="explore", tool="explore")
        self.assertEqual(mem.explore_decisions_total, 2)
        self.assertEqual(mem.explore_streak, 1)

    def test_substantive_user_turn_resets_exploration_counters(self) -> None:
        mem = SessionMemory()
        mem.record_planner_output(decision="explore", tool="explore")
        mem.record_last_exploration_engine_steps(5)
        mem.record_user_turn("New task: refactor parser completely")
        self.assertEqual(mem.explore_decisions_total, 0)
        self.assertEqual(mem.last_exploration_engine_steps, 0)

    def test_record_last_exploration_engine_steps(self) -> None:
        mem = SessionMemory()
        mem.record_last_exploration_engine_steps(4)
        self.assertEqual(mem.last_exploration_engine_steps, 4)

    def test_record_user_turn_clears_planner_validation_error(self) -> None:
        mem = SessionMemory()
        mem.last_planner_validation_error = "bad json"
        mem.record_user_turn("New instruction for the session")
        self.assertEqual(mem.last_planner_validation_error, "")


if __name__ == "__main__":
    unittest.main()
