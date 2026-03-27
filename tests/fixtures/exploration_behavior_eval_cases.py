from __future__ import annotations

from agent_v2.exploration.exploration_behavior_eval_harness import EvalCase, SymbolEntry


def build_eval_suites() -> dict[str, list[EvalCase]]:
    seed_a = SymbolEntry(file_path="agent_v2/exploration/exploration_engine_v2.py", symbol="_explore_inner")
    seed_b = SymbolEntry(file_path="agent_v2/config.py", symbol="get_project_root")
    seed_c = SymbolEntry(file_path="agent_v2/runtime/exploration_runner.py", symbol="run")

    expand_cases = [
        EvalCase(
            id="expand_caller_gap_1",
            instruction="Trace caller chain for _explore_inner",
            focus_area="expand",
            seed_symbols=[seed_a, seed_b],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.5,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing caller chain for _explore_inner"],
                    "summary": "need callers",
                },
                {
                    "relevance": "high",
                    "confidence": 0.7,
                    "sufficient": True,
                    "evidence_sufficiency": "sufficient",
                    "knowledge_gaps": [],
                    "summary": "resolved",
                },
            ],
            expected_behavior={
                "expected_actions": ["expand"],
                "step_expectations": {"step_1": ["must_expand"], "step_2": ["must_not_refine"]},
                "expected_patterns": ["must_expand_on_caller_gap"],
                "max_loop_depth": 6,
            },
        ),
        EvalCase(
            id="expand_callee_gap_2",
            instruction="Trace downstream callees of _explore_inner",
            focus_area="expand",
            seed_symbols=[seed_a, seed_c],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.55,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing callee flow for _explore_inner"],
                    "summary": "need callees",
                },
                {
                    "relevance": "high",
                    "confidence": 0.7,
                    "sufficient": True,
                    "evidence_sufficiency": "sufficient",
                    "knowledge_gaps": [],
                    "summary": "resolved",
                },
            ],
            expected_behavior={
                "expected_actions": ["expand"],
                "step_expectations": {"step_1": ["must_expand"]},
                "expected_patterns": ["must_expand_on_callee_gap"],
                "max_loop_depth": 6,
            },
        ),
        EvalCase(
            id="expand_direction_route_3",
            instruction="Follow caller relationships for exploration runner path",
            focus_area="expand",
            seed_symbols=[seed_b, seed_c],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.45,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing caller path for run"],
                    "summary": "expand direction expected",
                }
            ],
            expected_behavior={
                "expected_actions": ["expand"],
                "step_expectations": {"step_1": ["must_expand"]},
                "expected_patterns": ["expand_direction_routing_applies"],
                "max_loop_depth": 4,
            },
        ),
    ]

    refine_cases = [
        EvalCase(
            id="refine_wrong_target_1",
            instruction="Find definition path for unknown symbol mismatch",
            focus_area="refine",
            seed_symbols=[seed_a],
            analyzer_script=[
                {
                    "relevance": "low",
                    "confidence": 0.4,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing definition of unknown symbol"],
                    "summary": "wrong target likely",
                }
            ],
            expected_behavior={
                "step_expectations": {"step_1": ["must_refine"]},
                "expected_patterns": ["must_refine_on_wrong_target"],
                "max_loop_depth": 4,
            },
            force_refine_actions=1,
        ),
        EvalCase(
            id="refine_low_relevance_2",
            instruction="Narrow low relevance discovery to correct symbol",
            focus_area="refine",
            seed_symbols=[seed_b],
            analyzer_script=[
                {
                    "relevance": "low",
                    "confidence": 0.35,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing definition context for get_project_root"],
                    "summary": "low relevance",
                }
            ],
            expected_behavior={
                "step_expectations": {"step_1": ["must_refine"]},
                "expected_patterns": ["low_relevance_refine_path"],
                "max_loop_depth": 4,
            },
            force_refine_actions=1,
        ),
        EvalCase(
            id="refine_definition_gap_3",
            instruction="Locate exact definition for run symbol",
            focus_area="refine",
            seed_symbols=[seed_c],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.45,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing definition of run"],
                    "summary": "definition refine expected",
                }
            ],
            expected_behavior={
                "step_expectations": {"step_1": ["must_refine"]},
                "expected_patterns": ["definition_gap_refine_not_expand"],
                "max_loop_depth": 4,
            },
            force_refine_actions=1,
        ),
    ]

    memory_feedback_cases = [
        EvalCase(
            id="memory_influence_1",
            instruction="Use memory feedback to continue relationship tracing",
            focus_area="memory_feedback",
            seed_symbols=[seed_a, seed_b, seed_c],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.45,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing caller chain for _explore_inner"],
                    "summary": "first pass",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.5,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing caller chain for get_project_root"],
                    "summary": "second pass",
                },
            ],
            expected_behavior={
                "step_expectations": {"step_1": ["must_expand"]},
                "expected_patterns": ["memory_must_influence_next_decision"],
                "max_loop_depth": 6,
            },
        ),
        EvalCase(
            id="memory_refine_to_expand_2",
            instruction="Coerce refine to expand when memory has relationship gaps",
            focus_area="memory_feedback",
            seed_symbols=[seed_a, seed_b],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.4,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing caller chain for _explore_inner"],
                    "summary": "relationship gap",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.45,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing caller chain for _explore_inner"],
                    "summary": "relationship gap persists",
                },
            ],
            expected_behavior={
                "step_expectations": {"step_1": ["must_expand"], "step_2": ["must_not_refine"]},
                "expected_patterns": ["memory_relationships_can_override_refine"],
                "max_loop_depth": 6,
            },
            force_refine_actions=1,
        ),
        EvalCase(
            id="memory_query_repeat_3",
            instruction="Refine queries with context feedback and avoid repeats",
            focus_area="memory_feedback",
            seed_symbols=[seed_c, seed_b],
            analyzer_script=[
                {
                    "relevance": "low",
                    "confidence": 0.4,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing definition of run"],
                    "summary": "force initial refine",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.55,
                    "sufficient": True,
                    "evidence_sufficiency": "sufficient",
                    "knowledge_gaps": [],
                    "summary": "resolved after refine",
                },
            ],
            expected_behavior={
                "step_expectations": {"step_1": ["must_refine"]},
                "expected_patterns": ["must_avoid_repeated_queries"],
                "max_loop_depth": 6,
            },
            force_refine_actions=1,
        ),
    ]

    failure_cases = [
        EvalCase(
            id="failure_stagnation_1",
            instruction="Probe stagnation behavior when gaps do not reduce",
            focus_area="failure",
            seed_symbols=[seed_a],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.4,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing caller chain for _explore_inner"],
                    "summary": "stagnant 1",
                },
                {
                    "relevance": "medium",
                    "confidence": 0.4,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing caller chain for _explore_inner"],
                    "summary": "stagnant 2",
                },
            ],
            expected_behavior={
                "expected_patterns": ["stagnation_detected_or_guarded"],
                "max_loop_depth": 6,
            },
        ),
        EvalCase(
            id="failure_duplicate_query_2",
            instruction="Detect duplicate query pressure and avoid loops",
            focus_area="failure",
            seed_symbols=[seed_b],
            analyzer_script=[
                {
                    "relevance": "low",
                    "confidence": 0.35,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing definition context for get_project_root"],
                    "summary": "dup query pressure",
                }
            ],
            expected_behavior={
                "expected_patterns": ["must_avoid_repeated_queries"],
                "max_loop_depth": 6,
            },
            force_refine_actions=1,
        ),
        EvalCase(
            id="failure_pending_exhausted_3",
            instruction="Handle pending exhausted behavior with incomplete context",
            focus_area="failure",
            seed_symbols=[seed_c],
            analyzer_script=[
                {
                    "relevance": "medium",
                    "confidence": 0.45,
                    "sufficient": False,
                    "evidence_sufficiency": "partial",
                    "knowledge_gaps": ["missing caller path for run"],
                    "summary": "incomplete",
                }
            ],
            expected_behavior={
                "expected_patterns": ["pending_exhausted_is_observable"],
                "max_loop_depth": 6,
            },
        ),
    ]

    return {
        "expand_cases": expand_cases,
        "refine_cases": refine_cases,
        "memory_feedback_cases": memory_feedback_cases,
        "failure_cases": failure_cases,
    }

