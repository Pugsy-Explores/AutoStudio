"""Retrieval intent heuristic bias (Phase D)."""

from __future__ import annotations

from agent.retrieval.retrieval_intent import (
    INTENT_ARCHITECTURE,
    INTENT_FILE,
    INTENT_GENERIC,
    INTENT_REGION,
    INTENT_SYMBOL,
    apply_intent_bias,
    classify_query_intent,
)


def test_classify_file():
    assert classify_query_intent("where is the settings module path") == INTENT_FILE


def test_classify_symbol():
    assert classify_query_intent("which function handles the loader") == INTENT_SYMBOL


def test_classify_region():
    assert classify_query_intent("what is the fallback branch when timeout") == INTENT_REGION


def test_classify_architecture():
    assert classify_query_intent("how does the entry point connect to settings wiring") == INTENT_ARCHITECTURE


def test_classify_generic():
    assert classify_query_intent("ok") == INTENT_GENERIC


def test_apply_intent_boost_file():
    cands = [
        {"file": "a.py", "candidate_kind": "file", "retriever_score": 1.0},
        {"file": "b.py", "candidate_kind": "symbol", "retriever_score": 1.0},
    ]
    out = apply_intent_bias(cands, "show me the config file path")
    f_row = next(x for x in out if x.get("candidate_kind") == "file")
    s_row = next(x for x in out if x.get("candidate_kind") == "symbol")
    assert f_row.get("intent_boost", 0) >= s_row.get("intent_boost", 0)
    assert f_row["selection_score"] >= f_row.get("retriever_score", 0)


def test_apply_intent_architecture_boosts_linked():
    cands = [
        {
            "file": "a.py",
            "candidate_kind": "symbol",
            "retriever_score": 1.0,
            "relations": [{"kind": "ownership", "target_file": "a.py"}],
        },
        {"file": "b.py", "candidate_kind": "symbol", "retriever_score": 1.0},
    ]
    out = apply_intent_bias(cands, "how does X connect to Y flow")
    linked = out[0]
    unlinked = out[1]
    assert (linked.get("intent_boost") or 0) > (unlinked.get("intent_boost") or 0)


def test_short_query_no_boost_delta():
    cands = [{"file": "a.py", "candidate_kind": "file", "retriever_score": 2.0}]
    out = apply_intent_bias(cands, "a b")  # 2 tokens
    assert out[0].get("intent_boost", 0) == 0.0
