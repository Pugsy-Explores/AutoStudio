"""Answer synthesis V1: coverage derivation, prompt load, synthesize with mocked LLM."""

from __future__ import annotations

import json

import pytest

from agent.prompt_system.registry import get_registry
from agent_v2.exploration import answer_synthesizer as asn_mod
from agent_v2.exploration.answer_synthesizer import (
    ANSWER_SYNTHESIS_TASK,
    maybe_synthesize_to_state,
    synthesize_answer,
)
from agent_v2.schemas.answer_synthesis import AnswerSynthesisInput, derive_answer_synthesis_coverage
from agent_v2.schemas.exploration import (
    ExplorationContent,
    ExplorationItem,
    ExplorationItemMetadata,
    ExplorationRelevance,
    ExplorationResultMetadata,
    ExplorationSource,
    ExplorationSummary,
)
from agent_v2.schemas.final_exploration import ExplorationAdapterTrace, FinalExplorationSchema


def _item(i: int, *, score: float = 0.9) -> ExplorationItem:
    return ExplorationItem(
        item_id=f"e{i}",
        type="file",
        source=ExplorationSource(ref=f"src/f{i}.py"),
        content=ExplorationContent(summary="s", key_points=[], entities=[]),
        relevance=ExplorationRelevance(score=score, reason="r"),
        metadata=ExplorationItemMetadata(timestamp="t", tool_name="open_file"),
        snippet="def foo(): pass",
        read_source="symbol",
    )


def _final(
    *,
    evidence: list,
    confidence: str = "high",
    completion_status: str = "complete",
    termination_reason: str = "mapper_stop",
    gaps: list | None = None,
    gap_reason: str | None = None,
) -> FinalExplorationSchema:
    if gaps is None:
        gaps = []
    if not gaps:
        kr = gap_reason or "none"
    else:
        kr = None
    return FinalExplorationSchema(
        exploration_id="t",
        instruction="How does X work?",
        status="complete",
        evidence=evidence,
        relationships=[],
        exploration_summary=ExplorationSummary(
            overall="o",
            key_findings=["a"],
            knowledge_gaps=gaps,
            knowledge_gaps_empty_reason=kr,
        ),
        metadata=ExplorationResultMetadata(
            total_items=len(evidence),
            created_at="2026-01-01T00:00:00Z",
            completion_status=completion_status,  # type: ignore[arg-type]
            termination_reason=termination_reason,
        ),
        confidence=confidence,  # type: ignore[arg-type]
        trace=ExplorationAdapterTrace(llm_used=False, synthesis_success=False),
    )


def test_derive_weak_no_evidence() -> None:
    ex = _final(evidence=[])
    assert derive_answer_synthesis_coverage(ex) == "weak"


def test_derive_weak_stalled() -> None:
    ex = _final(evidence=[_item(1)], termination_reason="stalled")
    assert derive_answer_synthesis_coverage(ex) == "weak"


def test_derive_partial_gaps() -> None:
    ex = _final(evidence=[_item(1), _item(2)], gaps=["need Y"], gap_reason=None)
    assert derive_answer_synthesis_coverage(ex) == "partial"


def test_derive_partial_medium_confidence() -> None:
    ex = _final(evidence=[_item(1), _item(2)], confidence="medium")
    assert derive_answer_synthesis_coverage(ex) == "partial"


def test_derive_sufficient() -> None:
    ex = _final(
        evidence=[_item(1), _item(2)],
        confidence="high",
        completion_status="complete",
        termination_reason="mapper_stop",
    )
    assert derive_answer_synthesis_coverage(ex) == "sufficient"


def test_answer_synthesis_prompt_renders() -> None:
    ex = _final(evidence=[_item(1)])
    inp = AnswerSynthesisInput.from_exploration(ex)
    variables = asn_mod._build_prompt_variables(inp)
    sys_p, usr = get_registry().render_prompt_parts(
        "answer_synthesis",
        version="latest",
        variables=variables,
        model_name="qwen2.5-coder-7b",
    )
    assert "INSTRUCTION:" in usr
    assert "KEY_FINDINGS:" in usr
    assert "EVIDENCE:" in usr
    assert "COVERAGE" in usr
    assert "weak" in usr or "partial" in usr or "sufficient" in usr
    assert "file:" in usr and "summary:" in usr
    assert "Answer Synthesizer" in sys_p
    assert len(sys_p) > 50


def test_synthesize_disabled_returns_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asn_mod, "ENABLE_ANSWER_SYNTHESIS", False)
    ex = _final(evidence=[_item(1)])
    r = synthesize_answer(ex)
    assert r.synthesis_success is False
    assert r.error == "answer_synthesis_disabled"


def test_synthesize_parses_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asn_mod, "ENABLE_ANSWER_SYNTHESIS", True)

    def _fake_llm(*_a, **_k):
        return """
Answer:
It works by Z.

Explanation:
The code does Y.

Evidence:
- file: src/f1.py
  item_id: e1

Gaps:
None

Confidence:
high
"""

    monkeypatch.setattr(asn_mod, "call_reasoning_model", _fake_llm)
    ex = _final(evidence=[_item(1)])
    r = synthesize_answer(ex)
    assert r.synthesis_success is True
    assert "Z" in r.direct_answer
    assert r.citations and r.citations[0].item_id == "e1"


def test_synthesize_json_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asn_mod, "ENABLE_ANSWER_SYNTHESIS", True)

    def _fake_llm(*_a, **_k):
        return json.dumps(
            {
                "direct_answer": "JSON path.",
                "structured_explanation": "x",
                "citations": [{"item_id": "e1", "file": "src/f1.py", "symbol": ""}],
                "uncertainty": None,
            }
        )

    monkeypatch.setattr(asn_mod, "call_reasoning_model", _fake_llm)
    ex = _final(evidence=[_item(1)])
    r = synthesize_answer(ex)
    assert r.synthesis_success is True
    assert "JSON path" in r.direct_answer


def test_maybe_synthesize_to_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asn_mod, "ENABLE_ANSWER_SYNTHESIS", True)
    monkeypatch.setattr(
        asn_mod,
        "call_reasoning_model",
        lambda *a, **k: "Answer:\nhello\n\nExplanation:\nx\n\nEvidence:\n\nGaps:\n\nConfidence:\nhigh\n",
    )
    st = type("S", (), {"context": {}})()
    ex = _final(evidence=[_item(1)])
    maybe_synthesize_to_state(st, ex, None)
    assert "answer_synthesis" in st.context
    assert st.context.get("final_answer") == "hello"


def test_evidence_capped_at_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asn_mod, "ANSWER_SYNTHESIS_MAX_EVIDENCE_ITEMS", 3)
    ev = [_item(i, score=(9 - i) / 9.0) for i in range(9)]
    ex = _final(evidence=ev)
    inp = AnswerSynthesisInput.from_exploration(ex)
    block = asn_mod._evidence_structured_block(inp)
    assert block.count("- file:") == 3


def test_task_model_registered() -> None:
    from agent.models.model_config import get_model_for_task, get_model_call_params

    assert get_model_for_task(ANSWER_SYNTHESIS_TASK) == "REASONING"
    p = get_model_call_params(ANSWER_SYNTHESIS_TASK)
    assert "max_tokens" in p or "temperature" in p
