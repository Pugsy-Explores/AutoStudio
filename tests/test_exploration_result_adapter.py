"""Hybrid exploration result adapter — deterministic path + optional synthesis."""

from __future__ import annotations

import inspect

from agent_v2.exploration.exploration_engine_v2 import ExplorationEngineV2
from agent_v2.exploration.exploration_llm_synthesizer import apply_optional_llm_synthesis
from agent_v2.exploration.exploration_result_adapter import (
    ExplorationResultAdapter,
    project_discrete_confidence,
    source_summary_from_items,
)
from agent_v2.exploration.exploration_working_memory import ExplorationWorkingMemory
from agent_v2.schemas.exploration import ExplorationItem, ExplorationSource
from agent_v2.schemas.final_exploration import FinalExplorationSchema


def _minimal_items() -> list[ExplorationItem]:
    from agent_v2.schemas.exploration import (
        ExplorationContent,
        ExplorationItemMetadata,
        ExplorationRelevance,
    )

    return [
        ExplorationItem(
            item_id="item_1",
            type="file",
            source=ExplorationSource(ref="a.py", location=None),
            content=ExplorationContent(summary="x", key_points=["x"], entities=["a.py"]),
            relevance=ExplorationRelevance(score=0.8, reason="ok"),
            metadata=ExplorationItemMetadata(timestamp="t", tool_name="read_snippet"),
        )
    ]


def test_items_equal_evidence_single_mapping():
    mem = ExplorationWorkingMemory()
    mem.add_evidence(
        "foo",
        "src/x.py",
        (1, 5),
        "hello",
        snippet="s",
        read_source="symbol",
        confidence=0.9,
        source="analyzer",
        tier=0,
    )
    mem.add_relationships_from_expand(
        "src/x.py",
        "foo",
        {"callers": [{"file_path": "b.py", "symbol": "g"}]},
    )
    final = ExplorationResultAdapter.build(
        mem,
        "find foo",
        completion_status="complete",
        termination_reason="primary_symbol_sufficient",
        explored_files=1,
        explored_symbols=1,
    )
    assert len(final.evidence) >= 1
    assert len(final.relationships) == 1
    assert final.relationships[0].from_key.endswith("x.py::foo")
    assert final.confidence == "high"
    assert final.trace.llm_used is False
    assert final.trace.synthesis_success is False
    FinalExplorationSchema.model_validate(final.model_dump())


def test_project_discrete_confidence():
    assert project_discrete_confidence(
        completion_status="complete",
        termination_reason="pending_exhausted",
        n_items=2,
    ) == "high"
    assert project_discrete_confidence(
        completion_status="incomplete",
        termination_reason="stalled",
        n_items=1,
    ) == "low"
    assert project_discrete_confidence(
        completion_status="incomplete",
        termination_reason="max_steps",
        n_items=2,
    ) == "medium"


def test_build_result_delegates_to_adapter_only():
    src = inspect.getsource(ExplorationEngineV2._build_result_from_memory)
    assert "ExplorationResultAdapter.build" in src
    assert "ExplorationItem(" not in src


def test_llm_synthesis_success(monkeypatch):
    mem = ExplorationWorkingMemory()
    mem.add_evidence(
        None,
        "a.py",
        (1, 2),
        "alpha",
        confidence=0.9,
        source="analyzer",
        tier=0,
    )
    mem.add_evidence(
        None,
        "b.py",
        (1, 2),
        "beta",
        confidence=0.9,
        source="analyzer",
        tier=0,
    )
    final = ExplorationResultAdapter.build(
        mem,
        "instr",
        completion_status="incomplete",
        termination_reason="max_steps",
        explored_files=1,
        explored_symbols=1,
    )

    def _llm_ok(prompt: str) -> str:
        return (
            '{"key_insights": ["one insight here", "two insight here"], '
            '"objective_coverage": "partial coverage ok"}'
        )

    out = apply_optional_llm_synthesis(final, mem, "instr", _llm_ok)
    assert out.trace.llm_used is True
    assert out.trace.synthesis_success is True
    assert len(out.key_insights) == 2
    assert out.objective_coverage
    assert out.evidence == final.evidence
    assert all(a is b for a, b in zip(out.evidence, final.evidence))


def test_llm_synthesis_failure_keeps_factuals():
    mem = ExplorationWorkingMemory()
    mem.add_evidence(
        None,
        "a.py",
        (1, 2),
        "alpha",
        confidence=0.9,
        source="analyzer",
        tier=0,
    )
    final = ExplorationResultAdapter.build(
        mem,
        "instr",
        completion_status="complete",
        termination_reason="pending_exhausted",
        explored_files=1,
        explored_symbols=1,
    )
    ki_before = list(final.key_insights)

    def _llm_bad(_: str) -> str:
        return "not json"

    out = apply_optional_llm_synthesis(final, mem, "instr", _llm_bad)
    assert out.trace.synthesis_success is False
    assert out.key_insights == ki_before
    assert out.evidence == final.evidence
    assert all(a is b for a, b in zip(out.evidence, final.evidence))


def test_source_summary_from_items_symbol_line():
    items = _minimal_items()
    items[0].read_source = "symbol"
    assert source_summary_from_items(items)["symbol"] == 1
