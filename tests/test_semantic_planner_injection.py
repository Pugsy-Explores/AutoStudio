"""Phase 5.5b — semantic fact lines in planner context prompts."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from agent_v2.planner.planner_v2 import PlannerV2
from agent_v2.runtime.exploration_planning_input import (
    call_planner_with_context,
    exploration_to_planner_context,
)
from agent_v2.schemas.policies import ExecutionPolicy
from tests.test_planner_v2 import _minimal_exploration, _valid_plan_json


def test_format_semantic_facts_block_empty() -> None:
    assert PlannerV2._format_semantic_facts_block([]) == ""


def test_format_semantic_facts_block_renders_recap() -> None:
    facts = [
        {"key": "entrypoint", "text": "Main CLI lives in app/main.py"},
        {"key": "policy", "text": "No edits in plan_safe mode"},
    ]
    text = PlannerV2._format_semantic_facts_block(facts)
    assert "PROJECT FACTS (advisory):" in text
    assert "entrypoint:" in text
    assert "Main CLI lives in app/main.py"[:60] in text
    assert " ∙ " in text
    assert "If conflicts with exploration, trust exploration." not in text


def test_normalize_semantic_fact_key_type_prefix() -> None:
    assert PlannerV2._normalize_semantic_fact_key("file:agent/main.py") == "file:agent/main.py"
    assert PlannerV2._normalize_semantic_fact_key("rule:no_edits_in_plan_safe") == "rule:no_edits_in_pl"
    assert PlannerV2._normalize_semantic_fact_key("plainkey") == "plainkey"


def test_format_semantic_facts_block_caps_at_three() -> None:
    rows = [{"key": f"k{i}", "text": "x"} for i in range(5)]
    text = PlannerV2._format_semantic_facts_block(rows)
    assert text.count(" ∙ ") == 2


def test_planner_prompt_includes_semantic_after_attach() -> None:
    captured: dict[str, str] = {}

    def gen(user: str, system_prompt: str | None = None) -> str:
        captured["user"] = user
        return _valid_plan_json()

    policy = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
    planner = PlannerV2(generate_fn=gen, policy=policy)
    ex = _minimal_exploration()
    ctx = exploration_to_planner_context(ex, session=None)

    fake_facts = [{"key": "routing", "text": "Use dispatcher for all tool calls"}]
    with (
        patch(
            "agent_v2.runtime.planner_task_runtime._get_relevant_facts",
            return_value=fake_facts,
        ),
        patch(
            "agent_v2.runtime.planner_task_runtime.enable_semantic_injection",
            return_value=True,
        ),
        patch(
            "agent_v2.runtime.planner_task_runtime.enable_episodic_injection",
            return_value=False,
        ),
    ):
        call_planner_with_context(
            planner,
            "Explain AgentLoop dispatcher",
            ctx,
            deep=False,
            obs=None,
            langfuse_trace=None,
            require_controller_json=False,
            session=None,
            state=SimpleNamespace(instruction="Explain AgentLoop dispatcher"),
        )

    u = captured["user"]
    assert "PROJECT FACTS" in u
    assert "routing:Use dispatcher for all tool calls" in u
    assert "RECENT FAILURES" not in u
    assert u.count("If conflicts with exploration, trust exploration.") == 1


def test_semantic_injection_disabled_no_block_in_prompt() -> None:
    captured: dict[str, str] = {}

    def gen(user: str, system_prompt: str | None = None) -> str:
        captured["user"] = user
        return _valid_plan_json()

    policy = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
    planner = PlannerV2(generate_fn=gen, policy=policy)
    ex = _minimal_exploration()
    ctx = exploration_to_planner_context(ex, session=None)

    with (
        patch(
            "agent_v2.runtime.planner_task_runtime._get_relevant_facts",
            return_value=[{"key": "x", "text": "y"}],
        ),
        patch(
            "agent_v2.runtime.planner_task_runtime.enable_semantic_injection",
            return_value=False,
        ),
        patch(
            "agent_v2.runtime.planner_task_runtime.enable_episodic_injection",
            return_value=False,
        ),
    ):
        call_planner_with_context(
            planner,
            "Explain AgentLoop",
            ctx,
            deep=False,
            obs=None,
            langfuse_trace=None,
            require_controller_json=False,
            session=None,
        )

    assert "PROJECT FACTS" not in captured["user"]


def test_episodic_and_semantic_both_in_prompt() -> None:
    captured: dict[str, str] = {}

    def gen(user: str, system_prompt: str | None = None) -> str:
        captured["user"] = user
        return _valid_plan_json()

    policy = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
    planner = PlannerV2(generate_fn=gen, policy=policy)
    ex = _minimal_exploration()
    ctx = exploration_to_planner_context(ex, session=None)

    with (
        patch(
            "agent_v2.runtime.planner_task_runtime._get_recent_failures",
            return_value=[{"tool": "search", "error_type": "timeout"}],
        ),
        patch(
            "agent_v2.runtime.planner_task_runtime._get_relevant_facts",
            return_value=[{"key": "fact1", "text": "remember the graph"}],
        ),
        patch(
            "agent_v2.runtime.planner_task_runtime.enable_episodic_injection",
            return_value=True,
        ),
        patch(
            "agent_v2.runtime.planner_task_runtime.enable_semantic_injection",
            return_value=True,
        ),
    ):
        call_planner_with_context(
            planner,
            "search graph sqlite",
            ctx,
            deep=False,
            obs=None,
            langfuse_trace=None,
            require_controller_json=False,
            session=None,
        )

    u = captured["user"]
    assert "RECENT FAILURES" in u
    assert "search:timeout" in u
    assert "PROJECT FACTS" in u
    assert "fact1:remember the graph" in u
    assert u.find("RECENT FAILURES") < u.find("PROJECT FACTS")
    assert u.count("If conflicts with exploration, trust exploration.") == 1
