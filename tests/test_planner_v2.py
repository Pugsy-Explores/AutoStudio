"""Planner v2 (Phase 4): PlannerInput → PlanDocument + PlanValidator."""

import json
import unittest
from types import SimpleNamespace

from agent_v2.planner.planner_v2 import (
    TOOL_REPAIR_EXHAUSTED_PREFIX,
    PlannerV2,
    _coerce_step_action_and_type,
)
from agent_v2.runtime.session_memory import SessionMemory
from agent_v2.schemas.planner_plan_context import PlannerPlanContext
from agent_v2.schemas.exploration import ExplorationResultMetadata, ExplorationSummary
from agent_v2.schemas.final_exploration import ExplorationAdapterTrace, FinalExplorationSchema
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.schemas.replan import (
    ReplanContext,
    ReplanFailureContext,
    ReplanFailureError,
)
from agent_v2.validation.plan_validator import PlanValidationError, PlanValidator


def _minimal_exploration(instruction: str = "Explain AgentLoop") -> FinalExplorationSchema:
    return FinalExplorationSchema(
        exploration_id="exp_test",
        instruction=instruction,
        status="complete",
        evidence=[],
        relationships=[],
        exploration_summary=ExplorationSummary(
            overall="Exploration found agent loop in agent_v2/runtime.",
            key_findings=["AgentLoop coordinates dispatcher and action generator."],
            knowledge_gaps=[],
            knowledge_gaps_empty_reason="No gaps for this smoke test.",
        ),
        metadata=ExplorationResultMetadata(total_items=0, created_at="2026-01-01T00:00:00Z"),
        confidence="high",
        trace=ExplorationAdapterTrace(llm_used=False, synthesis_success=False),
    )


def _valid_plan_json() -> str:
    payload = {
        "understanding": "User wants an explanation of AgentLoop.",
        "sources": [
            {"type": "file", "ref": "agent_v2/runtime/agent_loop.py", "summary": "Main loop"}
        ],
        "risks": [
            {
                "risk": "File path may differ",
                "impact": "low",
                "mitigation": "Search if open_file fails",
            }
        ],
        "completion_criteria": ["Explanation delivered"],
        "steps": [
            {
                "step_id": "s1",
                "type": "analyze",
                "goal": "Read AgentLoop source",
                "action": "open_file",
                "dependencies": [],
                "inputs": {"path": "agent_v2/runtime/agent_loop.py"},
                "outputs": {},
            },
            {
                "step_id": "s2",
                "type": "finish",
                "goal": "Finish",
                "action": "finish",
                "dependencies": ["s1"],
                "inputs": {},
                "outputs": {},
            },
        ],
    }
    return json.dumps(payload)


def _valid_plan_json_with_controller() -> str:
    payload = json.loads(_valid_plan_json())
    payload["controller"] = {
        "action": "continue",
        "next_step_instruction": "Execute the next pending step",
        "exploration_query": "",
    }
    return json.dumps(payload)


def _valid_decision_json_act() -> str:
    return json.dumps(
        {
            "decision": "act",
            "reason": "User wants an explanation of AgentLoop.",
            "query": "",
            "step": {
                "action": "search",
                "input": "Search the codebase for AgentLoop entrypoints",
            },
        }
    )


class TestPlannerActSemanticCoercion(unittest.TestCase):
    def test_act_tool_none_empty_search_done_reason_becomes_stop(self):
        """Mirrors small-model JSON: act + tool none + empty search + 'answered from findings'."""
        bad = {
            "decision": "act",
            "tool": "none",
            "reason": "The task can be answered from the current findings.",
            "query": "",
            "step": {"action": "search", "input": "", "metadata": {}},
        }

        def gen(_p: str, _s=None) -> str:
            return json.dumps(bad)

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        doc = planner.plan("Explain AgentLoop", _minimal_exploration())
        self.assertEqual(doc.engine.decision, "stop")
        self.assertEqual(doc.engine.tool, "none")
        self.assertIsNone(doc.engine.step)

    def test_act_search_code_empty_query_done_reason_becomes_stop(self):
        payload = {
            "decision": "act",
            "tool": "search_code",
            "reason": "Sufficient information from current findings.",
            "query": "",
            "step": {"action": "search", "input": "", "metadata": {}},
        }

        def gen(_p: str, _s=None) -> str:
            return json.dumps(payload)

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        doc = planner.plan("x", _minimal_exploration())
        self.assertEqual(doc.engine.decision, "stop")

    def test_planner_accepts_reasoning_plus_fenced_json(self):
        def gen(_p: str, _s=None) -> str:
            return (
                "Reasoning about next action.\n"
                "```json\n"
                '{"decision":"stop","tool":"none","reason":"Done","query":"","step":null}\n'
                "```"
            )

        planner = PlannerV2(
            generate_fn=gen,
            policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2),
        )
        doc = planner.plan("Explain AgentLoop", _minimal_exploration())
        self.assertEqual(doc.engine.decision, "stop")
        self.assertEqual(doc.engine.tool, "none")

    def test_last_planner_validation_error_in_exploration_context_block(self):
        from agent_v2.runtime.exploration_planning_input import exploration_to_planner_context

        mem = SessionMemory()
        mem.last_planner_validation_error = 'tool "search_code" requires non-empty search query'
        ctx = exploration_to_planner_context(_minimal_exploration(), session=mem)
        planner = PlannerV2(
            generate_fn=lambda _u, _s=None: _valid_decision_json_act(),
            policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2),
        )
        block = planner._compose_exploration_context_block(
            ctx, deep=False, task_mode=None, plan_state=None
        )
        self.assertIn("LAST PLANNER JSON VALIDATION ERROR", block)
        self.assertIn("non-empty search query", block)

    def test_exploration_context_block_includes_available_and_missing_symbols(self):
        ctx = PlannerPlanContext(
            exploration=_minimal_exploration(),
            available_symbols=["Foo.run", "Bar.handle"],
            missing_symbols=["BazClient.fetch"],
        )
        planner = PlannerV2(
            generate_fn=lambda _u, _s=None: _valid_decision_json_act(),
            policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2),
        )
        block = planner._compose_exploration_context_block(
            ctx, deep=False, task_mode=None, plan_state=None
        )
        self.assertIn("AVAILABLE SYMBOLS", block)
        self.assertIn("MISSING SYMBOLS", block)
        self.assertIn("Foo.run", block)
        self.assertIn("BazClient.fetch", block)

    def test_exploration_to_planner_context_reads_symbol_signals_from_state(self):
        from agent_v2.runtime.exploration_planning_input import exploration_to_planner_context

        state = SimpleNamespace(
            context={
                "exploration_available_symbols": ["Foo.run"],
                "exploration_missing_symbols": ["Need.more"],
            }
        )
        ctx = exploration_to_planner_context(_minimal_exploration(), state=state)
        self.assertEqual(ctx.available_symbols, ["Foo.run"])
        self.assertEqual(ctx.missing_symbols, ["Need.more"])

    def test_user_task_intent_falls_back_to_instruction_when_query_intent_missing(self):
        """When exploration never produced QueryIntent, CONTEXT still carries scope from the user task."""
        ctx = PlannerPlanContext(exploration=_minimal_exploration())
        text = PlannerV2._user_task_intent_section(
            ctx, instruction="Locate the frobulator and describe its API"
        )
        self.assertIn("scope_from_user_instruction", text)
        self.assertIn("frobulator", text)
        self.assertNotIn("not recorded", text)


class TestPlannerActionTypeCoercion(unittest.TestCase):
    """
    Lock the temporary _coerce_step_action_and_type() behavior in planner_v2.py.

    CRITICAL: These tests document RCA-driven shims. When the replanner module and
    structured planner output replace coercion, update or delete this class per
    agent_v2/planner/planner_v2.py module TODO(replanner).
    """

    def test_coerce_llm_swapped_analyze_action(self):
        tool, intent = _coerce_step_action_and_type("analyze", "analyze")
        self.assertEqual(tool, "open_file")
        self.assertEqual(intent, "analyze")

    def test_coerce_type_was_tool_name(self):
        tool, intent = _coerce_step_action_and_type("search", "open_file")
        self.assertEqual(tool, "search")
        self.assertIn(intent, ("explore", "analyze"))


class TestPlannerV2(unittest.TestCase):
    def test_plan_returns_valid_plan_document(self):
        calls = []

        def gen(prompt: str) -> str:
            calls.append(prompt)
            return _valid_decision_json_act()

        policy = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
        planner = PlannerV2(generate_fn=gen, policy=policy)
        doc = planner.plan("Explain AgentLoop", _minimal_exploration())

        self.assertIsNotNone(doc.engine)
        self.assertEqual(doc.engine.decision, "act")
        self.assertEqual(doc.engine.tool, "search_code")
        self.assertIsNotNone(doc.engine.step)
        self.assertEqual(doc.engine.step.action, "search")
        self.assertTrue(doc.understanding)
        self.assertLessEqual(len(doc.steps), 8)
        actions = [s.action for s in doc.steps]
        self.assertIn("finish", actions)
        self.assertEqual(doc.steps[-1].type, "finish")
        self.assertEqual(doc.steps[-1].action, "finish")
        for s in doc.steps:
            self.assertEqual(s.execution.max_attempts, policy.max_retries_per_step)
        self.assertTrue(calls)
        self.assertIn("USER INSTRUCTION", calls[0])
        self.assertIn("CURRENT UNDERSTANDING:", calls[0])
        self.assertIn("KEY FINDINGS:", calls[0])
        self.assertIn("CONFIDENCE:", calls[0])
        self.assertIn("decision engine", calls[0].lower())

    def test_plan_from_replan_context(self):
        def gen(prompt: str) -> str:
            self.assertIn("FAILURE", prompt)
            self.assertIn("decision engine", prompt.lower())
            return _valid_plan_json()

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        ctx = ReplanContext(
            failure_context=ReplanFailureContext(
                step_id="s1",
                error=ReplanFailureError(type="not_found", message="missing"),
                attempts=2,
                last_output_summary="not found",
            ),
            completed_steps=[],
        )
        doc = planner.plan("Fix thing", ctx, deep=False)
        self.assertEqual(doc.steps[-1].action, "finish")
        self.assertIsNone(doc.controller)

    def test_replan_require_controller_json_parses_controller(self):
        def gen(prompt: str) -> str:
            self.assertIn("FAILURE", prompt)
            self.assertIn('"decision"', prompt)
            return _valid_plan_json_with_controller()

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        ctx = ReplanContext(
            failure_context=ReplanFailureContext(
                step_id="s1",
                error=ReplanFailureError(type="not_found", message="missing"),
                attempts=2,
                last_output_summary="not found",
            ),
            completed_steps=[],
        )
        doc = planner.plan("Fix thing", ctx, deep=False, require_controller_json=True)
        self.assertIsNotNone(doc.controller)
        self.assertEqual(doc.controller.action, "continue")

    def test_invalid_json_raises(self):
        def gen(_: str) -> str:
            return "not json"

        planner = PlannerV2(generate_fn=gen)
        with self.assertRaises(PlanValidationError):
            planner.plan("x", _minimal_exploration())

    def test_explore_without_query_raises(self):
        def gen(_: str) -> str:
            return json.dumps(
                {"decision": "explore", "reason": "need more", "query": "", "step": ""}
            )

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        with self.assertRaises(PlanValidationError):
            planner.plan("x", _minimal_exploration())

    def test_stop_decision_yields_single_finish_step(self):
        def gen(_: str) -> str:
            return json.dumps(
                {"decision": "stop", "reason": "answered", "query": "", "step": None}
            )

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        doc = planner.plan("x", _minimal_exploration())
        self.assertEqual(doc.engine.decision, "stop")
        self.assertEqual(len(doc.steps), 1)
        self.assertEqual(doc.steps[0].action, "finish")

    def test_act_open_file_synthesis(self):
        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "reason": "Read implementation",
                    "query": "",
                    "step": {"action": "open_file", "input": "agent_v2/planner/planner_v2.py"},
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        doc = planner.plan("Explain PlannerV2", _minimal_exploration())
        self.assertEqual(doc.steps[0].action, "open_file")
        self.assertEqual(doc.steps[0].inputs.get("path"), "agent_v2/planner/planner_v2.py")

    def test_act_search_code_empty_query_rejected(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "search_code",
                    "reason": "x",
                    "query": "",
                    "step": {"action": "search", "input": "", "metadata": {}},
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=pol)
        with self.assertRaises(PlanValidationError) as ctx:
            planner.plan("x", _minimal_exploration())
        self.assertIn("search_code", str(ctx.exception).lower())

    def test_act_open_file_empty_path_rejected(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "open_file",
                    "reason": "read",
                    "query": "",
                    "step": {"action": "open_file", "input": "", "metadata": {}},
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=pol)
        with self.assertRaises(PlanValidationError):
            planner.plan("x", _minimal_exploration())

    def test_act_run_shell_empty_command_rejected(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "run_shell",
                    "reason": "cmd",
                    "query": "",
                    "step": {"action": "shell", "input": "", "metadata": {}},
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=pol)
        with self.assertRaises(PlanValidationError):
            planner.plan("x", _minimal_exploration())

    def test_act_run_tests_empty_input_allowed(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "run_tests",
                    "reason": "validate",
                    "query": "",
                    "step": {"action": "run_tests", "input": ""},
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=pol)
        doc = planner.plan("x", _minimal_exploration())
        self.assertEqual(doc.steps[0].action, "run_tests")
        PlanValidator.validate_plan(doc, policy=pol)

    def test_plan_validator_accepts_run_tests_step(self):
        from agent_v2.schemas.plan import (
            PlanDocument,
            PlanMetadata,
            PlanRisk,
            PlanSource,
            PlanStep,
                    )

        good = PlanDocument(
            plan_id="p",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="a",
                    index=1,
                    type="validate",
                    goal="g",
                    action="run_tests",
                    dependencies=[],
                ),
                PlanStep(
                    step_id="b",
                    index=2,
                    type="finish",
                    goal="done",
                    action="finish",
                    dependencies=["a"],
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        PlanValidator.validate_plan(good)

    def test_plan_validator_plan_safe_accepts_run_tests_and_shell(self):
        from agent_v2.schemas.plan import (
            PlanDocument,
            PlanMetadata,
            PlanRisk,
            PlanSource,
            PlanStep,
                    )

        doc = PlanDocument(
            plan_id="p",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="a",
                    index=1,
                    type="validate",
                    goal="tests",
                    action="run_tests",
                    dependencies=[],
                ),
                PlanStep(
                    step_id="b",
                    index=2,
                    type="analyze",
                    goal="sh",
                    action="shell",
                    inputs={"command": "ls ."},
                    dependencies=["a"],
                ),
                PlanStep(
                    step_id="c",
                    index=3,
                    type="finish",
                    goal="done",
                    action="finish",
                    dependencies=["b"],
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        PlanValidator.validate_plan(doc, task_mode="plan_safe")

    def test_plan_validator_read_only_rejects_run_tests_not_plan_safe(self):
        from agent_v2.schemas.plan import (
            PlanDocument,
            PlanMetadata,
            PlanRisk,
            PlanSource,
            PlanStep,
                    )

        doc = PlanDocument(
            plan_id="p",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="a",
                    index=1,
                    type="validate",
                    goal="tests",
                    action="run_tests",
                    dependencies=[],
                ),
                PlanStep(
                    step_id="b",
                    index=2,
                    type="finish",
                    goal="done",
                    action="finish",
                    dependencies=["a"],
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        PlanValidator.validate_plan(doc, task_mode="plan_safe")
        with self.assertRaises(PlanValidationError):
            PlanValidator.validate_plan(doc, task_mode="read_only")

    def test_get_tool_by_name_run_tests(self):
        from agent.tools.react_registry import get_tool_by_name, initialize_tool_registry

        initialize_tool_registry()
        self.assertIsNotNone(get_tool_by_name("run_tests"))

    def test_validate_action_run_tests_empty(self):
        from agent.execution.react_schema import validate_action

        ok, err = validate_action("run_tests", {})
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_explore_cap_override_to_act_search_code(self):
        mem = SessionMemory.model_validate(
            {
                "explore_streak": 3,
                "intent_anchor": {"goal": "fix", "target": "auth middleware", "entity": ""},
                "last_user_instruction": "dig deeper",
            }
        )

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "explore",
                    "tool": "explore",
                    "reason": "want more",
                    "query": "more context",
                    "step": None,
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        ctx = PlannerPlanContext(exploration=_minimal_exploration(), session=mem)
        doc = planner.plan("Fix auth middleware", ctx)
        self.assertEqual(doc.engine.decision, "act")
        self.assertEqual(doc.engine.tool, "search_code")
        self.assertEqual(doc.engine.query, "")
        self.assertIsNotNone(doc.engine.step)
        assert doc.engine.step is not None
        self.assertEqual(doc.engine.step.action, "search")
        self.assertIn("auth middleware", doc.engine.step.input)

    def test_explore_cap_prefers_recent_step_path_over_vague_instruction(self):
        mem = SessionMemory.model_validate(
            {
                "explore_streak": 3,
                "intent_anchor": {"goal": "task", "target": "", "entity": ""},
                "last_user_instruction": "do it",
                "current_task": "",
                "recent_steps": [
                    {"t": "act", "tool": "open_file", "summary": "opened src/auth/handler.py"},
                ],
            }
        )

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "explore",
                    "tool": "explore",
                    "reason": "more",
                    "query": "x",
                    "step": None,
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        ctx = PlannerPlanContext(exploration=_minimal_exploration(), session=mem)
        doc = planner.plan("do it", ctx)
        assert doc.engine and doc.engine.step
        self.assertIn("handler.py", doc.engine.step.input)

    def test_explore_cap_uses_fail_safe_when_anchor_vague(self):
        mem = SessionMemory.model_validate(
            {
                "explore_streak": 3,
                "intent_anchor": {"goal": "task", "target": "", "entity": ""},
                "last_user_instruction": "do it",
                "current_task": "auth service module",
            }
        )

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "explore",
                    "tool": "explore",
                    "reason": "more",
                    "query": "x",
                    "step": None,
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        ctx = PlannerPlanContext(exploration=_minimal_exploration(), session=mem)
        doc = planner.plan("do it", ctx)
        assert doc.engine and doc.engine.step
        self.assertIn("relevant code for", doc.engine.step.input.lower())
        self.assertIn("auth service", doc.engine.step.input.lower())

    def test_replan_clears_spurious_query_from_model_json(self):
        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "replan",
                    "tool": "none",
                    "reason": "retry",
                    "query": "models leak this field",
                    "step": None,
                }
            )

        from agent_v2.runtime.tool_policy import PLAN_MODE_TOOL_POLICY

        planner = PlannerV2(
            generate_fn=gen,
            policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2),
            tool_policy=PLAN_MODE_TOOL_POLICY,
        )
        ctx = PlannerPlanContext(exploration=_minimal_exploration())
        doc = planner.plan("x", ctx)
        self.assertEqual(doc.engine.decision, "replan")
        self.assertEqual(doc.engine.query, "")

    def test_strict_tool_rejects_missing_tool_on_act(self):
        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "reason": "go",
                    "query": "",
                    "step": {"action": "search", "input": "q"},
                }
            )

        planner = PlannerV2(
            generate_fn=gen,
            policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2),
            strict_tool=True,
        )
        with self.assertRaises(PlanValidationError) as ctx:
            planner.plan("x", _minimal_exploration())
        self.assertIn("strict_tool", str(ctx.exception).lower())

    def test_tool_repair_single_retry_then_structured_fail(self):
        bad = json.dumps(
            {
                "decision": "act",
                "tool": "totally_invalid_tool_name",
                "reason": "x",
                "query": "",
                "step": {"action": "search", "input": "q"},
            }
        )
        calls: list[str] = []

        def gen(prompt: str) -> str:
            calls.append(prompt)
            return bad

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        with self.assertRaises(PlanValidationError) as ctx:
            planner.plan("x", _minimal_exploration())
        self.assertIn(TOOL_REPAIR_EXHAUSTED_PREFIX, str(ctx.exception))
        self.assertEqual(len(calls), 2)
        self.assertIn("invalid", calls[1].lower())

    def test_session_memory_appears_in_prompt(self):
        mem = SessionMemory(
            intent_anchor={"goal": "fix bug", "target": "foo.py", "entity": ""},
            explore_streak=0,
        )
        calls: list[str] = []

        def gen(prompt: str) -> str:
            calls.append(prompt)
            return _valid_decision_json_act()

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        ctx = PlannerPlanContext(exploration=_minimal_exploration(), session=mem)
        planner.plan("Explain AgentLoop", ctx)
        self.assertIn("SESSION MEMORY", calls[0])
        self.assertIn("foo.py", calls[0])

    def test_read_only_rejects_edit_act(self):
        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "edit",
                    "reason": "edit",
                    "query": "",
                    "step": {
                        "action": "edit",
                        "input": "patch foo",
                        "metadata": {"path": "a.py"},
                    },
                }
            )

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        with self.assertRaises(PlanValidationError):
            planner.plan("How does exploration work?", _minimal_exploration())

    def test_act_edit_requires_metadata_path(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "edit",
                    "reason": "patch",
                    "query": "",
                    "step": {"action": "edit", "input": "do something", "metadata": {}},
                }
            )

        from agent_v2.runtime.tool_policy import ACT_MODE_TOOL_POLICY

        planner = PlannerV2(generate_fn=gen, policy=pol, tool_policy=ACT_MODE_TOOL_POLICY)
        with self.assertRaises(PlanValidationError) as ctx:
            planner.plan("Implement fix", _minimal_exploration())
        self.assertIn("path", str(ctx.exception).lower())

    def test_act_edit_requires_instruction(self):
        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)

        def gen(_: str) -> str:
            return json.dumps(
                {
                    "decision": "act",
                    "tool": "edit",
                    "reason": "patch",
                    "query": "",
                    "step": {"action": "edit", "input": "", "metadata": {"path": "x.py"}},
                }
            )

        from agent_v2.runtime.tool_policy import ACT_MODE_TOOL_POLICY

        planner = PlannerV2(generate_fn=gen, policy=pol, tool_policy=ACT_MODE_TOOL_POLICY)
        with self.assertRaises(PlanValidationError) as ctx:
            planner.plan("Implement fix", _minimal_exploration())
        self.assertIn("instruction", str(ctx.exception).lower())

    def test_act_edit_accepts_path_in_input_with_instruction_in_metadata(self):
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
                        "input": "src/foo.py",
                        "metadata": {"instruction": "add docstring"},
                    },
                }
            )

        from agent_v2.runtime.tool_policy import ACT_MODE_TOOL_POLICY

        planner = PlannerV2(generate_fn=gen, policy=pol, tool_policy=ACT_MODE_TOOL_POLICY)
        doc = planner.plan("Implement fix", _minimal_exploration())
        self.assertEqual(doc.engine.tool, "edit")
        self.assertEqual(doc.steps[0].inputs.get("path"), "src/foo.py")

    def test_plan_validator_rejects_cyclic_dependencies(self):
        from agent_v2.schemas.plan import PlanDocument, PlanMetadata, PlanRisk, PlanSource, PlanStep

        bad = PlanDocument(
            plan_id="p",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="a",
                    index=1,
                    type="explore",
                    goal="g",
                    action="search",
                    dependencies=["b"],
                ),
                PlanStep(
                    step_id="b",
                    index=2,
                    type="analyze",
                    goal="g2",
                    action="open_file",
                    dependencies=["a"],
                ),
                PlanStep(
                    step_id="c",
                    index=3,
                    type="finish",
                    goal="done",
                    action="finish",
                    dependencies=["b"],
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        with self.assertRaises(PlanValidationError):
            PlanValidator.validate_plan(bad)

    def test_plan_validator_rejects_dependency_on_later_step(self):
        from agent_v2.schemas.plan import PlanDocument, PlanMetadata, PlanRisk, PlanSource, PlanStep

        bad = PlanDocument(
            plan_id="p",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="a",
                    index=1,
                    type="analyze",
                    goal="g",
                    action="open_file",
                    dependencies=["b"],
                ),
                PlanStep(
                    step_id="b",
                    index=2,
                    type="finish",
                    goal="done",
                    action="finish",
                    dependencies=[],
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        with self.assertRaises(PlanValidationError):
            PlanValidator.validate_plan(bad)

    def test_plan_validator_rejects_missing_finish(self):
        from agent_v2.schemas.plan import PlanDocument, PlanMetadata, PlanRisk, PlanSource, PlanStep

        bad = PlanDocument(
            plan_id="p",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="a",
                    index=1,
                    type="analyze",
                    goal="g",
                    action="open_file",
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        with self.assertRaises(PlanValidationError):
            PlanValidator.validate_plan(bad)


class TestTrimPlanStepsPreservingFinish(unittest.TestCase):
    def test_finish_after_cap_is_spliced_in(self):
        from agent_v2.planner.planner_v2 import _trim_plan_steps_preserving_finish

        steps: list[dict] = []
        for i in range(1, 10):
            steps.append(
                {
                    "step_id": str(i),
                    "type": "analyze",
                    "goal": "g",
                    "action": "open_file",
                    "dependencies": [str(i - 1)] if i > 1 else [],
                    "inputs": {},
                    "outputs": {},
                }
            )
        steps.append(
            {
                "step_id": "10",
                "type": "finish",
                "goal": "done",
                "action": "finish",
                "dependencies": ["9"],
                "inputs": {},
                "outputs": {},
            }
        )
        out = _trim_plan_steps_preserving_finish(steps, 8)
        self.assertEqual(len(out), 8)
        self.assertEqual(out[-1].get("action"), "finish")
        self.assertEqual(out[-1].get("type"), "finish")


class TestPlannerModelTaskRouting(unittest.TestCase):
    """Planner LLM task_name → models_config task_models / task_params."""

    def test_resolve_model_task_replanner_plan_act(self):
        from agent_v2.runtime.tool_policy import ACT_MODE_TOOL_POLICY, PLAN_MODE_TOOL_POLICY
        from agent_v2.schemas.execution import ErrorType
        from agent_v2.schemas.planner_plan_context import PlannerPlanContext
        from agent_v2.schemas.replan import ReplanContext, ReplanFailureContext, ReplanFailureError

        rc = ReplanContext(
            failure_context=ReplanFailureContext(
                step_id="s1",
                error=ReplanFailureError(type=ErrorType.tool_error, message="e"),
                attempts=1,
                last_output_summary="",
            ),
            completed_steps=[],
        )
        p_replan = PlannerV2(
            generate_fn=lambda *a, **k: "{}",
            policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2),
            tool_policy=PLAN_MODE_TOOL_POLICY,
        )
        self.assertEqual(
            p_replan._resolve_planner_model_task_name(PlannerPlanContext(replan=rc)),
            "PLANNER_REPLAN_PLAN",
        )
        p_replan_act = PlannerV2(
            generate_fn=lambda *a, **k: "{}",
            policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2),
            tool_policy=ACT_MODE_TOOL_POLICY,
        )
        self.assertEqual(
            p_replan_act._resolve_planner_model_task_name(PlannerPlanContext(replan=rc)),
            "PLANNER_REPLAN_ACT",
        )
        p_plan = PlannerV2(
            generate_fn=lambda *a, **k: "{}",
            policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2),
            tool_policy=PLAN_MODE_TOOL_POLICY,
        )
        self.assertEqual(
            p_plan._resolve_planner_model_task_name(
                PlannerPlanContext(exploration=_minimal_exploration())
            ),
            "PLANNER_DECISION_PLAN",
        )
        p_act = PlannerV2(
            generate_fn=lambda *a, **k: "{}",
            policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2),
            tool_policy=ACT_MODE_TOOL_POLICY,
        )
        self.assertEqual(
            p_act._resolve_planner_model_task_name(
                PlannerPlanContext(exploration=_minimal_exploration())
            ),
            "PLANNER_DECISION_ACT",
        )


class TestEngineSynthesisStepIds(unittest.TestCase):
    """Lock controller continuation: new work steps must not reuse completed step_ids (s1/s2)."""

    def test_act_without_prior_uses_s1_s2(self):
        from agent_v2.schemas.plan import PlannerEngineOutput, PlannerEngineStepSpec

        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
        p = PlannerV2(generate_fn=lambda *a, **k: "{}", policy=pol)
        eng = PlannerEngineOutput(
            decision="act",
            tool="search_code",
            reason="r",
            step=PlannerEngineStepSpec(action="search", input="q"),
        )
        steps = p._synthesize_steps_from_engine(eng, "instr")
        self.assertEqual([s.step_id for s in steps], ["s1", "s2"])

    def test_act_with_prior_plan_allocates_s3_s4(self):
        from agent_v2.schemas.plan import (
            PlanDocument,
            PlanMetadata,
            PlanRisk,
            PlanSource,
            PlanStep,
                                    PlannerEngineOutput,
            PlannerEngineStepSpec,
        )

        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
        p = PlannerV2(generate_fn=lambda *a, **k: "{}", policy=pol)
        prior = PlanDocument(
            plan_id="prior",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="s1",
                    index=1,
                    type="explore",
                    goal="g",
                    action="search",
                    inputs={"query": "q"},
                ),
                PlanStep(
                    step_id="s2",
                    index=2,
                    type="finish",
                    goal="done",
                    action="finish",
                    dependencies=["s1"],
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        eng = PlannerEngineOutput(
            decision="act",
            tool="run_shell",
            reason="r",
            step=PlannerEngineStepSpec(action="shell", input="ls"),
        )
        steps = p._synthesize_steps_from_engine(eng, "instr", prior_plan_document=prior)
        self.assertEqual([s.step_id for s in steps], ["s3", "s4"])
        self.assertEqual([s.index for s in steps], [1, 2])
        self.assertEqual(steps[0].action, "shell")
        self.assertEqual(steps[1].dependencies, ["s3"])

    def test_explore_with_prior_keeps_step_id_but_index_one_for_validator(self):
        """RCA: continuation step_id must not reuse s1; indices must still be 1..n on this document."""
        from agent_v2.schemas.plan import (
            PlanDocument,
            PlanMetadata,
            PlanRisk,
            PlanSource,
            PlanStep,
                        PlannerEngineOutput,
        )

        pol = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
        p = PlannerV2(generate_fn=lambda *a, **k: "{}", policy=pol)
        prior = PlanDocument(
            plan_id="prior",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="s1",
                    index=1,
                    type="explore",
                    goal="g",
                    action="search",
                    inputs={"query": "q"},
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        eng = PlannerEngineOutput(
            decision="explore",
            tool="none",
            reason="need context",
            query="ExplorationEngineV2 __init__",
        )
        steps = p._synthesize_steps_from_engine(eng, "instr", prior_plan_document=prior)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].step_id, "s2")
        self.assertEqual(steps[0].index, 1)


if __name__ == "__main__":
    unittest.main()
