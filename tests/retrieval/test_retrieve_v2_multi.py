"""Tests for ``retrieve`` (batch vector + mandatory ``rerank_batch``)."""

from __future__ import annotations

import os

import pytest

from agent.retrieval.retrieval_pipeline_v2 import _V2PreRerank, retrieve, retrieve_v2, retrieve_v2_multi
from agent.retrieval.candidate_schema import RetrievalInput
from config.retrieval_config import (
    V2_MAX_SNIPPETS,
    V2_RRF_K,
    V2_RRF_TOP_N,
    V2_TOP_K_PER_SOURCE,
    get_retrieval_extra_roots,
)


def _inp(q: str) -> RetrievalInput:
    extras = get_retrieval_extra_roots()
    return RetrievalInput(
        query=q,
        project_root=None,
        extra_project_roots=extras if extras else None,
        top_k_per_source=V2_TOP_K_PER_SOURCE,
        rrf_top_n=V2_RRF_TOP_N,
        rrf_k=V2_RRF_K,
        max_snippets=V2_MAX_SNIPPETS,
    )


def test_retrieve_alias_is_retrieve_v2_multi() -> None:
    assert retrieve is retrieve_v2_multi


def test_retrieve_v2_raises_without_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_RETRIEVE_V2_LEGACY", raising=False)
    with pytest.raises(RuntimeError, match="retrieve_v2 is deprecated"):
        retrieve_v2(_inp("x"), state=None)


def test_retrieve_v2_legacy_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_RETRIEVE_V2_LEGACY", "1")
    from agent.retrieval.candidate_schema import RetrievalOutput

    monkeypatch.setattr(
        "agent.retrieval.retrieval_pipeline_v2.retrieve",
        lambda *_a, **_k: [RetrievalOutput(candidates=[], query="def foo", warnings=[])],
    )
    out = retrieve_v2(_inp("def foo"), state=None)
    assert out.query == "def foo"
    assert out.candidates == []


def test_retrieve_single_query_uses_batch_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """One query still uses search_batch + rerank_batch (no single-query rerank path)."""
    monkeypatch.delenv("RETRIEVAL_EXTRA_PROJECT_ROOTS", raising=False)

    stages = {"pre_rrf": {}}

    def _fake_pre(inp, state=None, vector_rows_override=None):
        rows = [
            {
                "snippet": f"snip{inp.query}{j}",
                "file": "f.py",
                "symbol": "",
                "source": "bm25",
                "line": 1,
                "metadata": {},
            }
            for j in range(8)
        ]
        return (stages, _V2PreRerank(rerank_input=rows, tail=[]), [])

    monkeypatch.setattr(
        "agent.retrieval.retrieval_pipeline_v2._retrieve_v2_pre_only",
        _fake_pre,
    )
    monkeypatch.setattr(
        "agent.retrieval.vector_retriever.search_batch",
        lambda *_a, **_k: [{"results": []}],
    )

    class _R:
        def __init__(self) -> None:
            self.batch_calls = 0
            self.rerank_calls = 0

        def rerank_batch(self, reqs):
            self.batch_calls += 1
            return [[(d, 0.5) for d in docs] for _q, docs in reqs]

        def rerank(self, query: str, docs: list[str]):
            self.rerank_calls += 1
            return [(d, 0.5) for d in docs]

    rr = _R()
    monkeypatch.setattr(
        "agent.retrieval.reranker.reranker_factory.create_reranker",
        lambda: rr,
    )

    out = retrieve(["only"], state=None, project_root="/tmp")
    assert len(out) == 1
    assert rr.batch_calls == 1
    assert rr.rerank_calls == 0


def test_retrieve_two_queries_one_rerank_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RETRIEVAL_EXTRA_PROJECT_ROOTS", raising=False)

    stages = {"pre_rrf": {}}

    def _fake_pre(inp, state=None, vector_rows_override=None):
        rows = [
            {
                "snippet": f"snip{inp.query}{j}",
                "file": "f.py",
                "symbol": "",
                "source": "bm25",
                "line": 1,
                "metadata": {},
            }
            for j in range(8)
        ]
        return (stages, _V2PreRerank(rerank_input=rows, tail=[]), [])

    monkeypatch.setattr(
        "agent.retrieval.retrieval_pipeline_v2._retrieve_v2_pre_only",
        _fake_pre,
    )
    monkeypatch.setattr(
        "agent.retrieval.vector_retriever.search_batch",
        lambda *_a, **_k: [{"results": []}, {"results": []}],
    )

    class _R:
        def __init__(self) -> None:
            self.batch_calls = 0

        def rerank_batch(self, reqs):
            self.batch_calls += 1
            return [[(d, 0.5) for d in docs] for _q, docs in reqs]

        def rerank(self, query: str, docs: list[str]):
            raise AssertionError("single-query rerank must not be called")

    rr = _R()
    monkeypatch.setattr(
        "agent.retrieval.reranker.reranker_factory.create_reranker",
        lambda: rr,
    )

    out = retrieve(["q1", "q2"], state=None, project_root="/tmp")
    assert len(out) == 2
    assert rr.batch_calls == 1


def test_pre_only_contract_three_tuple(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RETRIEVAL_EXTRA_PROJECT_ROOTS", raising=False)
    monkeypatch.setattr(
        "agent.retrieval.vector_retriever.search_batch",
        lambda *_a, **_k: [{"results": []}],
    )

    def _bad_pre(*_a, **_k):
        return ({}, None)  # type: ignore[return-value]

    monkeypatch.setattr(
        "agent.retrieval.retrieval_pipeline_v2._retrieve_v2_pre_only",
        _bad_pre,
    )

    with pytest.raises(RuntimeError, match="3-tuple"):
        retrieve(["x"], state=None, project_root="/tmp")


def test_vector_batch_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RETRIEVAL_EXTRA_PROJECT_ROOTS", raising=False)

    def _boom(*_a, **_k):
        raise RuntimeError("vector failed")

    monkeypatch.setattr(
        "agent.retrieval.vector_retriever.search_batch",
        _boom,
    )

    with pytest.raises(RuntimeError, match="vector failed"):
        retrieve(["a"], state=None, project_root="/tmp")


def test_dispatcher_search_multi_returns_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RETRIEVAL_EXTRA_PROJECT_ROOTS", raising=False)
    os.environ.setdefault("RETRIEVAL_REMOTE_FIRST", "0")

    import agent.execution.react_schema  # noqa: F401 — initializes ReAct tool registry

    def _fake_retrieve(
        queries: list[str],
        state=None,
        project_root: str | None = None,
    ):
        from agent.retrieval.candidate_schema import RetrievalOutput  # noqa: PLC0415

        return [
            RetrievalOutput(candidates=[], query=q, warnings=[])
            for q in queries
        ]

    monkeypatch.setattr(
        "agent.retrieval.retrieval_pipeline_v2.retrieve",
        _fake_retrieve,
    )

    from agent.tools.react_tools import register_all_tools
    from agent_v2.runtime.dispatcher import Dispatcher
    from agent.execution.step_dispatcher import _dispatch_react

    register_all_tools()

    class _S:
        context = {"project_root": os.getcwd()}

    d = Dispatcher(execute_fn=_dispatch_react)
    step = {
        "id": "t_multi",
        "action": "SEARCH",
        "_react_action_raw": "search_multi",
        "_react_args": {"queries": ["foo", "bar"]},
    }
    res = d.execute(step, _S())
    assert isinstance(res, list)
    assert len(res) == 2
    assert all(getattr(x, "success", False) for x in res)
