"""Tests for selector candidate pool: stable IDs, typed metadata, guardrails, ranked_context unchanged."""

import pytest

from agent.memory.state import AgentState
from agent.retrieval.result_contract import (
    RETRIEVAL_RESULT_TYPE_REGION_BODY,
    RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
)
from agent.retrieval.retrieval_intent import INTENT_ARCHITECTURE
from agent.retrieval.selector_candidate_pool import (
    apply_selector_pool_guardrails,
    assign_stable_candidate_ids,
    build_selector_candidate_pool,
)


def _row(overrides=None):
    base = {
        "file": "a.py",
        "symbol": "foo",
        "snippet": "def foo",
        "candidate_kind": "symbol",
        "selection_score": 0.5,
    }
    base.update(overrides or {})
    return base


def test_pool_has_stable_candidate_ids():
    """Pool is created with stable candidate_id values (rc_0001, rc_0002, ...)."""
    pool = [{"x": 1}, {"x": 2}, {"x": 3}]
    out = assign_stable_candidate_ids(pool)
    assert [r["candidate_id"] for r in out] == ["rc_0001", "rc_0002", "rc_0003"]
    assert out[0]["x"] == 1
    assert out[1]["x"] == 2


def test_typed_metadata_survives_into_pool():
    """Typed metadata (retrieval_result_type, implementation_body_present, etc.) survives into pool."""
    candidates = [
        _row({
            "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
            "implementation_body_present": True,
            "line": 10,
            "line_range": [10, 20],
            "enclosing_class": "Bar",
            "relations": [{"kind": "call", "target_file": "b.py", "target_symbol": "baz"}],
        }),
    ]
    out = apply_selector_pool_guardrails(
        candidates,
        max_size=12,
        min_size=4,
        intent="generic",
    )
    assert len(out) == 1
    r = out[0]
    assert r["candidate_id"] == "rc_0001"
    assert r["retrieval_result_type"] == RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
    assert r["implementation_body_present"] is True
    assert r["line"] == 10
    assert r["line_range"] == [10, 20]
    assert r["enclosing_class"] == "Bar"
    assert len(r["relations"]) == 1
    assert r["relations"][0]["kind"] == "call"


def test_placeholder_only_rows_dropped_when_impl_exists():
    """Placeholder-only rows are dropped when real impl rows exist."""
    impl = _row({
        "file": "impl.py",
        "symbol": "real",
        "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
        "implementation_body_present": True,
    })
    placeholder1 = _row({
        "file": "stub.py",
        "symbol": "stub",
        "candidate_kind": "file",
        "retrieval_result_type": "file_header",
    })
    placeholder2 = _row({
        "file": "graph.py",
        "symbol": "graph_stub",
        "candidate_kind": "file",
    })
    candidates = [impl, placeholder1, placeholder2]
    out = apply_selector_pool_guardrails(
        candidates,
        max_size=12,
        min_size=4,
        intent="generic",
    )
    assert len(out) == 1
    assert out[0]["file"] == "impl.py"
    assert out[0]["implementation_body_present"] is True


def test_exact_duplicates_removed():
    """Exact duplicates (by retrieval_row_identity_key) are removed."""
    dup = _row({"file": "a.py", "symbol": "foo", "snippet": "def foo"})
    candidates = [dup, dict(dup), dict(dup)]
    out = apply_selector_pool_guardrails(
        candidates,
        max_size=12,
        min_size=4,
        intent="generic",
    )
    assert len(out) == 1
    assert out[0]["candidate_id"] == "rc_0001"


def test_placeholder_rows_kept_when_no_impl():
    """Placeholder-only rows are kept when no impl rows exist."""
    candidates = [
        _row({"file": "a.py", "candidate_kind": "file", "retrieval_result_type": "file_header"}),
        _row({"file": "b.py", "candidate_kind": "file"}),
    ]
    out = apply_selector_pool_guardrails(
        candidates,
        max_size=12,
        min_size=4,
        intent="generic",
    )
    assert len(out) == 2
    assert out[0]["file"] == "a.py"
    assert out[1]["file"] == "b.py"


def test_pool_capped_to_max():
    """Pool is capped to MAX_SELECTOR_CANDIDATE_POOL."""
    candidates = [_row({"file": f"f{i}.py", "symbol": f"s{i}"}) for i in range(20)]
    out = apply_selector_pool_guardrails(
        candidates,
        max_size=5,
        min_size=4,
        intent="generic",
    )
    assert len(out) == 5
    assert out[0]["candidate_id"] == "rc_0001"
    assert out[4]["candidate_id"] == "rc_0005"


def test_architecture_linked_rows_survive_capping():
    """Architecture-style linked rows survive capping when available."""
    linked1 = _row({
        "file": "a.py",
        "symbol": "a",
        "relations": [{"kind": "import", "target_file": "b.py"}],
        "selection_score": 0.3,
    })
    linked2 = _row({
        "file": "b.py",
        "symbol": "b",
        "relations": [{"kind": "call", "target_file": "a.py"}],
        "selection_score": 0.2,
    })
    unlinked = [_row({"file": f"x{i}.py", "symbol": f"s{i}"}) for i in range(5)]
    # Put linked at end; for architecture intent they get sorted first
    candidates = unlinked + [linked1, linked2]
    out = apply_selector_pool_guardrails(
        candidates,
        max_size=5,
        min_size=4,
        intent=INTENT_ARCHITECTURE,
    )
    files_in = [r["file"] for r in out]
    # Linked rows are preferred when architecture intent; at least one should survive
    assert "a.py" in files_in or "b.py" in files_in
    linked_count = sum(1 for r in out if r.get("relations"))
    assert linked_count >= 1


def test_build_selector_pool_sets_observability():
    """build_selector_candidate_pool sets retrieval_candidate_pool_* in state.context."""
    state = AgentState(
        instruction="test",
        current_plan={"plan_id": "x", "steps": []},
        context={"project_root": "/tmp"},
    )
    # Two impl-backed rows so both survive (no placeholder drop)
    candidates = [
        _row({"file": "a.py", "symbol": "a", "implementation_body_present": True}),
        _row({"file": "b.py", "symbol": "b", "implementation_body_present": True, "relations": [{"kind": "call"}]}),
    ]
    build_selector_candidate_pool(
        state, candidates, "generic",
        max_size=12, min_size=4,
    )
    assert "retrieval_candidate_pool" in state.context
    assert state.context["retrieval_candidate_pool_count"] == 2
    assert state.context["retrieval_candidate_pool_has_impl"] is True
    assert state.context["retrieval_candidate_pool_linked_count"] == 1
    assert state.context["retrieval_candidate_pool"][0]["candidate_id"] == "rc_0001"


def test_ranked_context_unchanged_when_selector_off():
    """
    ranked_context behavior remains unchanged: selector pool is additive only.
    build_selector_candidate_pool does not modify ranked_context; it only sets
    retrieval_candidate_pool*. ranked_context is always built by prune_context.
    """
    state = AgentState(
        instruction="Explain foo",
        current_plan={"plan_id": "x", "steps": []},
        context={"project_root": "/tmp", "ranked_context": [{"file": "a.py", "snippet": "existing"}]},
    )
    build_selector_candidate_pool(
        state, [_row({"file": "b.py"})], "generic",
        max_size=12, min_size=4,
    )
    # ranked_context untouched by selector pool building
    assert state.context["ranked_context"] == [{"file": "a.py", "snippet": "existing"}]
    # Pool is additive
    assert len(state.context["retrieval_candidate_pool"]) == 1
