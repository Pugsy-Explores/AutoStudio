"""
Bucketed PlannerV2 tests: real-schema exploration fixtures + mock LLM (no exploration execution).

Print human-readable plans with::

    PLANNER_V2_BUCKET_VERBOSE=1 pytest tests/test_planner_v2_bucketed.py -s
"""

from __future__ import annotations

import json
import os
import unittest
from typing import Any

from agent_v2.planner.planner_v2 import PlannerV2
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.schemas.plan import PlanDocument, PlanStep
from agent_v2.runtime.phase1_tool_exposure import ALLOWED_PLAN_STEP_ACTIONS
from agent_v2.validation.plan_validator import PlanValidator

from tests.fixtures.planner_repo_exploration_fixtures import (
    exploration_insufficient_low_confidence,
    exploration_simple_sufficient,
    exploration_with_gaps,
    exploration_with_relationships,
)

_ALLOWED_ACTIONS = ALLOWED_PLAN_STEP_ACTIONS
_VAGUE_SUBSTRINGS = (
    "understand the module",
    "understand module",
    "just explore",
    "think about",
    "figure out the codebase",
)


def _verbose() -> bool:
    return os.environ.get("PLANNER_V2_BUCKET_VERBOSE", "").strip() in ("1", "true", "yes")


def _dump_plan(instruction: str, doc: PlanDocument) -> None:
    if not _verbose():
        return
    ordered = sorted(doc.steps, key=lambda x: x.index)
    print("\n--- PlannerV2 bucket output ---")
    print("instruction:", instruction)
    print("steps (order):", " → ".join(s.step_id for s in ordered))
    print("step types:", " → ".join(s.type for s in ordered))
    print("dependencies:")
    for s in ordered:
        dep_s = ", ".join(s.dependencies) if s.dependencies else "(none)"
        print(f"  {s.step_id}: [{dep_s}]")
    print("detail:")
    for s in ordered:
        print(
            f"  {s.step_id}: type={s.type!r} action={s.action!r} "
            f"deps={s.dependencies!r} goal={s.goal[:80]!r}"
        )
    print("--- end ---\n")


def plan_structure_signature(doc: PlanDocument) -> tuple[tuple[Any, ...], ...]:
    """Stable shape: step_id, type, action, dependencies (sorted tuple)."""
    out: list[tuple[Any, ...]] = []
    for s in sorted(doc.steps, key=lambda x: x.index):
        out.append(
            (
                s.step_id,
                s.type,
                s.action,
                tuple(s.dependencies),
            )
        )
    return tuple(out)


def _assert_valid_actions_and_inputs(steps: list[PlanStep]) -> None:
    for s in steps:
        assert s.action in _ALLOWED_ACTIONS, f"bad action {s.action!r} on {s.step_id}"
        assert isinstance(s.inputs, dict)
        if s.action == "search":
            assert (s.inputs.get("query") or "").strip(), f"search needs query: {s.step_id}"
        if s.action == "open_file":
            assert (s.inputs.get("path") or "").strip(), f"open_file needs path: {s.step_id}"
        if s.action == "finish":
            assert s.type == "finish"


def _assert_not_vague_goals(steps: list[PlanStep]) -> None:
    for s in steps:
        g = (s.goal or "").strip().lower()
        assert len(g) >= 8, f"goal too short on {s.step_id}"
        for bad in _VAGUE_SUBSTRINGS:
            assert bad not in g, f"vague goal on {s.step_id}: {s.goal!r}"


def _plan_payload_base() -> dict[str, Any]:
    return {
        "understanding": "Fixture understanding.",
        "sources": [{"type": "file", "ref": "agent_v2/runtime/mode_manager.py", "summary": "fixture"}],
        "risks": [{"risk": "fixture", "impact": "low", "mitigation": "monitor"}],
        "completion_criteria": ["Criteria met"],
    }


# --- Mock LLM responses (deterministic JSON; simulates a strong model per bucket) ---


def _json_simple(variant: int) -> str:
    und = (
        "Map ACT wiring from exploration."
        if variant == 0
        else "Same task, rephrased understanding text for stability check."
    )
    p = _plan_payload_base()
    p["understanding"] = und
    p["steps"] = [
        {
            "step_id": "s1",
            "type": "explore",
            "goal": "Locate ModeManager ACT references via ripgrep query",
            "action": "search",
            "dependencies": [],
            "inputs": {"query": "ModeManager _run_act"},
            "outputs": {},
        },
        {
            "step_id": "s2",
            "type": "analyze",
            "goal": "Open mode_manager.py and confirm _run_explore_plan_execute",
            "action": "open_file",
            "dependencies": ["s1"],
            "inputs": {"path": "agent_v2/runtime/mode_manager.py"},
            "outputs": {},
        },
        {
            "step_id": "s3",
            "type": "finish",
            "goal": "Summarize ACT wiring",
            "action": "finish",
            "dependencies": ["s2"],
            "inputs": {},
            "outputs": {},
        },
    ]
    return json.dumps(p)


def _json_gaps() -> str:
    p = _plan_payload_base()
    p["understanding"] = "Replanner merge needs more files before concluding."
    p["steps"] = [
        {
            "step_id": "s1",
            "type": "explore",
            "goal": "Search merge_preserved_completed_steps usage",
            "action": "search",
            "dependencies": [],
            "inputs": {"query": "merge_preserved_completed_steps"},
            "outputs": {},
        },
        {
            "step_id": "s2",
            "type": "analyze",
            "goal": "Read replanner merge implementation",
            "action": "open_file",
            "dependencies": ["s1"],
            "inputs": {"path": "agent_v2/runtime/replanner.py"},
            "outputs": {},
        },
        {
            "step_id": "s3",
            "type": "analyze",
            "goal": "Read plan_executor for replan loop",
            "action": "open_file",
            "dependencies": ["s2"],
            "inputs": {"path": "agent_v2/runtime/plan_executor.py"},
            "outputs": {},
        },
        {
            "step_id": "s4",
            "type": "finish",
            "goal": "Answer after tracing merge and executor",
            "action": "finish",
            "dependencies": ["s3"],
            "inputs": {},
            "outputs": {},
        },
    ]
    return json.dumps(p)


def _json_relationships() -> str:
    p = _plan_payload_base()
    p["understanding"] = "Follow bootstrap → planner → mode_manager order."
    p["steps"] = [
        {
            "step_id": "s1",
            "type": "explore",
            "goal": "Search V2PlannerAdapter definition",
            "action": "search",
            "dependencies": [],
            "inputs": {"query": "V2PlannerAdapter"},
            "outputs": {},
        },
        {
            "step_id": "s2",
            "type": "analyze",
            "goal": "Open planner_v2.py for PlannerV2 class",
            "action": "open_file",
            "dependencies": ["s1"],
            "inputs": {"path": "agent_v2/planner/planner_v2.py"},
            "outputs": {},
        },
        {
            "step_id": "s3",
            "type": "analyze",
            "goal": "Open mode_manager.py for planner.plan calls",
            "action": "open_file",
            "dependencies": ["s2"],
            "inputs": {"path": "agent_v2/runtime/mode_manager.py"},
            "outputs": {},
        },
        {
            "step_id": "s4",
            "type": "finish",
            "goal": "Summarize entry chain",
            "action": "finish",
            "dependencies": ["s3"],
            "inputs": {},
            "outputs": {},
        },
    ]
    return json.dumps(p)


def _json_insufficient() -> str:
    p = _plan_payload_base()
    p["understanding"] = "Low confidence: narrow reads only; no edits."
    p["steps"] = [
        {
            "step_id": "s1",
            "type": "explore",
            "goal": "Search _run_act_controller_loop references",
            "action": "search",
            "dependencies": [],
            "inputs": {"query": "_run_act_controller_loop"},
            "outputs": {},
        },
        {
            "step_id": "s2",
            "type": "analyze",
            "goal": "Read controller loop section in mode_manager.py",
            "action": "open_file",
            "dependencies": ["s1"],
            "inputs": {"path": "agent_v2/runtime/mode_manager.py"},
            "outputs": {},
        },
        {
            "step_id": "s3",
            "type": "finish",
            "goal": "Report what is visible without further exploration",
            "action": "finish",
            "dependencies": ["s2"],
            "inputs": {},
            "outputs": {},
        },
    ]
    return json.dumps(p)


def _make_gen_for_bucket(bucket: str):
    """Return generate_fn(prompt)->str for PlannerV2."""
    state = {"stability_call": 0}

    def gen(_prompt: str) -> str:
        if bucket == "simple":
            return _json_simple(0)
        if bucket == "gaps":
            return _json_gaps()
        if bucket == "relationships":
            return _json_relationships()
        if bucket == "insufficient":
            return _json_insufficient()
        if bucket == "stability":
            v = state["stability_call"]
            state["stability_call"] = v + 1
            return _json_simple(v % 2)
        raise ValueError(bucket)

    return gen


class TestPlannerV2Bucketed(unittest.TestCase):
    policy = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

    def _run(
        self,
        exploration: FinalExplorationSchema,
        bucket: str,
    ) -> PlanDocument:
        planner = PlannerV2(
            generate_fn=_make_gen_for_bucket(bucket),
            policy=self.policy,
        )
        doc = planner.plan(
            exploration.instruction,
            exploration,
            deep=False,
        )
        PlanValidator.validate_plan(doc, policy=self.policy)
        _assert_valid_actions_and_inputs(doc.steps)
        _assert_not_vague_goals(doc.steps)
        _dump_plan(exploration.instruction, doc)
        return doc

    def test_bucket_1_simple_sufficient(self):
        ex = exploration_simple_sufficient()
        doc = self._run(ex, "simple")
        self.assertGreaterEqual(len(doc.steps), 1)
        self.assertLessEqual(len(doc.steps), 3)
        self.assertEqual(doc.steps[-1].action, "finish")
        actions = [s.action for s in doc.steps]
        self.assertTrue(all(a in _ALLOWED_ACTIONS for a in actions))

    def test_bucket_2_partial_gaps_refinement_not_direct_finish(self):
        ex = exploration_with_gaps()
        doc = self._run(ex, "gaps")
        self.assertGreaterEqual(len(doc.steps), 3)
        self.assertNotEqual(doc.steps[0].action, "finish")
        non_finish = [s for s in doc.steps if s.action != "finish"]
        self.assertTrue(any(s.action == "search" for s in non_finish))
        self.assertTrue(any(s.action == "open_file" for s in non_finish))

    def test_bucket_3_dependency_order_respects_edges(self):
        ex = exploration_with_relationships()
        doc = self._run(ex, "relationships")
        by_id = {s.step_id: s for s in doc.steps}
        for s in doc.steps:
            my_i = s.index
            for dep in s.dependencies:
                if dep in by_id:
                    self.assertLess(
                        by_id[dep].index,
                        my_i,
                        msg=f"{s.step_id} must depend only on prior indices",
                    )
        # open_file steps: mode_manager should depend on planner path (s3 after s2)
        s2 = by_id.get("s2")
        s3 = by_id.get("s3")
        if s2 and s3 and "s2" in s3.dependencies:
            self.assertLess(s2.index, s3.index)

    def test_bucket_4_insufficient_read_only_refinement(self):
        ex = exploration_insufficient_low_confidence()
        doc = self._run(ex, "insufficient")
        for s in doc.steps:
            if s.action != "finish":
                self.assertIn(
                    s.action,
                    {"search", "open_file"},
                    msg=f"expected refinement-only before finish, got {s.action}",
                )

    def test_advanced_same_input_structure_stable(self):
        ex = exploration_simple_sufficient()
        planner = PlannerV2(
            generate_fn=_make_gen_for_bucket("stability"),
            policy=self.policy,
        )
        d1 = planner.plan(ex.instruction, ex, deep=False)
        d2 = planner.plan(ex.instruction, ex, deep=False)
        PlanValidator.validate_plan(d1, policy=self.policy)
        PlanValidator.validate_plan(d2, policy=self.policy)
        self.assertEqual(plan_structure_signature(d1), plan_structure_signature(d2))
        self.assertNotEqual(d1.understanding, d2.understanding)


if __name__ == "__main__":
    unittest.main()
