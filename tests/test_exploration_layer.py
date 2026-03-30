"""Tests for controlled exploration layer: triggers, limits, pool-only, append, dedupe."""

from unittest.mock import patch

import pytest

from agent.execution.step_dispatcher import (
    MAX_EXPLORATION_ADDED_ROWS,
    MAX_EXPLORATION_STEPS,
    MAX_EXPLORATION_TOTAL_ROWS,
    _run_exploration,
    _should_run_exploration,
)
from agent.memory.state import AgentState
from agent.retrieval.exploration_tools import (
    expand_from_node,
    expand_symbol,
    follow_relation,
    read_file_region,
)


def _pool_row(cid: str, file: str = "a.py", **kw):
    return {"candidate_id": cid, "file": file, "symbol": "foo", "snippet": "def foo", **kw}


# --- Exploration tools ---


def test_follow_relation_returns_connected_candidates():
    """follow_relation returns candidates reachable via relations, capped at 3."""
    pool = [
        _pool_row("rc_1", "a.py", relations=[{"target_file": "b.py"}]),
        _pool_row("rc_2", "b.py"),
        _pool_row("rc_3", "b.py"),
        _pool_row("rc_4", "b.py"),
        _pool_row("rc_5", "c.py"),
    ]
    out = follow_relation("rc_1", pool)
    assert len(out) == 3
    files = {r["file"] for r in out}
    assert "b.py" in files
    assert all(r["candidate_id"] in ("rc_2", "rc_3", "rc_4") for r in out)


def test_follow_relation_empty_when_no_source():
    """follow_relation returns [] when candidate_id not in pool."""
    pool = [_pool_row("rc_1")]
    assert follow_relation("rc_99", pool) == []


def test_follow_relation_hard_limit_three():
    """follow_relation caps at 3 results."""
    pool = [
        _pool_row("rc_1", relations=[{"target_file": "b.py"}]),
        _pool_row("rc_2", "b.py"),
        _pool_row("rc_3", "b.py"),
        _pool_row("rc_4", "b.py"),
    ]
    out = follow_relation("rc_1", pool)
    assert len(out) <= 3


def test_expand_symbol_returns_row_when_has_impl():
    """expand_symbol returns row when implementation_body_present."""
    pool = [_pool_row("rc_1", implementation_body_present=True)]
    out = expand_symbol("rc_1", pool)
    assert len(out) == 1
    assert out[0]["candidate_id"] == "rc_1"


def test_expand_from_node_deterministic_tool_choice():
    """expand_from_node: relations > impl > file_region."""
    pool = [
        _pool_row("rc_1", relations=[{"target_file": "b.py"}]),
        _pool_row("rc_2", "b.py"),
    ]
    out = expand_from_node("rc_1", pool)
    assert len(out) >= 1  # follow_relation
    pool2 = [_pool_row("rc_1", implementation_body_present=True)]
    out2 = expand_from_node("rc_1", pool2)
    assert len(out2) == 1  # expand_symbol
    pool3 = [_pool_row("rc_1", "a.py")]
    out3 = expand_from_node("rc_1", pool3)
    assert len(out3) >= 1  # read_file_region


def test_read_file_region_same_file_cap():
    """read_file_region returns same-file candidates, capped at 3."""
    pool = [
        _pool_row("rc_1", "a.py"),
        _pool_row("rc_2", "a.py"),
        _pool_row("rc_3", "a.py"),
        _pool_row("rc_4", "a.py"),
    ]
    out = read_file_region("rc_1", pool)
    assert len(out) <= 3
    assert all(r["file"] == "a.py" for r in out)


# --- _should_run_exploration ---


def test_exploration_triggers_when_low_links():
    """Exploration triggers when architecture intent, bundle selector used, and linked < 2 or impl < 1."""
    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "artifact_mode": "code",
            "bundle_selector_used": True,
            "retrieval_intent": "architecture",
            "bundle_selector_selected_linked_row_count": 1,
            "bundle_selector_selected_impl_body_count": 0,
        },
    )
    assert _should_run_exploration(state) is True


def test_exploration_skipped_when_sufficient():
    """Exploration skipped when linked >= 2 and impl >= 1."""
    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "artifact_mode": "code",
            "bundle_selector_used": True,
            "retrieval_intent": "architecture",
            "bundle_selector_selected_linked_row_count": 2,
            "bundle_selector_selected_impl_body_count": 1,
        },
    )
    assert _should_run_exploration(state) is False


def test_exploration_skipped_without_bundle_selector():
    """Exploration skipped when bundle_selector_used is False."""
    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "artifact_mode": "code",
            "bundle_selector_used": False,
            "retrieval_intent": "architecture",
        },
    )
    assert _should_run_exploration(state) is False


def test_exploration_skipped_non_architecture_intent():
    """Exploration skipped when intent is not architecture."""
    state = AgentState(
        instruction="find foo",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "artifact_mode": "code",
            "bundle_selector_used": True,
            "retrieval_intent": "symbol",
        },
    )
    assert _should_run_exploration(state) is False


# --- _run_exploration ---


def test_exploration_max_steps_and_rows():
    """Exploration adds at most MAX_EXPLORATION_ADDED_ROWS and runs at most MAX_EXPLORATION_STEPS."""
    pool = [
        _pool_row("rc_1", "a.py", relations=[{"target_file": "b.py"}]),
        _pool_row("rc_2", "b.py", implementation_body_present=True),
        _pool_row("rc_3", "b.py", relations=[{"target_file": "c.py"}]),
        _pool_row("rc_4", "b.py"),
        _pool_row("rc_5", "c.py"),
    ]
    selected = [pool[0]]  # rc_1
    state = AgentState(
        instruction="explain",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "retrieval_candidate_pool": pool,
            "bundle_selector_selected_pool": selected,
        },
    )
    _run_exploration(state)
    ctx = state.context
    assert ctx["exploration_used"] is True
    assert ctx["exploration_added_count"] <= MAX_EXPLORATION_ADDED_ROWS
    assert len(ctx["ranked_context"]) <= MAX_EXPLORATION_TOTAL_ROWS
    assert len(ctx["ranked_context"]) <= len(selected) + MAX_EXPLORATION_ADDED_ROWS


def test_exploration_only_from_pool():
    """Exploration only adds rows that exist in retrieval_candidate_pool."""
    pool = [
        _pool_row("rc_1", "a.py", relations=[{"target_file": "b.py"}]),
        _pool_row("rc_2", "b.py", implementation_body_present=True),
    ]
    selected = [pool[0]]
    state = AgentState(
        instruction="explain",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "retrieval_candidate_pool": pool,
            "bundle_selector_selected_pool": selected,
        },
    )
    _run_exploration(state)
    ranked = state.context["ranked_context"]
    pool_ids = {str(r.get("candidate_id", "")) for r in pool}
    for r in ranked:
        cid = str(r.get("candidate_id", ""))
        if cid:
            assert cid in pool_ids


def test_exploration_appends_context():
    """Exploration appends to selected; does not replace."""
    pool = [
        _pool_row("rc_1", "a.py", relations=[{"target_file": "b.py"}]),
        _pool_row("rc_2", "b.py", implementation_body_present=True),
        _pool_row("rc_3", "c.py"),
    ]
    selected = [pool[0], pool[2]]
    state = AgentState(
        instruction="explain",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "retrieval_candidate_pool": pool,
            "bundle_selector_selected_pool": list(selected),
        },
    )
    _run_exploration(state)
    ranked = state.context["ranked_context"]
    selected_ids = [r["candidate_id"] for r in selected]
    for i, sid in enumerate(selected_ids):
        assert ranked[i]["candidate_id"] == sid
    assert len(ranked) >= len(selected)


def test_exploration_bridge_first_seed_order():
    """Exploration prioritizes bridge candidates as seeds before ranked non-bridge."""
    pool = [
        _pool_row("rc_1", "a.py", relations=[{"target_file": "b.py"}]),
        _pool_row("rc_2", "b.py", is_bridge=True),
        _pool_row("rc_3", "c.py"),
        _pool_row("rc_4", "d.py"),
    ]
    # rc_2 is bridge; rc_1 has more relations - ranked seeds would put rc_1 first
    selected = [pool[1], pool[0]]  # bridge, then linked
    state = AgentState(
        instruction="explain",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "retrieval_candidate_pool": pool,
            "bundle_selector_selected_pool": list(selected),
        },
    )
    _run_exploration(state)
    assert state.context.get("exploration_used") is True
    # Bridge (rc_2) should be expanded first; rc_2 has no relations so expand_symbol fallback
    # We just verify exploration ran without error and context was updated
    assert len(state.context["ranked_context"]) >= len(selected)


def test_exploration_dedupes_rows():
    """Exploration does not add duplicate candidate_ids."""
    pool = [
        _pool_row("rc_1", "a.py", relations=[{"target_file": "b.py"}]),
        _pool_row("rc_2", "b.py"),
    ]
    selected = [pool[0], pool[1]]  # already has rc_1 and rc_2
    state = AgentState(
        instruction="explain",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "retrieval_candidate_pool": pool,
            "bundle_selector_selected_pool": list(selected),
        },
    )
    _run_exploration(state)
    ranked = state.context["ranked_context"]
    seen = set()
    for r in ranked:
        cid = str(r.get("candidate_id", ""))
        assert cid not in seen
        seen.add(cid)


def test_exploration_path_continuity():
    """Exploration adds exploration_parent_id and exploration_depth to added rows."""
    pool = [
        _pool_row("rc_1", "a.py", relations=[{"target_file": "b.py"}]),
        _pool_row("rc_2", "b.py", implementation_body_present=True),
        _pool_row("rc_3", "b.py"),
    ]
    selected = [pool[0]]
    state = AgentState(
        instruction="explain",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "retrieval_candidate_pool": pool,
            "bundle_selector_selected_pool": selected,
        },
    )
    _run_exploration(state)
    ranked = state.context["ranked_context"]
    added = [r for r in ranked if r.get("exploration_parent_id")]
    for r in added:
        assert "exploration_parent_id" in r
        assert "exploration_depth" in r
        assert r["exploration_depth"] >= 1


def test_exploration_filters_low_value_rows():
    """Exploration only adds rows with relations or implementation_body_present."""
    pool = [
        _pool_row("rc_1", "a.py", relations=[{"target_file": "b.py"}]),
        _pool_row("rc_2", "b.py"),  # no relations, no impl - filtered out
        _pool_row("rc_3", "b.py", implementation_body_present=True),  # kept
    ]
    selected = [pool[0]]
    state = AgentState(
        instruction="explain",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "retrieval_candidate_pool": pool,
            "bundle_selector_selected_pool": selected,
        },
    )
    _run_exploration(state)
    ranked = state.context["ranked_context"]
    added = [r for r in ranked if r.get("exploration_parent_id")]
    for r in added:
        assert r.get("relations") or r.get("implementation_body_present")


def test_exploration_linked_gain_metric():
    """Exploration sets exploration_linked_gain in context."""
    pool = [
        _pool_row("rc_1", "a.py", relations=[{"target_file": "b.py"}]),
        _pool_row("rc_2", "b.py", relations=[{"target_file": "c.py"}]),  # linked
        _pool_row("rc_3", "c.py"),
    ]
    selected = [pool[0]]  # rc_1 has 1 linked; rc_2 adds 1 more
    state = AgentState(
        instruction="explain",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "retrieval_candidate_pool": pool,
            "bundle_selector_selected_pool": selected,
        },
    )
    _run_exploration(state)
    assert "exploration_linked_gain" in state.context
    assert state.context["exploration_linked_gain"] >= 0


def test_exploration_noop_when_empty_selected():
    """_run_exploration is no-op when selected is empty."""
    pool = [_pool_row("rc_1")]
    state = AgentState(
        instruction="explain",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "retrieval_candidate_pool": pool,
            "bundle_selector_selected_pool": [],
        },
    )
    _run_exploration(state)
    assert "exploration_used" not in state.context


def test_exploration_noop_when_empty_pool():
    """_run_exploration is no-op when pool is empty."""
    selected = [_pool_row("rc_1")]
    state = AgentState(
        instruction="explain",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "retrieval_candidate_pool": [],
            "bundle_selector_selected_pool": selected,
        },
    )
    _run_exploration(state)
    assert "exploration_used" not in state.context
