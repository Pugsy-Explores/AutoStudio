#!/usr/bin/env python3
"""
Live exploratory evaluation for QueryIntentParser only.

Non-goals:
- No exploration engine
- No selector/analyzer/planner
- No strict correctness assertions

What this script does:
1) Dynamically derives symbols/modules/files from current codebase.
2) Builds bucketed instructions (easy/medium/hard/adversarial).
3) Runs live QueryIntentParser.parse(...) calls.
4) Runs retry/failure-aware refinement cases with previous_queries + failure_reason.
5) Prints parser outputs and lightweight validation diagnostics.
"""

from __future__ import annotations

import ast
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from agent.models.model_client import call_reasoning_model_messages
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_QUERY_INTENT
from agent_v2.exploration.query_intent_parser import QueryIntentParser


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PYTHON_SCAN_ROOTS = [
    PROJECT_ROOT / "agent",
    PROJECT_ROOT / "agent_v2",
]

MODULE_HINT_KEYWORDS = (
    "service",
    "controller",
    "utils",
    "util",
    "config",
    "router",
    "dispatcher",
    "runtime",
    "exploration",
    "retrieval",
    "prompt",
    "model",
    "policy",
    "validator",
    "planner",
    "selector",
    "analyzer",
)

FAILURE_REASONS = ("no_results", "too_broad", "wrong_abstraction", "ambiguous_intent")


@dataclass(frozen=True)
class Case:
    bucket: str
    instruction: str
    previous_queries: dict | None = None
    failure_reason: str | None = None


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in PYTHON_SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            if "/tests/" in rel or rel.endswith("/__init__.py"):
                continue
            files.append(path)
    return sorted(files)


def _extract_code_signals() -> dict[str, list[str]]:
    function_names: set[str] = set()
    class_names: set[str] = set()
    file_names: set[str] = set()
    module_hints: set[str] = set()

    for path in _iter_python_files():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        file_names.add(path.name)

        for part in rel.replace(".py", "").split("/"):
            p = part.lower()
            if any(k in p for k in MODULE_HINT_KEYWORDS):
                module_hints.add(p)

        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name and not node.name.startswith("_"):
                    function_names.add(node.name)
            elif isinstance(node, ast.ClassDef):
                if node.name and not node.name.startswith("_"):
                    class_names.add(node.name)

    return {
        "functions": sorted(function_names),
        "classes": sorted(class_names),
        "files": sorted(file_names),
        "modules": sorted(module_hints),
    }


def _pick(values: list[str], idx: int, fallback: str) -> str:
    if not values:
        return fallback
    return values[idx % len(values)]


def build_cases(signals: dict[str, list[str]]) -> list[Case]:
    funcs = signals["functions"]
    classes = signals["classes"]
    files = signals["files"]
    modules = signals["modules"]

    easy = [
        Case("easy", f"Find where `{_pick(funcs, 0, 'run')}` is defined"),
        Case("easy", f"Locate class `{_pick(classes, 0, 'QueryIntentParser')}`"),
        Case("easy", f"Search for config in `{_pick(files, 0, 'config.py')}`"),
    ]

    medium = [
        Case("medium", f"Where is `{_pick(modules, 0, 'authentication')}` handled?"),
        Case("medium", f"How is data fetched for `{_pick(modules, 1, 'exploration')}`?"),
        Case("medium", f"Find logic for `{_pick(modules, 2, 'retrieval')}` feature"),
    ]

    hard = [
        Case(
            "hard",
            f"Trace flow from API to database for `{_pick(modules, 3, 'prompt')}`",
        ),
        Case(
            "hard",
            f"Find all components involved in `{_pick(modules, 4, 'exploration')}` workflow",
        ),
        Case(
            "hard",
            f"Where does request processing start and end for `{_pick(modules, 5, 'runtime')}`?",
        ),
    ]

    adversarial = [
        Case("adversarial", f"Fix this in `{_pick(modules, 6, 'exploration')}`"),
        Case("adversarial", f"Something is slow in `{_pick(modules, 7, 'retrieval')}`"),
        Case("adversarial", f"`{_pick(modules, 8, 'prompt')}` is broken"),
    ]

    retry_seed = Case(
        "retry-seed",
        f"Find how `{_pick(modules, 9, 'exploration')}` is implemented end-to-end",
    )
    retry = [retry_seed]
    for reason in FAILURE_REASONS:
        retry.append(
            Case(
                "retry",
                retry_seed.instruction,
                failure_reason=reason,
            )
        )

    return easy + medium + hard + adversarial + retry


def _has_duplicates(items: list[str]) -> bool:
    return len(items) != len(set(items))


def _validate_output(output: dict) -> dict:
    fields = ("symbols", "keywords", "regex_patterns", "intents")
    valid_schema = all(isinstance(output.get(k, []), list) for k in fields)
    dupes = {k: _has_duplicates(output.get(k, [])) for k in fields}
    return {
        "valid_json": True,
        "schema_ok": valid_schema,
        "duplicates": dupes,
    }


def _meaningful_retry_change(prev: dict, cur: dict) -> dict:
    fields = ("symbols", "keywords", "regex_patterns", "intents")
    unchanged = []
    overlap = {}
    for f in fields:
        p = set(prev.get(f, []))
        c = set(cur.get(f, []))
        if p == c:
            unchanged.append(f)
        overlap[f] = sorted(p & c)
    return {
        "fields_unchanged": unchanged,
        "overlap": overlap,
        "overlap_reduction": {
            f: max(0, len(set(prev.get(f, [])) - set(cur.get(f, [])))) for f in fields
        },
        "set_difference_sizes": {
            f: len(set(cur.get(f, [])) - set(prev.get(f, []))) for f in fields
        },
        "changed_meaningfully": len(unchanged) < len(fields),
    }


def _diversity_score(output: dict) -> int:
    vals = []
    for f in ("symbols", "keywords", "regex_patterns"):
        vals.extend([str(x).strip() for x in output.get(f, []) if str(x).strip()])
    return len(set(vals))


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-zA-Z0-9_]+", text.lower()) if t]


def _build_bm25_index() -> tuple[list[str], list[list[str]], BM25Okapi]:
    docs: list[str] = []
    doc_tokens: list[list[str]] = []
    for path in _iter_python_files():
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        body = f"{rel}\n{content[:12000]}"
        toks = _tokenize(body)
        if not toks:
            continue
        docs.append(rel)
        doc_tokens.append(toks)
    bm25 = BM25Okapi(doc_tokens if doc_tokens else [["empty"]])
    return docs, doc_tokens, bm25


def _retrieval_metrics(output: dict, bm25_docs: list[str], bm25: BM25Okapi) -> dict:
    query_terms: list[str] = []
    for f in ("symbols", "keywords", "regex_patterns", "intents"):
        query_terms.extend([str(x).strip() for x in output.get(f, []) if str(x).strip()])
    q_tokens = _tokenize(" ".join(query_terms))
    if not q_tokens:
        return {"num_results": 0, "top_score": 0.0, "score_distribution": {"mean_top5": 0.0, "std_top5": 0.0}}
    scores = bm25.get_scores(q_tokens)
    top = sorted((float(s) for s in scores), reverse=True)[:5]
    if not top:
        return {"num_results": 0, "top_score": 0.0, "score_distribution": {"mean_top5": 0.0, "std_top5": 0.0}}
    positive = [s for s in scores if float(s) > 0]
    mean = sum(top) / len(top)
    var = sum((x - mean) ** 2 for x in top) / len(top)
    return {
        "num_results": len(positive),
        "top_score": top[0],
        "score_distribution": {"mean_top5": mean, "std_top5": math.sqrt(var)},
        "top_docs": [
            bm25_docs[i]
            for i, _s in sorted(enumerate(scores), key=lambda x: float(x[1]), reverse=True)[:3]
        ]
        if bm25_docs
        else [],
    }


def _print_case(case: Case, output: dict, validation: dict) -> None:
    print("=" * 100)
    print(f"BUCKET: {case.bucket}")
    print(f"INSTRUCTION: {case.instruction}")
    print(f"PREVIOUS_QUERIES: {json.dumps(case.previous_queries, ensure_ascii=False) if case.previous_queries else 'None'}")
    print(f"FAILURE_REASON: {case.failure_reason or 'None'}")
    print("PARSER_OUTPUT:")
    print(json.dumps(output, indent=2, ensure_ascii=False))
    print("VALIDATION:")
    print(json.dumps(validation, indent=2, ensure_ascii=False))


def main() -> int:
    bm25_docs, _doc_tokens, bm25 = _build_bm25_index()
    print(f"# BM25 corpus docs: {len(bm25_docs)}")

    signals = _extract_code_signals()
    print("# Live QueryIntentParser Evaluation")
    print(f"# Project root: {PROJECT_ROOT}")
    print(
        f"# Signals extracted: functions={len(signals['functions'])}, classes={len(signals['classes'])}, files={len(signals['files'])}, modules={len(signals['modules'])}"
    )

    model_name = get_prompt_model_name_for_task(EXPLORATION_TASK_QUERY_INTENT)
    parser = QueryIntentParser(
        llm_generate_messages=lambda messages: call_reasoning_model_messages(
            messages, task_name=EXPLORATION_TASK_QUERY_INTENT
        ),
        model_name=model_name,
    )

    cases = build_cases(signals)
    last_output: dict | None = None
    retry_failures: list[dict] = []

    for case in cases:
        prev = case.previous_queries
        if case.bucket == "retry" and prev is None:
            prev = last_output
        parsed = parser.parse(
            case.instruction,
            previous_queries=prev,
            failure_reason=case.failure_reason,
        )
        out = parsed.model_dump()
        validation = _validate_output(out)
        validation["diversity_score"] = _diversity_score(out)
        # Stability check: same input should be stable enough run-to-run.
        parsed_2 = parser.parse(
            case.instruction,
            previous_queries=prev,
            failure_reason=case.failure_reason,
        )
        out_2 = parsed_2.model_dump()
        validation["stability_check"] = {
            "same_input_same_output": out == out_2,
        }
        validation["retrieval_metrics"] = _retrieval_metrics(out, bm25_docs, bm25)
        if prev:
            validation["retry_change"] = _meaningful_retry_change(prev, out)
            prev_retr = _retrieval_metrics(prev, bm25_docs, bm25)
            cur_retr = validation["retrieval_metrics"]
            gain = {
                "num_results_delta": cur_retr["num_results"] - prev_retr["num_results"],
                "top_score_delta": round(cur_retr["top_score"] - prev_retr["top_score"], 6),
            }
            validation["retrieval_gain"] = gain
            if case.bucket == "retry" and gain["num_results_delta"] <= 0 and gain["top_score_delta"] <= 0:
                retry_failures.append(
                    {
                        "instruction": case.instruction,
                        "failure_reason": case.failure_reason,
                        "retrieval_gain": gain,
                    }
                )
        _print_case(case, out, validation)
        last_output = out

    print("=" * 100)
    print("RETRY_NON_IMPROVED_CASES:")
    print(json.dumps(retry_failures, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
