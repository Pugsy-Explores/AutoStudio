#!/usr/bin/env python3
"""
Live retry-focused evaluation for QueryIntentParser.

All test cases explicitly include:
- previous_queries
- failure_reason

No downstream exploration stages are invoked.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from agent.models.model_client import call_reasoning_model_messages
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_QUERY_INTENT
from agent_v2.exploration.query_intent_parser import QueryIntentParser
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from live_query_intent_parser_eval import (
    _build_bm25_index,
    _diversity_score,
    _extract_code_signals,
    _meaningful_retry_change,
    _retrieval_metrics,
    _validate_output,
)


@dataclass(frozen=True)
class RetryCase:
    name: str
    instruction: str
    previous_queries: dict
    failure_reason: str


def _pick(values: list[str], idx: int, fallback: str) -> str:
    if not values:
        return fallback
    return values[idx % len(values)]


def build_realistic_retry_cases() -> list[RetryCase]:
    signals = _extract_code_signals()
    funcs = signals["functions"]
    classes = signals["classes"]
    modules = signals["modules"]

    f0 = _pick(funcs, 0, "run")
    f1 = _pick(funcs, 3, "dispatch")
    c0 = _pick(classes, 0, "QueryIntentParser")
    c1 = _pick(classes, 4, "ExplorationEngineV2")
    m0 = _pick(modules, 0, "exploration")
    m1 = _pick(modules, 2, "retrieval")
    m2 = _pick(modules, 5, "runtime")

    return [
        RetryCase(
            name="too_broad_runtime_flow",
            instruction=f"Trace request flow through `{m2}` and `{m0}`",
            previous_queries={
                "symbols": [],
                "keywords": ["flow", "request", "system", "architecture", "implementation"],
                "regex_patterns": [".*flow.*", ".*request.*"],
                "intents": ["trace flow"],
            },
            failure_reason="too_broad",
        ),
        RetryCase(
            name="no_results_missing_symbol",
            instruction=f"Find where `{f1}` is implemented and called",
            previous_queries={
                "symbols": [],
                "keywords": [f"{f1} logic", f"{f1} flow", "implementation"],
                "regex_patterns": [],
                "intents": ["find implementation"],
            },
            failure_reason="no_results",
        ),
        RetryCase(
            name="wrong_abstraction_api_to_db",
            instruction=f"Trace API to storage path for `{m1}`",
            previous_queries={
                "symbols": [m1],
                "keywords": ["api", "http endpoint", "controller only", "routes"],
                "regex_patterns": [".*router.*", ".*endpoint.*"],
                "intents": ["api layer"],
            },
            failure_reason="wrong_abstraction",
        ),
        RetryCase(
            name="ambiguous_intent_vague_bug",
            instruction=f"`{m0}` is broken and slow",
            previous_queries={
                "symbols": [],
                "keywords": ["broken", "slow", "bug"],
                "regex_patterns": [],
                "intents": [],
            },
            failure_reason="ambiguous_intent",
        ),
        RetryCase(
            name="too_narrow_exact_symbol_only",
            instruction=f"Where is `{c1}` initialized and used?",
            previous_queries={
                "symbols": [c1],
                "keywords": [],
                "regex_patterns": [],
                "intents": ["definition"],
            },
            failure_reason="too_narrow",
        ),
        RetryCase(
            name="low_relevance_generic_terms",
            instruction=f"Find logic for `{c0}` query refinement",
            previous_queries={
                "symbols": [],
                "keywords": ["logic", "feature", "query", "code", "function"],
                "regex_patterns": [],
                "intents": ["search"],
            },
            failure_reason="low_relevance",
        ),
        RetryCase(
            name="missing_symbol_signal_semantic_only",
            instruction=f"How does `{m1}` ranking work?",
            previous_queries={
                "symbols": [],
                "keywords": ["ranking", "scoring", "ordering", "relevance"],
                "regex_patterns": [],
                "intents": ["explain ranking"],
            },
            failure_reason="missing_symbol_signal",
        ),
        RetryCase(
            name="no_results_class_trace",
            instruction=f"Trace lifecycle of class `{c0}`",
            previous_queries={
                "symbols": [],
                "keywords": [f"{c0} lifecycle", "class flow", "creation and usage"],
                "regex_patterns": [],
                "intents": ["trace lifecycle"],
            },
            failure_reason="no_results",
        ),
        RetryCase(
            name="too_broad_function_find",
            instruction=f"Find all places related to `{f0}`",
            previous_queries={
                "symbols": [],
                "keywords": [f0, "all", "related", "system", "module"],
                "regex_patterns": [".*"],
                "intents": ["search everywhere"],
            },
            failure_reason="too_broad",
        ),
    ]


def main() -> int:
    model_name = get_prompt_model_name_for_task(EXPLORATION_TASK_QUERY_INTENT)
    parser = QueryIntentParser(
        llm_generate_messages=lambda messages: call_reasoning_model_messages(
            messages, task_name=EXPLORATION_TASK_QUERY_INTENT
        ),
        model_name=model_name,
    )
    bm25_docs, _dt, bm25 = _build_bm25_index()
    cases = build_realistic_retry_cases()

    print("# QueryIntentParser Retry Live Evaluation")
    print(f"# cases={len(cases)}")
    print(f"# bm25_docs={len(bm25_docs)}")

    aggregate = {
        "improved_count": 0,
        "not_improved_count": 0,
        "top_score_delta_sum": 0.0,
        "num_results_delta_sum": 0,
    }

    for idx, case in enumerate(cases, 1):
        prev = case.previous_queries
        refined = parser.parse(
            case.instruction,
            previous_queries=prev,
            failure_reason=case.failure_reason,
        ).model_dump()

        prev_validation = _validate_output(prev)
        ref_validation = _validate_output(refined)

        prev_retr = _retrieval_metrics(prev, bm25_docs, bm25)
        ref_retr = _retrieval_metrics(refined, bm25_docs, bm25)
        retry_change = _meaningful_retry_change(prev, refined)

        gain = {
            "num_results_delta": ref_retr["num_results"] - prev_retr["num_results"],
            "top_score_delta": round(ref_retr["top_score"] - prev_retr["top_score"], 6),
        }
        improved = gain["num_results_delta"] > 0 or gain["top_score_delta"] > 0
        if improved:
            aggregate["improved_count"] += 1
        else:
            aggregate["not_improved_count"] += 1
        aggregate["num_results_delta_sum"] += gain["num_results_delta"]
        aggregate["top_score_delta_sum"] += gain["top_score_delta"]

        report = {
            "case_index": idx,
            "case_name": case.name,
            "instruction": case.instruction,
            "failure_reason": case.failure_reason,
            "previous_queries": prev,
            "refined_output": refined,
            "validation_previous": {
                **prev_validation,
                "diversity_score": _diversity_score(prev),
                "retrieval_metrics": prev_retr,
            },
            "validation_refined": {
                **ref_validation,
                "diversity_score": _diversity_score(refined),
                "retrieval_metrics": ref_retr,
            },
            "retry_change": retry_change,
            "retrieval_gain": gain,
            "improved": improved,
        }

        print("=" * 110)
        print(json.dumps(report, ensure_ascii=False, indent=2))

    print("=" * 110)
    print("AGGREGATE")
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
