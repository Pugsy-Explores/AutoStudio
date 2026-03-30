#!/usr/bin/env python3
"""
Live exploratory evaluation for ExplorationScoper only.

Scope:
- Runs ExplorationScoper.scope(...) in isolation.
- Inputs are instruction + retrieval-derived candidates.
- No selector/analyzer/planner/exploration-engine usage.
"""

from __future__ import annotations

import ast
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from rank_bm25 import BM25Okapi

from agent.models.model_client import call_reasoning_model
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.exploration.exploration_scoper import ExplorationScoper
from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_SCOPER
from agent_v2.schemas.exploration import ExplorationCandidate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [PROJECT_ROOT / "agent", PROJECT_ROOT / "agent_v2"]
RNG = random.Random(7)


@dataclass(frozen=True)
class ScoperCase:
    bucket: str
    instruction: str
    query_seed: str
    relevant_k: int
    loose_k: int
    irrelevant_k: int


def _iter_python_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            rel = path.relative_to(PROJECT_ROOT).as_posix()
            if "/tests/" in rel or rel.endswith("/__init__.py"):
                continue
            files.append(path)
    return sorted(files)


def _tokenize(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-zA-Z0-9_]+", text.lower()) if t]


def _extract_signals(py_files: list[Path]) -> dict[str, list[str]]:
    funcs: set[str] = set()
    classes: set[str] = set()
    modules: set[str] = set()

    for path in py_files:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        parts = rel.replace(".py", "").split("/")
        for p in parts:
            low = p.lower()
            if any(k in low for k in ("exploration", "runtime", "prompt", "selector", "analyzer", "scoper", "query", "config", "dispatcher", "policy")):
                modules.add(low)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name and not node.name.startswith("_"):
                funcs.add(node.name)
            elif isinstance(node, ast.ClassDef) and node.name and not node.name.startswith("_"):
                classes.add(node.name)

    return {
        "functions": sorted(funcs),
        "classes": sorted(classes),
        "modules": sorted(modules),
    }


def _pick(vals: list[str], idx: int, fallback: str) -> str:
    if not vals:
        return fallback
    return vals[idx % len(vals)]


def _build_cases(signals: dict[str, list[str]]) -> list[ScoperCase]:
    funcs = signals["functions"]
    classes = signals["classes"]
    mods = signals["modules"]

    return [
        ScoperCase(
            bucket="easy",
            instruction=f"Find where `{_pick(funcs, 0, 'parse')}` is implemented and used.",
            query_seed=_pick(funcs, 0, "parse"),
            relevant_k=8,
            loose_k=3,
            irrelevant_k=2,
        ),
        ScoperCase(
            bucket="easy",
            instruction=f"Locate core logic for class `{_pick(classes, 0, 'ExplorationScoper')}`.",
            query_seed=_pick(classes, 0, "ExplorationScoper"),
            relevant_k=8,
            loose_k=3,
            irrelevant_k=2,
        ),
        ScoperCase(
            bucket="medium",
            instruction=f"Narrow candidates for `{_pick(mods, 0, 'exploration')}` retrieval flow.",
            query_seed=f"{_pick(mods, 0, 'exploration')} retrieval",
            relevant_k=7,
            loose_k=4,
            irrelevant_k=3,
        ),
        ScoperCase(
            bucket="medium",
            instruction=f"Scope files likely handling `{_pick(mods, 1, 'runtime')}` orchestration.",
            query_seed=f"{_pick(mods, 1, 'runtime')} orchestration",
            relevant_k=7,
            loose_k=4,
            irrelevant_k=3,
        ),
        ScoperCase(
            bucket="hard",
            instruction="Identify files central to candidate narrowing between scoping and selection stages.",
            query_seed="scoper selector candidate narrowing",
            relevant_k=6,
            loose_k=5,
            irrelevant_k=4,
        ),
        ScoperCase(
            bucket="hard",
            instruction="Trace high-signal filtering in exploration with overlapping query/selection modules.",
            query_seed="exploration query selector scoper filtering",
            relevant_k=6,
            loose_k=5,
            irrelevant_k=4,
        ),
        ScoperCase(
            bucket="adversarial",
            instruction="Fix this; something in exploration seems wrong.",
            query_seed="exploration",
            relevant_k=4,
            loose_k=4,
            irrelevant_k=7,
        ),
        ScoperCase(
            bucket="adversarial",
            instruction="Performance issue somewhere, not sure where.",
            query_seed="performance issue",
            relevant_k=3,
            loose_k=5,
            irrelevant_k=7,
        ),
    ]


def _build_bm25(py_files: list[Path]) -> tuple[list[str], list[list[str]], BM25Okapi]:
    docs: list[str] = []
    toks: list[list[str]] = []
    for path in py_files:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        body = f"{rel}\n{content[:12000]}"
        tt = _tokenize(body)
        if not tt:
            continue
        docs.append(rel)
        toks.append(tt)
    bm25 = BM25Okapi(toks if toks else [["empty"]])
    return docs, toks, bm25


def _extract_symbols_for_file(path: Path, max_items: int = 3) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and getattr(node, "name", None):
            nm = str(node.name)
            if nm and not nm.startswith("_"):
                out.append(nm)
        if len(out) >= max_items:
            break
    return out


def _mk_candidate(rel: str, score: float, source: str) -> ExplorationCandidate:
    abs_path = PROJECT_ROOT / rel
    symbols = _extract_symbols_for_file(abs_path, max_items=1)
    symbol = symbols[0] if symbols else None
    snippet = f"retrieval_score={round(score, 4)} source={source} file={rel}"
    return ExplorationCandidate(
        file_path=str(abs_path),
        symbol=symbol,
        snippet=snippet,
        source=source,
    )


def _build_candidate_pool(case: ScoperCase, docs: list[str], bm25: BM25Okapi) -> list[ExplorationCandidate]:
    q_tokens = _tokenize(case.query_seed)
    scores = list(bm25.get_scores(q_tokens)) if q_tokens else [0.0 for _ in docs]
    ranked = sorted(enumerate(scores), key=lambda x: float(x[1]), reverse=True)
    positives = [x for x in ranked if float(x[1]) > 0]
    non_pos = [x for x in ranked if float(x[1]) <= 0]

    rel_rows = positives[: case.relevant_k]
    loose_rows = positives[case.relevant_k : case.relevant_k + case.loose_k]
    irr_pool = non_pos if non_pos else ranked[-max(20, case.irrelevant_k * 3) :]
    irr_rows = RNG.sample(irr_pool, k=min(case.irrelevant_k, len(irr_pool))) if irr_pool else []

    candidates: list[ExplorationCandidate] = []
    seen: set[str] = set()

    for idx, score in rel_rows:
        rel = docs[idx]
        if rel in seen:
            continue
        seen.add(rel)
        candidates.append(_mk_candidate(rel, float(score), source="graph"))

    for idx, score in loose_rows:
        rel = docs[idx]
        if rel in seen:
            continue
        seen.add(rel)
        candidates.append(_mk_candidate(rel, float(score), source="grep"))

    for idx, score in irr_rows:
        rel = docs[idx]
        if rel in seen:
            continue
        seen.add(rel)
        candidates.append(_mk_candidate(rel, float(score), source="vector"))

    RNG.shuffle(candidates)
    return candidates


def _to_display_rows(candidates: list[ExplorationCandidate]) -> list[dict]:
    rows: list[dict] = []
    for i, c in enumerate(candidates):
        rows.append(
            {
                "index": i,
                "file_path": c.file_path,
                "symbol": c.symbol,
                "source": c.source,
            }
        )
    return rows


def _selected_dedupe_indices(
    scoper: ExplorationScoper,
    candidates: list[ExplorationCandidate],
    selected: list[ExplorationCandidate],
) -> list[int]:
    payload, _ = scoper._aggregate_payload_by_file_path(candidates)  # noqa: SLF001
    selected_paths = {c.file_path for c in selected}
    out: list[int] = []
    for row in payload:
        if str(row.get("file_path")) in selected_paths:
            out.append(int(row.get("index")))
    return sorted(out)


def _print_case_header(case: ScoperCase) -> None:
    print("=" * 100)
    print(f"BUCKET: {case.bucket}")
    print(f"INSTRUCTION: {case.instruction}")
    print(f"QUERY_SEED: {case.query_seed}")


def main() -> int:
    py_files = _iter_python_files()
    signals = _extract_signals(py_files)
    docs, _tokens, bm25 = _build_bm25(py_files)
    cases = _build_cases(signals)

    print("# Live ExplorationScoper Evaluation")
    print(f"# Project root: {PROJECT_ROOT}")
    print(f"# Python files indexed: {len(py_files)}")
    print(f"# BM25 docs: {len(docs)}")
    print(
        f"# Signals: functions={len(signals['functions'])}, classes={len(signals['classes'])}, modules={len(signals['modules'])}"
    )

    model_name = get_prompt_model_name_for_task(EXPLORATION_TASK_SCOPER)
    scoper = ExplorationScoper(
        llm_generate=lambda prompt: call_reasoning_model(prompt, task_name=EXPLORATION_TASK_SCOPER),
        model_name=model_name,
    )

    stable_count = 0
    for case in cases:
        candidates = _build_candidate_pool(case, docs, bm25)
        if not candidates:
            continue

        selected_1 = scoper.scope(case.instruction, candidates)
        selected_2 = scoper.scope(case.instruction, candidates)

        indices_1 = _selected_dedupe_indices(scoper, candidates, selected_1)
        indices_2 = _selected_dedupe_indices(scoper, candidates, selected_2)
        is_stable = indices_1 == indices_2
        if is_stable:
            stable_count += 1

        _print_case_header(case)
        print("INPUT_CANDIDATES:")
        print(json.dumps(_to_display_rows(candidates), indent=2, ensure_ascii=False))
        print("SELECTED_INDICES:")
        print(json.dumps(indices_1, ensure_ascii=False))
        print("BEFORE_CANDIDATE_SET:")
        print(json.dumps(sorted({c.file_path for c in candidates}), indent=2, ensure_ascii=False))
        print("AFTER_CANDIDATE_SET:")
        print(json.dumps(sorted({c.file_path for c in selected_1}), indent=2, ensure_ascii=False))
        print(
            "EVAL_NOTES:"
            + json.dumps(
                {
                    "removed_clearly_irrelevant_signal": len(selected_1) < len(candidates),
                    "avoids_over_pruning": len(selected_1) > 0,
                    "stable_across_two_runs": is_stable,
                },
                ensure_ascii=False,
            )
        )

    print("=" * 100)
    print(
        json.dumps(
            {
                "summary": {
                    "total_cases": len(cases),
                    "stable_cases_two_runs": stable_count,
                }
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

