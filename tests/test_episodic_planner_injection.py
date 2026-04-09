"""Phase 5.5a — episodic failure lines in planner context prompts."""

from __future__ import annotations

from unittest.mock import patch

from agent_v2.planner.planner_v2 import PlannerV2
from agent_v2.runtime.exploration_planning_input import (
    call_planner_with_context,
    exploration_to_planner_context,
)
from agent_v2.schemas.policies import ExecutionPolicy
from tests.test_planner_v2 import _minimal_exploration, _valid_plan_json


def test_format_episodic_failure_block_empty() -> None:
    assert PlannerV2._format_episodic_failure_block([]) == ""


def test_format_episodic_failure_block_renders_recap() -> None:
    failures = [
        {"tool": "open_file", "error_type": "tool_error", "timestamp": "2026-04-09T12:00:00Z"},
        {"tool": "search", "error_type": "timeout", "timestamp": "2026-04-08T00:00:00Z"},
    ]
    text = PlannerV2._format_episodic_failure_block(failures)
    assert "RECENT FAILURES (advisory; avoid repeating):" in text
    assert "open_file:tool_error" in text
    assert "search:timeout" in text
    assert " ∙ " in text
    assert "If conflicts with exploration, trust exploration." in text


def test_format_episodic_failure_block_caps_at_three() -> None:
    rows = [
        {"tool": f"t{i}", "error_type": "e", "timestamp": "2026-01-01T00:00:00Z"}
        for i in range(5)
    ]
    text = PlannerV2._format_episodic_failure_block(rows)
    assert text.count(" ∙ ") == 2


def test_planner_prompt_includes_failures_after_attach() -> None:
    captured: dict[str, str] = {}

    def gen(user: str, system_prompt: str | None = None) -> str:
        captured["user"] = user
        return _valid_plan_json()

    policy = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
    planner = PlannerV2(generate_fn=gen, policy=policy)
    ex = _minimal_exploration()
    ctx = exploration_to_planner_context(ex, session=None)

    fake_failures = [
        {"tool": "shell", "error_type": "policy_denied", "timestamp": "2026-04-09T10:00:00Z"},
    ]
    with (
        patch(
            "agent_v2.runtime.planner_task_runtime._get_recent_failures",
            return_value=fake_failures,
        ),
        patch(
            "agent_v2.runtime.planner_task_runtime.enable_episodic_injection",
            return_value=True,
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

    assert "RECENT FAILURES" in captured["user"]
    assert "shell:policy_denied" in captured["user"]


def test_episodic_injection_disabled_no_block_in_prompt() -> None:
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
            return_value=[
                {"tool": "edit", "error_type": "x", "timestamp": "2026-01-01T00:00:00Z"},
            ],
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

    assert "RECENT FAILURES" not in captured["user"]
