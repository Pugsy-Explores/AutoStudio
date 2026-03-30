"""
Replanner regression suite — 12 scenarios that every replanner prompt change must pass.

Baseline (v2 contract):
  - Generic leakage: ≤ 3/12
  - BUILD_CONTEXT present: 12/12
  - No direct SEARCH→EXPLAIN: 12/12
  - SEARCH diversity: 12/12
  - single_search_valid_rate: 100%
  - over_expansion_rate: < 20%

Run: pytest tests/test_replanner_regression_suite.py -v -m replanner_regression
Requires network (live LLM calls). Marked slow.
"""

import pytest

from agent.memory.state import AgentState
from agent.memory.step_result import StepResult
from agent.orchestrator.replanner import replan

# --- Helpers ---


def _mk_state(instruction: str, steps: list, step_results: list, ctx_extra: dict | None = None) -> AgentState:
    ctx = {"dominant_artifact_mode": "code", "lane_violations": []}
    if ctx_extra:
        ctx.update(ctx_extra)
    return AgentState(
        instruction=instruction,
        current_plan={"plan_id": "p1", "steps": steps},
        completed_steps=[],
        step_results=step_results,
        context=ctx,
    )


def _weak(mode: str = "need_implementation_search") -> dict:
    return {
        "search_quality": "weak",
        "replan_recovery_history": [
            {"failed_action": "EXPLAIN", "error_signal": "insufficient_substantive_context", "recovery_mode": mode}
        ],
    }


# Generic concepts that must NOT appear when instruction is about something else
_GENERIC_LEAK = ("entrypoint", "main", "cli", "run", "__main__")
# Instructions where generic concepts ARE relevant
_ENTRYPOINT_RELEVANT = ("where does the application start", "main", "cli", "run entry", "entry point")


def _is_generic_leak(instruction: str, desc: str) -> bool:
    inst_low = instruction.lower()
    desc_low = (desc or "").lower()
    if any(kw in inst_low for kw in _ENTRYPOINT_RELEVANT):
        return False
    return any(g in desc_low for g in _GENERIC_LEAK)


# --- 12 scenarios (S1-S8 baseline + S9-S12 compress/simple/vague/non-code) ---

REPLANNER_REGRESSION_SCENARIOS = [
    {
        "id": "S1",
        "name": "entrypoint substantive",
        "single_search_valid": False,
        "instruction": "Where does the application start? Identify main, CLI, or run entry.",
        "state_factory": lambda: _mk_state(
            "Where does the application start? Identify main, CLI, or run entry.",
            [
                {"id": 1, "action": "SEARCH", "description": "find entry point"},
                {"id": 2, "action": "SEARCH", "description": "find entry point application"},
                {"id": 3, "action": "BUILD_CONTEXT"},
                {"id": 4, "action": "EXPLAIN"},
            ],
            [
                StepResult(1, "SEARCH", True, {"query": "find entry point"}, 0.1),
                StepResult(2, "SEARCH", True, {"query": "find entry point application startup"}, 0.1),
                StepResult(3, "BUILD_CONTEXT", True, {"context_blocks": [{"p": "app/main.py", "s": "def main():\n    ..."}]}, 0.1),
                StepResult(4, "EXPLAIN", False, "", 0.1, error="non-substantive context", reason_code="insufficient_substantive_context"),
            ],
            _weak(),
        ),
        "failed": {"id": 4, "action": "EXPLAIN", "description": "explain how the app starts", "reason": "r"},
        "error": "EXPLAIN received non-substantive context: only stubs/placeholders",
    },
    {
        "id": "S2",
        "name": "short auth question",
        "single_search_valid": True,
        "instruction": "How does auth work?",
        "state_factory": lambda: _mk_state(
            "How does auth work?",
            [{"id": 1, "action": "SEARCH"}, {"id": 2, "action": "EXPLAIN"}],
            [
                StepResult(1, "SEARCH", True, {"query": "auth module"}, 0.1),
                StepResult(2, "EXPLAIN", False, "", 0.1, error="non-substantive", reason_code="insufficient_substantive_context"),
            ],
            _weak(),
        ),
        "failed": {"id": 2, "action": "EXPLAIN", "description": "explain auth", "reason": "r"},
        "error": "EXPLAIN received non-substantive context",
    },
    {
        "id": "S3",
        "name": "grounding block",
        "single_search_valid": False,
        "instruction": "Explain the retrieval pipeline step order.",
        "state_factory": lambda: _mk_state(
            "Explain the retrieval pipeline step order.",
            [{"id": 1, "action": "SEARCH"}, {"id": 2, "action": "EXPLAIN"}],
            [
                StepResult(1, "SEARCH", True, {"query": "retrieval pipeline"}, 0.1),
                StepResult(2, "EXPLAIN", False, "", 0.1, error="Blocked: insufficient grounding", reason_code="insufficient_grounding"),
            ],
            {"last_dispatch_reason_code": "insufficient_grounding", "replan_recovery_history": []},
        ),
        "failed": {"id": 2, "action": "EXPLAIN", "description": "explain pipeline", "reason": "r"},
        "error": "Blocked: insufficient grounding evidence",
    },
    {
        "id": "S4",
        "name": "test-only context",
        "single_search_valid": False,
        "instruction": "How does the dispatcher route steps to tools?",
        "state_factory": lambda: _mk_state(
            "How does the dispatcher route steps to tools?",
            [{"id": 1, "action": "SEARCH"}, {"id": 2, "action": "BUILD_CONTEXT"}, {"id": 3, "action": "EXPLAIN"}],
            [
                StepResult(1, "SEARCH", True, {"query": "dispatcher"}, 0.1),
                StepResult(2, "BUILD_CONTEXT", True, {"context_blocks": [{"p": "tests/test_dispatcher.py", "s": "def test_dispatch():..."}]}, 0.1),
                StepResult(3, "EXPLAIN", False, "", 0.1, error="Context only contains test file documentation", reason_code="insufficient_substantive_context"),
            ],
            _weak("need_non_test_code"),
        ),
        "failed": {"id": 3, "action": "EXPLAIN", "description": "explain dispatcher", "reason": "r"},
        "error": "Context only contains test file documentation",
    },
    {
        "id": "S5",
        "name": "weak SEARCH result",
        "single_search_valid": True,
        "instruction": "Find the patch generator implementation.",
        "state_factory": lambda: _mk_state(
            "Find the patch generator implementation.",
            [{"id": 1, "action": "SEARCH", "description": "generic search patches"}],
            [StepResult(1, "SEARCH", False, {"query": "patches", "results": []}, 0.1, error="empty results", reason_code=None)],
            {"search_quality": "weak", "replan_recovery_history": []},
        ),
        "failed": {"id": 1, "action": "SEARCH", "description": "generic search patches", "reason": "r"},
        "error": "empty or no useful results",
    },
    {
        "id": "S6",
        "name": "architecture multi-hop",
        "single_search_valid": False,
        "instruction": "How does a request flow from the planner through dispatch to the retrieval pipeline?",
        "state_factory": lambda: _mk_state(
            "How does a request flow from the planner through dispatch to the retrieval pipeline?",
            [{"id": 1, "action": "SEARCH"}, {"id": 2, "action": "EXPLAIN"}],
            [
                StepResult(1, "SEARCH", True, {"query": "planner dispatch"}, 0.1),
                StepResult(2, "EXPLAIN", False, "", 0.1, error="non-substantive: only high-level stubs", reason_code="insufficient_substantive_context"),
            ],
            _weak(),
        ),
        "failed": {"id": 2, "action": "EXPLAIN", "description": "explain request flow", "reason": "r"},
        "error": "EXPLAIN received non-substantive context: only high-level stubs",
    },
    {
        "id": "S7",
        "name": "escalated retry",
        "single_search_valid": False,
        "instruction": "Where does the application start? Identify main, CLI, or run entry.",
        "state_factory": lambda: _mk_state(
            "Where does the application start? Identify main, CLI, or run entry.",
            [{"id": 1, "action": "SEARCH"}, {"id": 2, "action": "BUILD_CONTEXT"}, {"id": 3, "action": "EXPLAIN"}],
            [
                StepResult(1, "SEARCH", True, {"query": "files with __main__"}, 0.1),
                StepResult(2, "BUILD_CONTEXT", True, {"context_blocks": [{"p": "app/__main__.py", "s": "from app import main"}]}, 0.1),
                StepResult(3, "EXPLAIN", False, "", 0.1, error="non-substantive: only import lines", reason_code="insufficient_substantive_context"),
            ],
            {
                "search_quality": "weak",
                "replan_recovery_history": [
                    {"failed_action": "EXPLAIN", "error_signal": "insufficient_substantive_context", "recovery_mode": "need_implementation_search"},
                    {"failed_action": "EXPLAIN", "error_signal": "insufficient_substantive_context", "recovery_mode": "need_implementation_search"},
                ],
            },
        ),
        "failed": {"id": 3, "action": "EXPLAIN", "description": "explain startup", "reason": "r"},
        "error": "EXPLAIN received non-substantive context: only import lines, no bodies",
    },
    {
        "id": "S8",
        "name": "cold EXPLAIN",
        "single_search_valid": True,
        "instruction": "Explain the config loader.",
        "state_factory": lambda: _mk_state(
            "Explain the config loader.",
            [{"id": 1, "action": "EXPLAIN", "description": "explain config loader"}],
            [StepResult(1, "EXPLAIN", False, "", 0.1, error="I cannot answer without relevant code context", reason_code=None)],
            {"replan_recovery_history": []},
        ),
        "failed": {"id": 1, "action": "EXPLAIN", "description": "explain config loader", "reason": "r"},
        "error": "I cannot answer without relevant code context",
    },
    # --- S9-S12: compress / simple factual / vague / non-code ---
    {
        "id": "S9",
        "name": "direct symbol lookup",
        "instruction": "Where is function load_config defined?",
        "single_search_valid": True,  # 1 SEARCH acceptable; no forced multi-hop
        "state_factory": lambda: _mk_state(
            "Where is function load_config defined?",
            [{"id": 1, "action": "EXPLAIN", "description": "explain load_config"}],
            [StepResult(1, "EXPLAIN", False, "", 0.1, error="I cannot answer without relevant code context", reason_code=None)],
            {"replan_recovery_history": []},
        ),
        "failed": {"id": 1, "action": "EXPLAIN", "description": "explain load_config", "reason": "r"},
        "error": "I cannot answer without relevant code context",
    },
    {
        "id": "S10",
        "name": "simple factual",
        "instruction": "What does the retry mechanism do?",
        "single_search_valid": True,  # grounded SEARCH; no entrypoint bias; 1 SEARCH acceptable
        "state_factory": lambda: _mk_state(
            "What does the retry mechanism do?",
            [{"id": 1, "action": "SEARCH"}, {"id": 2, "action": "EXPLAIN"}],
            [
                StepResult(1, "SEARCH", True, {"query": "retry"}, 0.1),
                StepResult(2, "EXPLAIN", False, "", 0.1, error="non-substantive", reason_code="insufficient_substantive_context"),
            ],
            _weak(),
        ),
        "failed": {"id": 2, "action": "EXPLAIN", "description": "explain retry", "reason": "r"},
        "error": "EXPLAIN received non-substantive context",
    },
    {
        "id": "S11",
        "name": "vague user query",
        "instruction": "How does this system work?",
        "single_search_valid": False,  # multi SEARCH expected (exploration justified); no generic leakage
        "state_factory": lambda: _mk_state(
            "How does this system work?",
            [{"id": 1, "action": "SEARCH"}, {"id": 2, "action": "EXPLAIN"}],
            [
                StepResult(1, "SEARCH", True, {"query": "system"}, 0.1),
                StepResult(2, "EXPLAIN", False, "", 0.1, error="Blocked: insufficient grounding", reason_code="insufficient_grounding"),
            ],
            {"last_dispatch_reason_code": "insufficient_grounding", "replan_recovery_history": []},
        ),
        "failed": {"id": 2, "action": "EXPLAIN", "description": "explain system", "reason": "r"},
        "error": "Blocked: insufficient grounding evidence",
    },
    {
        "id": "S12",
        "name": "non-code config query",
        "instruction": "Where are API keys configured?",
        "single_search_valid": True,  # config/settings oriented; no architecture bias; 1 SEARCH acceptable
        "state_factory": lambda: _mk_state(
            "Where are API keys configured?",
            [{"id": 1, "action": "EXPLAIN", "description": "explain API key config"}],
            [StepResult(1, "EXPLAIN", False, "", 0.1, error="I cannot answer without relevant code context", reason_code=None)],
            {"replan_recovery_history": []},
        ),
        "failed": {"id": 1, "action": "EXPLAIN", "description": "explain API key config", "reason": "r"},
        "error": "I cannot answer without relevant code context",
    },
]

# Property: single_search_valid = instruction refers to a specific symbol, file, or component
#           AND does not require cross-module reasoning.
# Scenario flags below are the regression oracle for this property (S2,S5,S8,S9,S10,S12).
def _single_search_would_suffice(scenario: dict) -> bool:
    """True when instruction is specific/scoped; single SEARCH is appropriate (no over-expansion)."""
    return bool(scenario.get("single_search_valid"))


def _eval_plan(plan: dict, instruction: str) -> dict:
    steps = plan.get("steps") or []
    actions = [(s.get("action") or "").upper() for s in steps]
    searches = [s for s in steps if (s.get("action") or "").upper() == "SEARCH"]
    bcs = [s for s in steps if (s.get("action") or "").upper() == "BUILD_CONTEXT"]
    descs = [(s.get("description") or "").strip() for s in searches]

    direct = any(
        actions[i] == "SEARCH" and actions[i + 1] == "EXPLAIN"
        for i in range(len(actions) - 1)
    )
    leak = any(_is_generic_leak(instruction, d) for d in descs)
    has_bc = len(bcs) >= 1
    diverse = (
        len(set(d.lower()[:50] for d in descs)) == len(descs)
        if len(descs) > 1
        else True
    )
    n_search = len(searches)
    return {"leak": leak, "has_bc": has_bc, "direct": direct, "diverse": diverse, "n_search": n_search, "descs": descs}


@pytest.mark.replanner_regression
@pytest.mark.slow
def test_replanner_regression_suite():
    """
    Replanner regression suite — 12 scenarios. Every prompt change must pass.

    Contract (v2 baseline):
      - BUILD_CONTEXT present: 12/12
      - No direct SEARCH→EXPLAIN: 12/12
      - SEARCH diversity: 12/12
      - Generic leakage: ≤ 3/12
      - single_search_valid_rate: 100% (avoids under-search)
      - over_expansion_rate: < 20% (avoids over-search)

    Balancing forces: under-search (single_search_valid_rate) vs over-search (over_expansion_rate).
    Adaptive: simple→compress, complex→expand, uncertain→explore, failing→recover.
    """
    n_scenarios = len(REPLANNER_REGRESSION_SCENARIOS)
    results = []
    for sc in REPLANNER_REGRESSION_SCENARIOS:
        state = sc["state_factory"]()
        plan = replan(state, failed_step=sc["failed"], error=sc["error"])
        assert "steps" in plan, f"{sc['id']}: plan must have steps"
        ev = _eval_plan(plan, sc["instruction"])
        results.append({
            "id": sc["id"],
            "name": sc["name"],
            "single_search_valid": _single_search_would_suffice(sc),
            **ev,
        })

    bc_ok = sum(1 for r in results if r["has_bc"])
    no_direct = sum(1 for r in results if not r["direct"])
    diverse_ok = sum(1 for r in results if r["diverse"])
    leaks = sum(1 for r in results if r["leak"])

    # single_search_valid_rate: % of single-SEARCH plans that are appropriate.
    # Property: instruction refers to specific symbol/file/component, no cross-module reasoning.
    single_search_results = [r for r in results if r["n_search"] == 1]
    if single_search_results:
        valid_single = sum(1 for r in single_search_results if r["single_search_valid"])
        single_search_valid_rate = valid_single / len(single_search_results)
    else:
        single_search_valid_rate = 1.0  # vacuously ok

    # over_expansion_rate: % of plans with ≥2 SEARCH where single would suffice.
    # Avoids drift to "always 2 SEARCH" (safe but inefficient).
    over_expanded = sum(
        1 for r in results
        if r["n_search"] >= 2 and r["single_search_valid"]
    )
    over_expansion_rate = over_expanded / n_scenarios

    assert bc_ok == n_scenarios, f"BUILD_CONTEXT present: {bc_ok}/{n_scenarios} (expected {n_scenarios})"
    assert no_direct == n_scenarios, f"No direct SEARCH→EXPLAIN: {no_direct}/{n_scenarios} (expected {n_scenarios})"
    assert diverse_ok == n_scenarios, f"SEARCH diversity: {diverse_ok}/{n_scenarios} (expected {n_scenarios})"
    assert leaks <= 3, (
        f"Generic leakage {leaks}/{n_scenarios} exceeds target (≤3). "
        "replanner_user v2 contract requires instruction-grounded searches."
    )
    assert single_search_valid_rate >= 1.0, (
        f"single_search_valid_rate {single_search_valid_rate:.0%} < 100%. "
        "When plan has 1 SEARCH, instruction must be specific and scoped (no cross-module reasoning). "
        "Prevents under-search."
    )
    assert over_expansion_rate < 0.20, (
        f"over_expansion_rate {over_expansion_rate:.0%} >= 20%. "
        "Too many multi-SEARCH plans where single would suffice. "
        "Prevents over-search / drift to always-2-SEARCH."
    )
