"""Tests for LLM bundle selector: eligibility, parsing, rebuild, fallback."""

import json
from unittest.mock import patch

import pytest

from agent.memory.state import AgentState
from agent.retrieval.bundle_selector import (
    build_bundle_selector_payload,
    parse_bundle_selector_output,
    rebuild_ranked_context_from_selected_ids,
    run_bundle_selector,
    should_use_bundle_selector,
    _validate_selection_against_constraints,
)


def _pool_row(cid: str, **kw):
    return {"candidate_id": cid, "file": "a.py", "symbol": "foo", "snippet": "def foo", **kw}


def test_selector_not_used_when_flag_off(monkeypatch):
    """Selector not used when ENABLE_LLM_BUNDLE_SELECTOR is off."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "0")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    state = AgentState(
        instruction="how does X connect to Y",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": [_pool_row("rc_0001"), _pool_row("rc_0002"), _pool_row("rc_0003")],
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}
    assert should_use_bundle_selector(step, state, []) is False
    assert state.context.get("bundle_selector_skip_reason") == "flag_disabled"


def test_selector_not_used_for_simple_symbol_lookup(monkeypatch):
    """Selector not used for simple exact symbol/file lookups (generic intent)."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    state = AgentState(
        instruction="find StepExecutor",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": [_pool_row("rc_0001") for _ in range(6)],
            "retrieval_intent": "symbol",  # not architecture
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "find StepExecutor"}
    # Query does not suggest connection/flow; intent is symbol
    assert should_use_bundle_selector(step, state, []) is False
    assert state.context.get("bundle_selector_skip_reason") == "intent_not_matched"


def test_selector_used_for_architecture_code_explain(monkeypatch):
    """Selector used for architecture/code explain with eligible pool."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    monkeypatch.setenv("MAX_SELECTOR_CANDIDATE_POOL", "12")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    # Pool must have at least one linked row for architecture intent (Phase 5 guard)
    pool = [_pool_row(f"rc_{i:04d}") for i in range(1, 7)]
    pool[0] = _pool_row("rc_0001", relations=[{"kind": "import", "target_file": "x.py"}])
    state = AgentState(
        instruction="how does replanner connect to validator",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does replanner connect"}
    assert should_use_bundle_selector(step, state, pool) is True
    assert state.context.get("bundle_selector_skip_reason") == ""


def test_selector_runs_when_query_has_connect_keyword(monkeypatch):
    """Selector runs when query contains 'connect' (architecture keyword) even without architecture intent."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    monkeypatch.setenv("MAX_SELECTOR_CANDIDATE_POOL", "12")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    pool = [_pool_row(f"rc_{i:04d}") for i in range(1, 5)]
    state = AgentState(
        instruction="how does X connect to Y",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "symbol",  # not architecture
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect to Y"}
    assert should_use_bundle_selector(step, state, pool) is True
    assert state.context.get("bundle_selector_skip_reason") == ""


def test_selector_runs_when_pool_exceeds_max_after_trimming(monkeypatch):
    """Selector runs when pool_size > MAX; pool is trimmed to top MAX."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    monkeypatch.setenv("MAX_SELECTOR_CANDIDATE_POOL", "6")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    pool = [_pool_row(f"rc_{i:04d}") for i in range(1, 15)]
    pool[0] = _pool_row("rc_0001", relations=[{"kind": "import"}])
    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": list(pool),
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}
    assert should_use_bundle_selector(step, state, pool) is True
    trimmed = state.context.get("retrieval_candidate_pool")
    assert len(trimmed) == 6
    assert [r["candidate_id"] for r in trimmed] == [f"rc_{i:04d}" for i in range(1, 7)]


def test_selector_runs_when_force_selector_in_eval(monkeypatch):
    """Selector runs when FORCE_SELECTOR_IN_EVAL=True even with flag off and non-architecture intent."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "0")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "1")
    monkeypatch.setenv("MAX_SELECTOR_CANDIDATE_POOL", "12")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    pool = [_pool_row(f"rc_{i:04d}") for i in range(1, 5)]
    state = AgentState(
        instruction="find StepExecutor",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "symbol",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "find StepExecutor"}
    assert should_use_bundle_selector(step, state, pool) is True
    assert state.context.get("bundle_selector_skip_reason") == ""


def test_skip_reason_pool_too_small(monkeypatch):
    """skip_reason is 'pool_too_small' when pool has < 3 rows."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": [_pool_row("rc_0001"), _pool_row("rc_0002")],
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}
    assert should_use_bundle_selector(step, state, []) is False
    assert state.context.get("bundle_selector_skip_reason") == "pool_too_small"


def test_skip_reason_no_pool(monkeypatch):
    """skip_reason is 'no_pool' when retrieval_candidate_pool is missing."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={"project_root": "/tmp"},
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}
    assert should_use_bundle_selector(step, state, []) is False
    assert state.context.get("bundle_selector_skip_reason") == "no_pool"


def test_selector_runs_when_linked_row_count_ge_2(monkeypatch):
    """Selector runs when pool has >= 2 linked rows (invariant) even without architecture intent."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    monkeypatch.setenv("MAX_SELECTOR_CANDIDATE_POOL", "12")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    pool = [
        _pool_row("rc_0001", relations=[{"kind": "call"}]),
        _pool_row("rc_0002", relations=[{"kind": "import"}]),
        _pool_row("rc_0003"),
    ]
    state = AgentState(
        instruction="find foo",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "symbol",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "find foo"}
    assert should_use_bundle_selector(step, state, pool) is True
    assert state.context.get("bundle_selector_skip_reason") == ""


def test_skip_reason_not_explain_code(monkeypatch):
    """skip_reason is 'not_explain_code' when artifact_mode is docs."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    pool = [_pool_row(f"rc_{i:04d}") for i in range(1, 5)]
    state = AgentState(
        instruction="docs flow",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "docs", "description": "docs flow"}
    assert should_use_bundle_selector(step, state, pool) is False
    assert state.context.get("bundle_selector_skip_reason") == "not_explain_code"


def test_valid_selected_ids_rebuild_ranked_context():
    """Valid selected ids rebuild ranked_context correctly."""
    pool = [
        _pool_row("rc_0001", file="a.py", implementation_body_present=True),
        _pool_row("rc_0002", file="b.py", relations=[{"kind": "call"}]),
        _pool_row("rc_0003", file="c.py"),
    ]
    rebuilt = rebuild_ranked_context_from_selected_ids(pool, ["rc_0002", "rc_0001"])
    assert len(rebuilt) == 2
    assert rebuilt[0]["file"] == "b.py"
    assert rebuilt[0]["relations"] == [{"kind": "call"}]
    assert rebuilt[1]["file"] == "a.py"
    assert rebuilt[1]["implementation_body_present"] is True


def test_invalid_ids_fall_back_safely():
    """Invalid (invented) ids are filtered out; only valid ids produce rows."""
    pool = [_pool_row("rc_0001"), _pool_row("rc_0002")]
    rebuilt = rebuild_ranked_context_from_selected_ids(pool, ["rc_0001", "rc_9999", "rc_0002"])
    assert len(rebuilt) == 2
    assert rebuilt[0]["candidate_id"] == "rc_0001"
    assert rebuilt[1]["candidate_id"] == "rc_0002"


def test_empty_selector_output_falls_back():
    """Empty selector output returns None from parse."""
    assert parse_bundle_selector_output("") is None
    assert parse_bundle_selector_output("   ") is None
    assert parse_bundle_selector_output("no json here") is None


def test_parse_valid_json():
    """Parse valid JSON output."""
    raw = '{"keep_ids": ["rc_0002", "rc_0005"], "primary_ids": ["rc_0002"], "supporting_ids": ["rc_0005"], "reason": "best"}'
    out = parse_bundle_selector_output(raw)
    assert out is not None
    assert out["keep_ids"] == ["rc_0002", "rc_0005"]
    assert out["primary_ids"] == ["rc_0002"]
    assert out["supporting_ids"] == ["rc_0005"]
    assert out["reason"] == "best"


def test_parse_json_wrapped_in_markdown():
    """Parse JSON wrapped in markdown/code block."""
    raw = """
Here is my selection:
```json
{"keep_ids": ["rc_0001"], "primary_ids": ["rc_0001"], "supporting_ids": [], "reason": "single best"}
```
"""
    out = parse_bundle_selector_output(raw)
    assert out is not None
    assert out["keep_ids"] == ["rc_0001"]


def test_selected_context_preserves_typed_metadata():
    """Selected context preserves typed metadata from pool rows."""
    pool = [
        _pool_row(
            "rc_0001",
            file="x.py",
            symbol="bar",
            retrieval_result_type="symbol_body",
            implementation_body_present=True,
            line=10,
            line_range=[10, 20],
            enclosing_class="Baz",
            relations=[{"kind": "import"}],
        ),
    ]
    rebuilt = rebuild_ranked_context_from_selected_ids(pool, ["rc_0001"])
    assert len(rebuilt) == 1
    r = rebuilt[0]
    assert r["retrieval_result_type"] == "symbol_body"
    assert r["implementation_body_present"] is True
    assert r["line"] == 10
    assert r["line_range"] == [10, 20]
    assert r["enclosing_class"] == "Baz"
    assert r["relations"] == [{"kind": "import"}]


def test_run_bundle_selector_invalid_ids_falls_back():
    """run_bundle_selector with invalid ids (mocked response) falls back safely."""
    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": [_pool_row("rc_0001"), _pool_row("rc_0002")],
            "ranked_context": [{"file": "orig.py", "snippet": "original"}],
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({"keep_ids": ["rc_9999"], "primary_ids": [], "supporting_ids": [], "reason": ""})
        ok = run_bundle_selector(step, state)
    assert ok is False
    assert state.context["ranked_context"] == [{"file": "orig.py", "snippet": "original"}]


def test_run_bundle_selector_empty_keep_ids_falls_back():
    """run_bundle_selector with empty keep_ids falls back safely."""
    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": [_pool_row("rc_0001")],
            "ranked_context": [{"file": "orig.py"}],
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({"keep_ids": [], "primary_ids": [], "supporting_ids": [], "reason": ""})
        ok = run_bundle_selector(step, state)
    assert ok is False
    assert state.context["ranked_context"] == [{"file": "orig.py"}]


def test_run_bundle_selector_success_rebuilds_context():
    """run_bundle_selector with valid ids rebuilds ranked_context."""
    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": [
                _pool_row("rc_0001", file="a.py"),
                _pool_row("rc_0002", file="b.py"),
            ],
            "ranked_context": [{"file": "old.py"}],
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({
            "keep_ids": ["rc_0002", "rc_0001"],
            "primary_ids": ["rc_0002"],
            "supporting_ids": ["rc_0001"],
            "reason": "both relevant",
        })
        ok = run_bundle_selector(step, state)
    assert ok is True
    assert state.context["bundle_selector_used"] is True
    assert state.context["bundle_selector_keep_ids"] == ["rc_0002", "rc_0001"]
    rc = state.context["ranked_context"]
    assert len(rc) == 2
    assert rc[0]["file"] == "b.py"
    assert rc[1]["file"] == "a.py"


def test_build_bundle_selector_observability_summary():
    """Observability summary extracts selector state from context."""
    from agent.retrieval.bundle_selector import build_bundle_selector_observability_summary

    ctx = {
        "bundle_selector_used": True,
        "bundle_selector_skip_reason": "",
        "bundle_selector_keep_ids": ["rc_0001", "rc_0002"],
        "bundle_selector_dropped_ids": ["rc_0003"],
        "bundle_selector_selected_impl_body_count": 2,
        "bundle_selector_selected_linked_row_count": 1,
        "bundle_selector_selected_test_row_count": 0,
        "final_answer_context_from_selected_rows_only": True,
    }
    summary = build_bundle_selector_observability_summary(ctx)
    assert summary["used"] is True
    assert summary["skip_reason"] == ""
    assert summary["keep_ids"] == ["rc_0001", "rc_0002"]
    assert summary["dropped_ids_count"] == 1
    assert summary["selected_id_count"] == 2
    assert summary["selected_impl_body_count"] == 2
    assert summary["selected_linked_row_count"] == 1
    assert summary["selected_test_row_count"] == 0
    assert summary["final_answer_context_from_selected_rows_only"] is True

    ctx_skip = {"bundle_selector_used": False, "bundle_selector_skip_reason": "pool_too_small"}
    summary_skip = build_bundle_selector_observability_summary(ctx_skip)
    assert summary_skip["used"] is False
    assert summary_skip["skip_reason"] == "pool_too_small"
    assert summary_skip["keep_ids"] == []
    assert summary_skip["selected_id_count"] == 0


def test_build_bundle_selector_payload():
    """Payload includes question and pool summary."""
    state = AgentState(instruction="how X flows", current_plan={}, context={})
    step = {"description": "how X flows"}
    pool = [_pool_row("rc_0001", file="a.py", implementation_body_present=True)]
    payload = build_bundle_selector_payload(step, state, pool)
    assert "how X flows" in payload
    assert "rc_0001" in payload
    assert "[impl]" in payload


def test_payload_marks_test_rows_for_selector_preference():
    """Payload marks test files with [test] so selector can prefer impl over test."""
    state = AgentState(instruction="how X connects", current_plan={}, context={})
    step = {"description": "how X connects"}
    pool = [
        _pool_row("rc_0001", file="tests/test_foo.py", implementation_body_present=False),
        _pool_row("rc_0002", file="src/impl.py", implementation_body_present=True),
    ]
    payload = build_bundle_selector_payload(step, state, pool)
    assert "[test]" in payload
    assert "[impl]" in payload
    assert "prefer implementation-backed" in payload or "prefer non-test" in payload


# --- Phase 1, 2, 3, 4, 5 structural correctness tests ---


def test_architecture_never_zero_linked_rows(monkeypatch):
    """When selector omits all linked rows for architecture intent, link is injected at front."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    linked_row = _pool_row("rc_0001", relations=[{"kind": "import"}], implementation_body_present=True)
    impl_only = _pool_row("rc_0002", implementation_body_present=True)
    impl_only2 = _pool_row("rc_0003", implementation_body_present=True)
    pool = [linked_row, impl_only, impl_only2]

    state = AgentState(
        instruction="how does X connect to Y",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        # Selector returns only impl rows (no linked)
        mock_call.return_value = json.dumps({
            "keep_ids": ["rc_0002", "rc_0003"],
            "primary_ids": ["rc_0002"],
            "supporting_ids": ["rc_0003"],
            "reason": "impl only",
        })
        ok = run_bundle_selector(step, state)

    assert ok is True
    keep = state.context["bundle_selector_keep_ids"]
    assert "rc_0001" in keep
    # Injected linked row must be at front
    assert keep[0] == "rc_0001"
    assert state.context["bundle_selector_forced_link_injection"] is True
    assert state.context["bundle_selector_selected_linked_row_count"] >= 1


def test_forced_link_injection_ranked_best_row(monkeypatch):
    """_rank_linked_candidate picks richer linked row when multiple exist."""
    from agent.retrieval.bundle_selector import _rank_linked_candidate

    richer = {"relations": [{"a": 1}, {"b": 2}, {"c": 3}], "implementation_body_present": True, "final_score": 0.5}
    poorer = {"relations": [{"a": 1}], "implementation_body_present": False, "final_score": 0.9}
    assert _rank_linked_candidate(richer) > _rank_linked_candidate(poorer)
    candidates = [poorer, richer]
    candidates.sort(key=_rank_linked_candidate, reverse=True)
    assert candidates[0] == richer


def test_fallback_when_pool_has_no_linked_rows(monkeypatch):
    """When architecture intent but pool has no linked rows, fallback with explicit state."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    pool = [
        _pool_row("rc_0001", implementation_body_present=True),
        _pool_row("rc_0002", implementation_body_present=True),
        _pool_row("rc_0003", implementation_body_present=True),
    ]

    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({
            "keep_ids": ["rc_0001", "rc_0002"],
            "primary_ids": ["rc_0001"],
            "supporting_ids": ["rc_0002"],
            "reason": "impl only",
        })
        ok = run_bundle_selector(step, state)

    assert ok is False
    assert state.context.get("bundle_selector_used") is False
    assert state.context.get("bundle_selector_fallback_reason") == "no_linked_rows"


def test_constraint_violations_test_dominated():
    """_validate_selection_against_constraints returns test_dominated_context when test_count > impl_count."""
    rows = [
        {"file": "tests/test_x.py", "implementation_body_present": False},
        {"file": "tests/test_y.py", "implementation_body_present": False},
        {"file": "src/impl.py", "implementation_body_present": True},
    ]
    violations = _validate_selection_against_constraints(rows, "architecture")
    assert "test_dominated_context" in violations
    assert "missing_linked_row" in violations


def test_stub_respects_linked_priority_via_metadata():
    """Stub guarantees at least one linked row when linked rows exist in payload."""
    from tests.agent_eval.real_execution import _stub_bundle_selector

    prompt = """
Question: how does X connect to Y?

Candidate pool (choose IDs to keep for explanation context):
  rc_0001: src/impl_only.py foo (symbol) [impl]
    snippet: def impl...
  rc_0002: src/linked.py bar (file) [impl] [linked]
    snippet: import ...
  rc_0003: src/other.py baz (symbol)
    snippet: x = 1...
"""
    result = _stub_bundle_selector(prompt)
    data = json.loads(result)
    keep = data["keep_ids"]
    assert "rc_0002" in keep
    assert keep[0] == "rc_0002"


def test_architecture_safe_selection_rate_computed():
    """aggregate_retrieval_metrics computes architecture_safe_selection_rate correctly."""
    from tests.agent_eval.check_retrieval_quality import aggregate_retrieval_metrics

    recs = [
        {"task_id": "a", "tags": ["architecture"], "bundle_selector_used": True, "architecture_safe_selection": True},
        {"task_id": "b", "tags": ["architecture"], "bundle_selector_used": True, "architecture_safe_selection": False},
        {"task_id": "c", "tags": ["architecture"], "bundle_selector_used": False},
    ]
    agg = aggregate_retrieval_metrics(recs)
    assert agg["architecture_safe_selection_rate"] == 0.5


def test_arch_pool_lacks_linked_skips_selector(monkeypatch):
    """Phase 5: When architecture intent and pool has no linked rows, selector is skipped."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    pool = [
        _pool_row("rc_0001", implementation_body_present=True),
        _pool_row("rc_0002", implementation_body_present=True),
        _pool_row("rc_0003", implementation_body_present=True),
    ]
    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}
    assert should_use_bundle_selector(step, state, pool) is False
    assert state.context.get("bundle_selector_skip_reason") == "arch_pool_lacks_linked"


# --- Multi-link preference injection tests ---


def test_multi_link_injection_triggers(monkeypatch):
    """When pool has ≥2 linked rows and selection only picks 1, second linked row is injected."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    linked1 = _pool_row("rc_0001", relations=[{"kind": "import"}], file="src/a.py", implementation_body_present=True)
    linked2 = _pool_row("rc_0002", relations=[{"kind": "call"}], file="src/b.py", implementation_body_present=True)
    impl_only = _pool_row("rc_0003", implementation_body_present=True)
    pool = [linked1, linked2, impl_only]

    state = AgentState(
        instruction="how does X connect to Y",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        # Selector returns only 1 linked row
        mock_call.return_value = json.dumps({
            "keep_ids": ["rc_0001", "rc_0003"],
            "primary_ids": ["rc_0001"],
            "supporting_ids": ["rc_0003"],
            "reason": "one linked",
        })
        ok = run_bundle_selector(step, state)

    assert ok is True
    keep = state.context["bundle_selector_keep_ids"]
    assert "rc_0002" in keep
    assert state.context["bundle_selector_selected_linked_row_count"] >= 2


def test_multi_link_injection_flag(monkeypatch):
    """When multi-link injection triggers, bundle_selector_forced_multi_link_injection is True."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    linked1 = _pool_row("rc_0001", relations=[{}], file="src/a.py", implementation_body_present=True)
    linked2 = _pool_row("rc_0002", relations=[{}], file="src/b.py", implementation_body_present=True)
    pool = [linked1, linked2, _pool_row("rc_0003", implementation_body_present=True)]

    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({
            "keep_ids": ["rc_0001"],
            "primary_ids": ["rc_0001"],
            "supporting_ids": [],
            "reason": "single linked",
        })
        run_bundle_selector(step, state)

    assert state.context["bundle_selector_forced_multi_link_injection"] is True


def test_no_multi_link_injection_when_already_multiple(monkeypatch):
    """When 2+ linked rows already selected, no multi-link injection."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    linked1 = _pool_row("rc_0001", relations=[{}], file="src/a.py", implementation_body_present=True)
    linked2 = _pool_row("rc_0002", relations=[{}], file="src/b.py", implementation_body_present=True)
    pool = [linked1, linked2, _pool_row("rc_0003", implementation_body_present=True)]

    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({
            "keep_ids": ["rc_0001", "rc_0002"],
            "primary_ids": ["rc_0001"],
            "supporting_ids": ["rc_0002"],
            "reason": "both linked",
        })
        run_bundle_selector(step, state)

    assert state.context["bundle_selector_forced_multi_link_injection"] is False
    assert state.context["bundle_selector_selected_linked_row_count"] == 2


def test_structure_score_computation():
    """structure_score = linked_count + distinct_impl_files + (1 if linked connects to impl else 0)."""
    from tests.agent_eval.check_retrieval_quality import build_retrieval_quality_record

    class _Spec:
        task_id = "sq_test"
        tags = ("architecture",)
        instruction = "how does X connect"

    # Both rows have relations and file in selection -> useful links, linked_connects_to_impl=True
    selected_rows = [
        {"file": "src/a.py", "relations": [{}], "implementation_body_present": True},
        {"file": "src/b.py", "relations": [{}], "implementation_body_present": True},
    ]
    class _State:
        context = {
            "ranked_context": selected_rows,
            "retrieval_candidate_pool": selected_rows,
            "bundle_selector_used": True,
            "bundle_selector_keep_ids": ["rc_0001", "rc_0002"],
            "bundle_selector_selected_pool": selected_rows,
            "bundle_selector_selected_impl_body_count": 2,
            "bundle_selector_selected_linked_row_count": 2,
        }
        step_results = []

    rec = build_retrieval_quality_record(_Spec(), _State(), None)
    assert rec["structure_score"] is not None
    # 2 linked + 2 distinct_impl_files + 1 (linked_connects_to_impl)
    assert rec["structure_score"] == 2 + 2 + 1
    assert rec["useful_link_count"] == 2
    assert rec["isolated_link_count"] == 0


def test_validate_insufficient_multi_hop_structure():
    """_validate_selection_against_constraints returns insufficient_multi_hop_structure when 1 linked + 1 file."""
    rows = [
        {"file": "src/a.py", "relations": [{"kind": "import"}], "implementation_body_present": True},
    ]
    violations = _validate_selection_against_constraints(rows, "architecture")
    assert "insufficient_multi_hop_structure" in violations


def test_linked_row_connects_to_selected():
    """_linked_row_connects_to_selected returns True when relation targets selected file."""
    from agent.retrieval.bundle_selector import _linked_row_connects_to_selected

    selected = {"src/a.py", "src/b.py"}
    # Row's file in selection
    assert _linked_row_connects_to_selected({"file": "src/a.py", "relations": [{"kind": "x"}]}, selected) is True
    # Relation target_file in selection
    assert _linked_row_connects_to_selected(
        {"file": "x/y.py", "relations": [{"target_file": "src/a.py"}]}, selected
    ) is True
    # No connection
    assert _linked_row_connects_to_selected(
        {"file": "other/z.py", "relations": [{"target_file": "elsewhere.py"}]}, selected
    ) is False


def test_multi_link_injection_prefers_connecting_row(monkeypatch):
    """When injecting second linked row, prefer one that connects to already-selected files."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    # linked1 in a.py (selected), linked2 in c.py with relation to a.py (connects), linked3 in d.py (isolated)
    linked1 = _pool_row("rc_0001", relations=[{"kind": "import"}], file="src/a.py", implementation_body_present=True)
    linked2_connects = _pool_row("rc_0002", relations=[{"kind": "call", "target_file": "src/a.py"}], file="src/c.py")
    linked3_isolated = _pool_row("rc_0003", relations=[{"kind": "import", "target_file": "other/z.py"}], file="src/d.py")
    impl = _pool_row("rc_0004", implementation_body_present=True, file="src/b.py")
    pool = [linked1, linked2_connects, linked3_isolated, impl]

    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({
            "keep_ids": ["rc_0001", "rc_0004"],
            "primary_ids": ["rc_0001"],
            "supporting_ids": ["rc_0004"],
            "reason": "one linked",
        })
        ok = run_bundle_selector(step, state)

    assert ok is True
    keep = state.context["bundle_selector_keep_ids"]
    # Injected row should be rc_0002 (connects to a.py) not rc_0003 (isolated)
    assert "rc_0002" in keep
    assert keep[0] == "rc_0002"


# --- Bundle-level selection tests (ENABLE_BUNDLE_SELECTION) ---


def test_selector_prefers_single_coherent_bundle(monkeypatch):
    """When ENABLE_BUNDLE_SELECTION, selection from one bundle produces expected observability."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("ENABLE_BUNDLE_SELECTION", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    pool = [
        _pool_row("rc_0001", file="engine.py", relations=[{"kind": "import", "target_file": "impl.py"}]),
        _pool_row("rc_0002", file="impl.py"),
        _pool_row("rc_0003", file="other.py"),
    ]
    state = AgentState(
        instruction="how does engine connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does engine connect"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({
            "keep_ids": ["rc_0001", "rc_0002"],
            "primary_ids": ["rc_0001"],
            "supporting_ids": ["rc_0002"],
            "reason": "coherent bundle",
        })
        ok = run_bundle_selector(step, state)

    assert ok is True
    assert "bundle_selector_selected_bundle_ids" in state.context
    assert "bundle_selector_bundle_count" in state.context
    assert "bundle_selector_cross_bundle" in state.context
    assert "bundle_selector_bridge_selected" in state.context


def test_selector_injects_missing_bundle_rows(monkeypatch):
    """Fragmented selection triggers injection from best bundle or bridge."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("ENABLE_BUNDLE_SELECTION", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    linked1 = _pool_row("rc_0001", relations=[{"kind": "import"}], file="a.py", implementation_body_present=True)
    linked2 = _pool_row("rc_0002", relations=[{"kind": "call"}], file="b.py", implementation_body_present=True)
    pool = [linked1, linked2, _pool_row("rc_0003", implementation_body_present=True)]

    state = AgentState(
        instruction="how does X connect",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does X connect"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({
            "keep_ids": ["rc_0003"],
            "primary_ids": ["rc_0003"],
            "supporting_ids": [],
            "reason": "fragmented",
        })
        ok = run_bundle_selector(step, state)

    assert ok is True
    keep = state.context["bundle_selector_keep_ids"]
    assert "rc_0001" in keep or "rc_0002" in keep


def test_bundle_observability_fields_present(monkeypatch):
    """bundle_selector_selected_bundle_ids, bundle_count, cross_bundle, bridge_selected set correctly."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("ENABLE_BUNDLE_SELECTION", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    pool = [
        _pool_row("rc_0001", file="a.py", relations=[{"kind": "import", "target_file": "b.py"}]),
        _pool_row("rc_0002", file="b.py"),
        _pool_row("rc_0003", file="c.py"),
    ]
    state = AgentState(
        instruction="how does a connect to b",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does a connect to b"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({
            "keep_ids": ["rc_0001", "rc_0002"],
            "primary_ids": ["rc_0001"],
            "supporting_ids": ["rc_0002"],
            "reason": "single bundle",
        })
        run_bundle_selector(step, state)

    assert isinstance(state.context.get("bundle_selector_selected_bundle_ids"), list)
    assert isinstance(state.context.get("bundle_selector_bundle_count"), int)
    assert isinstance(state.context.get("bundle_selector_cross_bundle"), bool)
    assert isinstance(state.context.get("bundle_selector_bridge_selected"), bool)


def test_selector_handles_two_connected_bundles(monkeypatch):
    """Entrypoint + settings bundles: selection can span 2 bundles; no blind collapse."""
    monkeypatch.setenv("ENABLE_LLM_BUNDLE_SELECTOR", "1")
    monkeypatch.setenv("ENABLE_BUNDLE_SELECTION", "1")
    monkeypatch.setenv("FORCE_SELECTOR_IN_EVAL", "0")
    import importlib
    import config.retrieval_config as rc

    importlib.reload(rc)

    pool = [
        _pool_row("rc_0001", file="engine.py", relations=[{"kind": "import", "target_file": "impl.py"}]),
        _pool_row("rc_0002", file="impl.py"),
        _pool_row("rc_0003", file="settings.py", relations=[{"kind": "import", "target_file": "config.py"}]),
        _pool_row("rc_0004", file="config.py"),
    ]
    state = AgentState(
        instruction="how does entrypoint connect to settings",
        current_plan={"plan_id": "x", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": pool,
            "retrieval_intent": "architecture",
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does entrypoint connect to settings"}

    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({
            "keep_ids": ["rc_0001", "rc_0002", "rc_0003", "rc_0004"],
            "primary_ids": ["rc_0001", "rc_0003"],
            "supporting_ids": ["rc_0002", "rc_0004"],
            "reason": "both bundles",
        })
        ok = run_bundle_selector(step, state)

    assert ok is True
    keep = state.context["bundle_selector_keep_ids"]
    assert len(keep) >= 2
