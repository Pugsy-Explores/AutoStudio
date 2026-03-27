from __future__ import annotations

from agent_v2.exploration import exploration_behavior_eval_harness as harness
from tests.fixtures.exploration_behavior_eval_cases import build_eval_suites


def test_exploration_behavior_eval_suite_live_judge_integration(monkeypatch):
    monkeypatch.setattr(
        harness,
        "call_reasoning_model",
        lambda **kwargs: (
            '{"semantic_alignment":"correct","decision_quality":"good","loop_behavior":"efficient",'
            '"gap_handling":"resolved","final_verdict":"pass","reason":"behavior aligns with expected patterns"}'
        ),
    )
    monkeypatch.setattr(harness, "get_model_call_params", lambda _task: {"temperature": 0.0})

    suites = build_eval_suites()
    all_cases = []
    for v in suites.values():
        all_cases.extend(v)
    out = harness.run_eval_suite(all_cases)
    assert out["summary"]["total_cases"] == 12
    assert out["summary"]["passed_cases"] == 12
    assert all(row["final_case_pass"] for row in out["cases"])
