#!/usr/bin/env python3
"""
Live integration evaluation for:
QueryIntentParser -> Search -> Scoper -> Selector

Scope:
- Full pipeline execution with real repository signals.
- No analyzer/planner stages.
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from agent.models.model_client import call_reasoning_model, call_reasoning_model_messages
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.exploration.candidate_selector import CandidateSelector
from agent_v2.exploration.exploration_scoper import ExplorationScoper
from agent_v2.exploration.exploration_task_names import (
    EXPLORATION_TASK_QUERY_INTENT,
    EXPLORATION_TASK_SCOPER,
    EXPLORATION_TASK_SELECTOR_BATCH,
)
from agent_v2.exploration.query_intent_parser import QueryIntentParser
from agent_v2.schemas.exploration import ExplorationCandidate, QueryIntent


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [PROJECT_ROOT / "agent", PROJECT_ROOT / "agent_v2"]
RNG = random.Random(17)
FAILURE_REASONS = ("no_results", "too_broad", "wrong_abstraction", "ambiguous_intent")


@dataclass(frozen=True)
class Case:
    bucket: str
    instruction: str
    relevance_seed: str
    retry: bool = False


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            rel = p.relative_to(PROJECT_ROOT).as_posix()
            if "/tests/" in rel or rel.endswith("/__init__.py"):
                continue
            files.append(p)
    return sorted(files)


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-zA-Z0-9_]+", text.lower()) if t]


def _extract_signals(py_files: list[Path]) -> dict[str, list[str]]:
    funcs: set[str] = set()
    classes: set[str] = set()
    modules: set[str] = set()
    for p in py_files:
        rel = p.relative_to(PROJECT_ROOT).as_posix()
        for part in rel.replace(".py", "").split("/"):
            low = part.lower()
            if any(
                k in low
                for k in (
                    "exploration",
                    "retrieval",
                    "selector",
                    "scoper",
                    "query",
                    "runtime",
                    "prompt",
                    "config",
                )
            ):
                modules.add(low)
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.FunctionDef) and n.name and not n.name.startswith("_"):
                funcs.add(n.name)
            elif isinstance(n, ast.ClassDef) and n.name and not n.name.startswith("_"):
                classes.add(n.name)
    return {"functions": sorted(funcs), "classes": sorted(classes), "modules": sorted(modules)}


def _pick(vals: list[str], idx: int, fallback: str) -> str:
    if not vals:
        return fallback
    return vals[idx % len(vals)]


def _build_cases(signals: dict[str, list[str]]) -> list[Case]:
    funcs = signals["functions"]
    classes = signals["classes"]
    mods = signals["modules"]
    return [
        Case("easy", f"Find where `{_pick(funcs, 0, 'parse')}` is defined and used.", _pick(funcs, 0, "parse")),
        Case("medium", f"Where is `{_pick(mods, 0, 'exploration')}` retrieval handled?", _pick(mods, 0, "exploration")),
        Case("hard", "Trace workflow from query intent to final candidate selection in exploration.", "query selector scoper exploration"),
        Case("adversarial", "Something is broken but not sure where.", "unknown"),
        Case("retry", f"Find how `{_pick(mods, 2, 'exploration')}` is implemented end-to-end.", _pick(mods, 2, "exploration"), retry=True),
    ]


def _build_index(py_files: list[Path]) -> tuple[list[str], list[str], list[list[str]], BM25Okapi]:
    rels: list[str] = []
    texts: list[str] = []
    toks: list[list[str]] = []
    for p in py_files:
        rel = p.relative_to(PROJECT_ROOT).as_posix()
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        text = f"{rel}\n{content[:14000]}"
        tt = _tokenize(text)
        if not tt:
            continue
        rels.append(rel)
        texts.append(text)
        toks.append(tt)
    bm25 = BM25Okapi(toks if toks else [["empty"]])
    return rels, texts, toks, bm25


def _extract_symbols(path: Path, cap: int = 2) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return out
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and getattr(n, "name", None):
            nm = str(n.name)
            if nm and not nm.startswith("_"):
                out.append(nm)
        if len(out) >= cap:
            break
    return out


def _query_terms(intent: QueryIntent) -> list[str]:
    terms: list[str] = []
    terms.extend([x for x in intent.symbols if x.strip()])
    terms.extend([x for x in intent.keywords if x.strip()])
    terms.extend([x for x in intent.regex_patterns if x.strip()])
    terms.extend([x for x in intent.intents if x.strip()])
    return terms


def _search_candidates(
    intent: QueryIntent,
    rels: list[str],
    texts: list[str],
    bm25: BM25Okapi,
    *,
    top_k: int = 24,
) -> list[ExplorationCandidate]:
    terms = _query_terms(intent)
    if not terms:
        return []
    q_tokens = _tokenize(" ".join(terms))
    if not q_tokens:
        return []
    scores = list(bm25.get_scores(q_tokens))
    ranked = sorted(enumerate(scores), key=lambda x: float(x[1]), reverse=True)[:top_k]
    out: list[ExplorationCandidate] = []
    seen: set[str] = set()
    for idx, score in ranked:
        rel = rels[idx]
        abs_path = PROJECT_ROOT / rel
        if str(abs_path) in seen:
            continue
        seen.add(str(abs_path))
        syms = _extract_symbols(abs_path, cap=1)
        source = "graph" if idx % 3 == 0 else ("grep" if idx % 3 == 1 else "vector")
        out.append(
            ExplorationCandidate(
                file_path=str(abs_path),
                symbol=syms[0] if syms else None,
                source=source,
                snippet=f"score={round(float(score),4)} rel={rel}",
            )
        )
    return out


def _relevance_set(seed: str, rels: list[str], texts: list[str], bm25: BM25Okapi, top_k: int = 20) -> set[str]:
    q = _tokenize(seed)
    if not q:
        return set()
    scores = list(bm25.get_scores(q))
    ranked = sorted(enumerate(scores), key=lambda x: float(x[1]), reverse=True)[:top_k]
    return {str(PROJECT_ROOT / rels[i]) for i, s in ranked if float(s) > 0}


def _rows(cands: list[ExplorationCandidate]) -> list[dict]:
    rows: list[dict] = []
    for i, c in enumerate(cands):
        rows.append(
            {
                "index": i,
                "file_path": c.file_path,
                "symbol": c.symbol,
                "source": c.source,
            }
        )
    return rows


def _precision(cands: list[ExplorationCandidate], relevant: set[str]) -> float:
    if not cands:
        return 0.0
    hit = sum(1 for c in cands if c.file_path in relevant)
    return hit / len(cands)


def _selected_indices(full: list[ExplorationCandidate], selected: list[ExplorationCandidate]) -> list[int]:
    keys = {(c.file_path, c.symbol or "", c.source) for c in selected}
    out: list[int] = []
    for i, c in enumerate(full):
        if (c.file_path, c.symbol or "", c.source) in keys:
            out.append(i)
    return out


def _run_once(
    case: Case,
    parser: QueryIntentParser,
    scoper: ExplorationScoper,
    selector: CandidateSelector,
    rels: list[str],
    texts: list[str],
    bm25: BM25Okapi,
) -> dict:
    parsed = parser.parse(case.instruction)
    search_cands = _search_candidates(parsed, rels, texts, bm25)
    scoped = scoper.scope(case.instruction, search_cands) if search_cands else []
    selected = (
        selector.select_batch(
            case.instruction,
            ", ".join(parsed.intents) if parsed.intents else "no intent",
            scoped,
            seen_files=set(),
            limit=min(10, len(scoped)),
        )
        if scoped
        else []
    )
    selected = selected or []

    retry_trace = None
    if case.retry:
        # Simulate failure-driven refinement and rerun parser/search/scoper/selector.
        fr = RNG.choice(FAILURE_REASONS)
        parsed_r = parser.parse(
            case.instruction,
            previous_queries=parsed,
            failure_reason=fr,
        )
        search_r = _search_candidates(parsed_r, rels, texts, bm25)
        scoped_r = scoper.scope(case.instruction, search_r) if search_r else []
        selected_r = (
            selector.select_batch(
                case.instruction,
                ", ".join(parsed_r.intents) if parsed_r.intents else "no intent",
                scoped_r,
                seen_files=set(),
                limit=min(10, len(scoped_r)),
            )
            if scoped_r
            else []
        )
        selected_r = selected_r or []
        retry_trace = {
            "failure_reason": fr,
            "queries": parsed_r.model_dump(),
            "candidates": _rows(search_r),
            "scoped_indices": _selected_indices(search_r, scoped_r),
            "selected_indices": _selected_indices(scoped_r, selected_r),
            "selected_content": _rows(selected_r),
            "selected_paths": sorted({c.file_path for c in selected_r}),
        }

    return {
        "queries": parsed.model_dump(),
        "candidates": _rows(search_cands),
        "scoped_indices": _selected_indices(search_cands, scoped),
        "scoped_content": _rows(scoped),
        "selected_indices": _selected_indices(scoped, selected),
        "selected_content": _rows(selected),
        "selected_paths": sorted({c.file_path for c in selected}),
        "scoped": scoped,
        "selected": selected,
        "retry_trace": retry_trace,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-cases", type=int, default=5, help="Maximum number of generated cases to run.")
    args = ap.parse_args()

    py_files = _iter_python_files()
    signals = _extract_signals(py_files)
    cases = _build_cases(signals)[: max(1, int(args.max_cases))]
    rels, texts, _toks, bm25 = _build_index(py_files)

    print("# Live Exploration Integration Evaluation")
    print("# Pipeline: QueryIntentParser -> Search -> Scoper -> Selector")
    print(f"# Project root: {PROJECT_ROOT}")
    print(f"# Indexed files: {len(rels)}")

    parser = QueryIntentParser(
        llm_generate_messages=lambda messages: call_reasoning_model_messages(
            messages, task_name=EXPLORATION_TASK_QUERY_INTENT
        ),
        model_name=get_prompt_model_name_for_task(EXPLORATION_TASK_QUERY_INTENT),
    )
    scoper = ExplorationScoper(
        llm_generate=lambda p: call_reasoning_model(p, task_name=EXPLORATION_TASK_SCOPER),
        model_name=get_prompt_model_name_for_task(EXPLORATION_TASK_SCOPER),
    )
    selector = CandidateSelector(
        llm_generate=lambda p: call_reasoning_model(p, task_name=EXPLORATION_TASK_SELECTOR_BATCH),
        model_name_batch=get_prompt_model_name_for_task(EXPLORATION_TASK_SELECTOR_BATCH),
    )

    stable = 0
    retry_improved = 0
    retry_total = 0
    for case in cases:
        relevant = _relevance_set(case.relevance_seed, rels, texts, bm25)
        r1 = _run_once(case, parser, scoper, selector, rels, texts, bm25)
        # Lightweight stability check on final selection only (re-run selector on same scoped set).
        selector_repeat = (
            selector.select_batch(
                case.instruction,
                ", ".join(r1["queries"].get("intents", [])) or "no intent",
                [
                    ExplorationCandidate(
                        file_path=str(x.get("file_path") or ""),
                        symbol=(str(x.get("symbol")) if x.get("symbol") else None),
                        source=str(x.get("source") or "grep"),
                        snippet=None,
                    )
                    for x in r1["scoped_content"]
                    if str(x.get("file_path") or "").strip()
                ],
                seen_files=set(),
                limit=min(10, len(r1["scoped_content"])),
            )
            if r1["scoped_content"]
            else []
        )
        selector_repeat = selector_repeat or []
        st = sorted({c.file_path for c in selector_repeat}) == r1["selected_paths"]
        if st:
            stable += 1

        p_search = _precision(r1["scoped"], relevant) if r1["scoped"] else 0.0
        p_select = _precision(r1["selected"], relevant) if r1["selected"] else 0.0
        final_has_relevant = any(c.file_path in relevant for c in r1["selected"])
        scoper_reduced_noise = len(r1["scoped"]) < len(r1["candidates"])
        selector_improved_precision = p_select >= p_search

        retry_outcome = None
        if r1["retry_trace"] is not None:
            retry_total += 1
            before_prec = p_select
            retry_sel_paths = set(r1["retry_trace"]["selected_paths"])
            after_prec = (
                len([p for p in retry_sel_paths if p in relevant]) / len(retry_sel_paths)
                if retry_sel_paths
                else 0.0
            )
            improved = after_prec >= before_prec and len(retry_sel_paths) > 0
            if improved:
                retry_improved += 1
            retry_outcome = {
                "retry_improved_results": improved,
                "before_precision": round(before_prec, 4),
                "after_precision": round(after_prec, 4),
                "trace": r1["retry_trace"],
            }

        print("=" * 100)
        print(f"BUCKET: {case.bucket}")
        print(f"INSTRUCTION: {case.instruction}")
        print("QUERIES:")
        print(json.dumps(r1["queries"], indent=2, ensure_ascii=False))
        print("CANDIDATES:")
        print(json.dumps(r1["candidates"], indent=2, ensure_ascii=False))
        print("SCOPED:")
        print(json.dumps({"indices": r1["scoped_indices"], "content": r1["scoped_content"]}, indent=2, ensure_ascii=False))
        print("SELECTED:")
        print(json.dumps({"indices": r1["selected_indices"], "content": r1["selected_content"]}, indent=2, ensure_ascii=False))
        print(
            "OUTCOME_EVAL:"
            + json.dumps(
                {
                    "final_contains_relevant_code": final_has_relevant,
                    "scoper_reduced_noise": scoper_reduced_noise,
                    "selector_improved_precision": selector_improved_precision,
                    "stable_across_runs": st,
                },
                ensure_ascii=False,
            )
        )
        if retry_outcome is not None:
            print("RETRY_OUTCOME:")
            print(json.dumps(retry_outcome, indent=2, ensure_ascii=False))

    print("=" * 100)
    print(
        json.dumps(
            {
                "summary": {
                    "total_cases": len(cases),
                    "stable_cases": stable,
                    "retry_cases": retry_total,
                    "retry_improved": retry_improved,
                }
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

