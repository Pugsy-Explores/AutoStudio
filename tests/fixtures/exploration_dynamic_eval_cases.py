from __future__ import annotations

from agent_v2.exploration.exploration_behavior_eval_harness import EvalCase, SymbolEntry

# Real symbol relationships discovered from agent_v2/ for dynamic eval targeting.
REAL_TARGET_RELATIONSHIPS: list[tuple[str, str, str]] = [
    ("ExplorationEngineV2", "agent_v2/exploration/exploration_engine_v2.py", "class_definition"),
    ("_explore_inner", "agent_v2/exploration/exploration_engine_v2.py", "core_engine_method"),
    ("_should_expand", "agent_v2/exploration/exploration_engine_v2.py", "decision_guard"),
    ("_should_refine", "agent_v2/exploration/exploration_engine_v2.py", "decision_guard"),
    ("_next_action", "agent_v2/exploration/exploration_engine_v2.py", "decision_projection"),
    ("QueryIntentParser.parse", "agent_v2/exploration/query_intent_parser.py", "called_by_engine"),
    ("UnderstandingAnalyzer.analyze", "agent_v2/exploration/understanding_analyzer.py", "called_by_engine"),
    ("GraphExpander.expand", "agent_v2/exploration/graph_expander.py", "expand_dependency"),
    ("ExplorationScoper.scope", "agent_v2/exploration/exploration_scoper.py", "scope_dependency"),
    ("ExplorationWorkingMemory.add_gap", "agent_v2/exploration/exploration_working_memory.py", "memory_feedback"),
    ("ExplorationWorkingMemory.get_summary", "agent_v2/exploration/exploration_working_memory.py", "memory_feedback"),
    ("ExplorationResultAdapter.build", "agent_v2/exploration/exploration_result_adapter.py", "finalization"),
    ("EXPLORATION_MAX_STEPS", "agent_v2/config.py", "config_used_by_engine"),
    ("EXPLORATION_STAGNATION_STEPS", "agent_v2/config.py", "config_used_by_engine"),
    ("ENABLE_GAP_DRIVEN_EXPANSION", "agent_v2/config.py", "config_used_by_engine"),
    ("ENABLE_REFINE_COOLDOWN", "agent_v2/config.py", "config_used_by_engine"),
    ("ENABLE_EXPLORATION_SCOPER", "agent_v2/config.py", "config_used_by_runner"),
    ("ExplorationRunner.run", "agent_v2/runtime/exploration_runner.py", "entrypoint_to_engine"),
]


EXPLORATION_EVAL_CASES = [
    # ------------------------------------------------------------------
    # expand-heavy (4)
    # ------------------------------------------------------------------
    {
        "id": "dyn_expand_caller_chain_explore_inner",
        "instruction": "Trace where ExplorationEngineV2._explore_inner is invoked and follow the caller chain into the runtime entrypoint.",
        "target": {"symbol": "_explore_inner", "file": "agent_v2/exploration/exploration_engine_v2.py"},
        "focus_area": "expand",
        "expected_behavior": {
            "expected_actions": ["expand"],
            "required_patterns": ["must_expand_on_caller_gap", "must_traverse_runtime_to_engine_path"],
            "forbidden_patterns": ["repeated_same_query", "premature_refine"],
        },
        "step_expectations": {1: ["must_expand"]},
    },
    {
        "id": "dyn_expand_gap_driven_config_flow",
        "instruction": "Follow how ENABLE_GAP_DRIVEN_EXPANSION from config affects action selection in ExplorationEngineV2 decision logic.",
        "target": {"symbol": "ENABLE_GAP_DRIVEN_EXPANSION", "file": "agent_v2/config.py"},
        "focus_area": "expand",
        "expected_behavior": {
            "expected_actions": ["expand"],
            "required_patterns": ["must_expand_on_config_gap", "must_link_config_to_decision_branch"],
            "forbidden_patterns": ["single_file_stop", "repeated_same_query"],
        },
        "step_expectations": {1: ["must_expand"], 2: ["must_not_refine"]},
    },
    {
        "id": "dyn_expand_should_expand_to_graph",
        "instruction": "Identify how _should_expand gates graph traversal and where GraphExpander.expand is triggered from the engine loop.",
        "target": {"symbol": "_should_expand", "file": "agent_v2/exploration/exploration_engine_v2.py"},
        "focus_area": "expand",
        "expected_behavior": {
            "expected_actions": ["expand", "expand"],
            "required_patterns": ["must_expand_on_caller_or_callee_gap", "must_follow_expand_guard_to_dependency"],
            "forbidden_patterns": ["premature_stop", "repeated_same_query"],
        },
        "step_expectations": {1: ["must_expand"]},
    },
    {
        "id": "dyn_expand_runner_engine_wiring",
        "instruction": "Trace the runtime wiring path from ExplorationRunner.run to the instantiated ExplorationEngineV2 components and explain the expansion path.",
        "target": {"symbol": "ExplorationRunner.run", "file": "agent_v2/runtime/exploration_runner.py"},
        "focus_area": "expand",
        "expected_behavior": {
            "expected_actions": ["expand"],
            "required_patterns": ["must_expand_for_cross_file_wiring", "must_follow_constructor_dependencies"],
            "forbidden_patterns": ["single_hop_answer", "repeated_same_query"],
        },
        "step_expectations": {1: ["must_expand"]},
    },
    # ------------------------------------------------------------------
    # refine-required (3)
    # ------------------------------------------------------------------
    {
        "id": "dyn_refine_query_intent_feedback",
        "instruction": "Follow how QueryIntentParser.parse uses previous_queries and failure_reason to remove repeated queries and refine intent.",
        "target": {"symbol": "QueryIntentParser.parse", "file": "agent_v2/exploration/query_intent_parser.py"},
        "focus_area": "refine",
        "expected_behavior": {
            "expected_actions": ["refine"],
            "required_patterns": ["must_refine_on_previous_query_overlap", "must_avoid_repeated_queries"],
            "forbidden_patterns": ["blind_expand_without_disambiguation", "repeated_same_query"],
        },
        "step_expectations": {1: ["must_refine"]},
    },
    {
        "id": "dyn_refine_should_refine_cooldown",
        "instruction": "Trace _should_refine behavior and explain how ENABLE_REFINE_COOLDOWN and related guards affect refine execution.",
        "target": {"symbol": "_should_refine", "file": "agent_v2/exploration/exploration_engine_v2.py"},
        "focus_area": "refine",
        "expected_behavior": {
            "expected_actions": ["refine"],
            "required_patterns": ["must_refine_when_target_is_wrong_or_partial", "must_account_for_refine_guards"],
            "forbidden_patterns": ["unbounded_refine_loop", "repeated_same_query"],
        },
        "step_expectations": {1: ["must_refine"]},
    },
    {
        "id": "dyn_refine_analyzer_context_disambiguation",
        "instruction": "Inspect how UnderstandingAnalyzer.analyze consumes context blocks and drives refine decisions when relevance is low.",
        "target": {"symbol": "UnderstandingAnalyzer.analyze", "file": "agent_v2/exploration/understanding_analyzer.py"},
        "focus_area": "refine",
        "expected_behavior": {
            "expected_actions": ["refine", "expand"],
            "required_patterns": ["must_refine_on_low_relevance", "must_transition_after_disambiguation"],
            "forbidden_patterns": ["premature_expand", "repeated_same_query"],
        },
        "step_expectations": {1: ["must_refine"]},
    },
    # ------------------------------------------------------------------
    # memory feedback (3)
    # ------------------------------------------------------------------
    {
        "id": "dyn_memory_gap_to_next_action",
        "instruction": "Track how ExplorationWorkingMemory.add_gap and get_summary influence the next action selected by ExplorationEngineV2.",
        "target": {"symbol": "ExplorationWorkingMemory.add_gap", "file": "agent_v2/exploration/exploration_working_memory.py"},
        "focus_area": "memory_feedback",
        "expected_behavior": {
            "expected_actions": ["expand", "expand"],
            "required_patterns": ["must_use_memory_between_steps", "must_expand_on_persisting_structural_gap"],
            "forbidden_patterns": ["memory_ignored_between_steps", "repeated_same_query"],
        },
        "step_expectations": {1: ["must_expand"], 2: ["must_not_refine"]},
    },
    {
        "id": "dyn_memory_relationship_feedback_expand",
        "instruction": "Follow how add_relationships_from_expand updates working memory and how that feedback changes subsequent exploration direction.",
        "target": {"symbol": "add_relationships_from_expand", "file": "agent_v2/exploration/exploration_working_memory.py"},
        "focus_area": "memory_feedback",
        "expected_behavior": {
            "expected_actions": ["expand"],
            "required_patterns": ["must_use_relationship_feedback", "must_avoid_redundant_expansion"],
            "forbidden_patterns": ["repeated_same_query", "memory_ignored_between_steps"],
        },
        "step_expectations": {1: ["must_expand"]},
    },
    {
        "id": "dyn_memory_result_adapter_snapshot_use",
        "instruction": "Trace how ExplorationResultAdapter.build consumes memory snapshot data to produce final exploration schema outputs.",
        "target": {"symbol": "ExplorationResultAdapter.build", "file": "agent_v2/exploration/exploration_result_adapter.py"},
        "focus_area": "memory_feedback",
        "expected_behavior": {
            "expected_actions": ["expand", "refine"],
            "required_patterns": ["must_use_memory_snapshot_in_finalization", "must_preserve_memory_derived_relationships"],
            "forbidden_patterns": ["single_file_stop", "repeated_same_query"],
        },
        "step_expectations": {1: ["must_expand"]},
    },
    # ------------------------------------------------------------------
    # multi-hop traversal (3)
    # ------------------------------------------------------------------
    {
        "id": "dyn_multihop_runner_scoper_engine",
        "instruction": "Trace the multi-hop path from ENABLE_EXPLORATION_SCOPER in config through ExplorationRunner wiring into ExplorationScoper.scope usage.",
        "target": {"symbol": "ENABLE_EXPLORATION_SCOPER", "file": "agent_v2/config.py"},
        "focus_area": "multi_hop",
        "expected_behavior": {
            "expected_actions": ["expand", "expand"],
            "required_patterns": ["must_follow_config_to_runner_to_component", "must_expand_multi_hop_path"],
            "forbidden_patterns": ["single_hop_answer", "repeated_same_query"],
        },
        "step_expectations": {1: ["must_expand"]},
    },
    {
        "id": "dyn_multihop_intent_to_engine_decision",
        "instruction": "Follow how QueryIntentParser.parse output propagates through exploration loop decisions, including retries and refined intent paths.",
        "target": {"symbol": "QueryIntentParser.parse", "file": "agent_v2/exploration/query_intent_parser.py"},
        "focus_area": "multi_hop",
        "expected_behavior": {
            "expected_actions": ["expand", "refine"],
            "required_patterns": ["must_trace_parser_to_engine_decision_flow", "must_capture_retry_refine_transition"],
            "forbidden_patterns": ["single_file_stop", "repeated_same_query"],
        },
        "step_expectations": {1: ["must_expand"]},
    },
    {
        "id": "dyn_multihop_max_steps_termination",
        "instruction": "Trace how EXPLORATION_MAX_STEPS is defined in config and enforced in engine/runtime loop termination behavior.",
        "target": {"symbol": "EXPLORATION_MAX_STEPS", "file": "agent_v2/config.py"},
        "focus_area": "multi_hop",
        "expected_behavior": {
            "expected_actions": ["expand"],
            "required_patterns": ["must_link_config_limit_to_termination", "must_expand_across_runtime_and_engine"],
            "forbidden_patterns": ["ignore_loop_limits", "repeated_same_query"],
        },
        "step_expectations": {1: ["must_expand"]},
    },
    # ------------------------------------------------------------------
    # failure scenarios (2)
    # ------------------------------------------------------------------
    {
        "id": "dyn_failure_no_callers_terminal_symbol",
        "instruction": "Investigate callers of _norm_gap_desc and explain how exploration should terminate when caller expansion yields little additional signal.",
        "target": {"symbol": "_norm_gap_desc", "file": "agent_v2/exploration/exploration_working_memory.py"},
        "focus_area": "failure_mode",
        "expected_behavior": {
            "expected_actions": ["expand", "stop"],
            "required_patterns": ["should_not_loop_when_callers_sparse", "should_terminate_with_guardrails"],
            "forbidden_patterns": ["repeated_same_query", "unbounded_expand_loop"],
        },
        "step_expectations": {2: ["must_not_refine"]},
    },
    {
        "id": "dyn_failure_noisy_context_refine_then_stop",
        "instruction": "Trace EXPLORATION_STAGNATION_STEPS and show how noisy multi-file context should trigger refine once, then bounded termination without repeated queries.",
        "target": {"symbol": "EXPLORATION_STAGNATION_STEPS", "file": "agent_v2/config.py"},
        "focus_area": "failure_mode",
        "expected_behavior": {
            "expected_actions": ["refine", "stop"],
            "required_patterns": ["must_refine_under_noise_then_bound", "should_not_repeat_queries_after_stagnation"],
            "forbidden_patterns": ["repeated_same_query", "unbounded_refine_loop"],
        },
        "step_expectations": {1: ["must_refine"]},
    },
]


def _normalize_step_expectations(step_expectations: dict[int, list[str]] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for idx, reqs in (step_expectations or {}).items():
        out[f"step_{int(idx)}"] = list(reqs or [])
    return out


def _default_analyzer_script(case: dict) -> list[dict]:
    target = case.get("target") or {}
    symbol = str(target.get("symbol") or "target_symbol")
    focus = str(case.get("focus_area") or "")
    expected_actions = list((case.get("expected_behavior") or {}).get("expected_actions") or [])
    first_action = str(expected_actions[0] if expected_actions else "")

    gap_text = f"missing caller/usage chain for {symbol}"
    if focus == "refine":
        gap_text = f"missing exact definition context for {symbol}"
    elif focus == "memory_feedback":
        gap_text = f"missing relationship context for {symbol}"
    elif focus == "failure_mode":
        gap_text = f"incomplete context for {symbol} under noisy candidates"

    first = {
        "relevance": "low" if first_action == "refine" else "medium",
        "confidence": 0.4 if first_action == "refine" else 0.5,
        "sufficient": False,
        "evidence_sufficiency": "partial",
        "knowledge_gaps": [gap_text],
        "summary": "dynamic eval step 1",
    }
    second = {
        "relevance": "high",
        "confidence": 0.7,
        "sufficient": True,
        "evidence_sufficiency": "sufficient",
        "knowledge_gaps": [],
        "summary": "dynamic eval resolved",
    }
    if len(expected_actions) >= 2:
        return [first, second]
    return [first]


def _to_eval_case(case: dict) -> EvalCase:
    target = case.get("target") or {}
    symbol = str(target.get("symbol") or "")
    file_path = str(target.get("file") or "")
    expected_behavior = dict(case.get("expected_behavior") or {})
    # Harness uses expected_patterns key.
    required_patterns = list(expected_behavior.pop("required_patterns", []) or [])
    if required_patterns:
        expected_behavior["expected_patterns"] = required_patterns
    step_expectations = _normalize_step_expectations(case.get("step_expectations"))
    if step_expectations:
        expected_behavior["step_expectations"] = step_expectations
    expected_behavior.setdefault("max_loop_depth", 6)

    force_refine_actions = 0
    expected_actions = list(expected_behavior.get("expected_actions") or [])
    if expected_actions and str(expected_actions[0]) == "refine":
        force_refine_actions = 1

    return EvalCase(
        id=str(case.get("id") or "dynamic_case"),
        instruction=str(case.get("instruction") or ""),
        focus_area=str(case.get("focus_area") or "multi_hop"),
        expected_behavior=expected_behavior,
        analyzer_script=_default_analyzer_script(case),
        seed_symbols=[SymbolEntry(file_path=file_path, symbol=symbol)],
        force_refine_actions=force_refine_actions,
    )


def build_dynamic_eval_cases() -> list[EvalCase]:
    """Convert repo-grounded dict fixtures to EvalCase dataclasses for run_eval_suite()."""
    return [_to_eval_case(case) for case in EXPLORATION_EVAL_CASES]


def build_dynamic_eval_suites() -> dict[str, list[EvalCase]]:
    suites: dict[str, list[EvalCase]] = {}
    for case in EXPLORATION_EVAL_CASES:
        focus = str(case.get("focus_area") or "multi_hop")
        suites.setdefault(focus, []).append(_to_eval_case(case))
    return suites

