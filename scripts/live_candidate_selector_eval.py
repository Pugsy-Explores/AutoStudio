#!/usr/bin/env python3
"""
Live exploratory evaluation for CandidateSelector only.

Scope:
- Runs CandidateSelector.select_batch(...) in isolation.
- Input is instruction + pre-scoped candidates.
- No scoper/search/analyzer/planner logic in this script.
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path

from agent.models.model_client import call_reasoning_model
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.exploration.candidate_selector import CandidateSelector
from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_SELECTOR_BATCH
from agent_v2.schemas.exploration import ExplorationCandidate


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [PROJECT_ROOT / "agent", PROJECT_ROOT / "agent_v2"]
RNG = random.Random(11)


@dataclass(frozen=True)
class SelectorCase:
    bucket: str
    instruction: str
    intent: str
    tags: tuple[str, ...]
    limit: int


@dataclass(frozen=True)
class FileRecord:
    file_path: str
    tags: tuple[str, ...]
    symbols: tuple[str, ...]


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


def _extract_symbols(path: Path, cap: int = 4) -> tuple[str, ...]:
    names: list[str] = []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return tuple()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            nm = getattr(node, "name", "")
            if nm and not nm.startswith("_") and nm not in names:
                names.append(nm)
        if len(names) >= cap:
            break
    return tuple(names)


def _build_records(py_files: list[Path]) -> list[FileRecord]:
    records: list[FileRecord] = []
    for path in py_files:
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        parts = _tokenize(rel.replace(".py", ""))
        tags = tuple(sorted(set(parts)))
        symbols = _extract_symbols(path)
        if not symbols:
            continue
        records.append(
            FileRecord(
                file_path=str(path),
                tags=tags,
                symbols=symbols,
            )
        )
    return records


def _overlap_score(tags: tuple[str, ...], target: tuple[str, ...]) -> int:
    s = set(tags)
    t = set(target)
    return len(s & t)


def _to_candidate(rec: FileRecord, *, source: str, symbol_idx: int = 0) -> ExplorationCandidate:
    symbol = rec.symbols[symbol_idx % len(rec.symbols)]
    snippet = f"file={Path(rec.file_path).name} tags={','.join(rec.tags[:6])}"
    return ExplorationCandidate(file_path=rec.file_path, symbol=symbol, source=source, snippet=snippet)


def _build_candidate_pool(case: SelectorCase, records: list[FileRecord]) -> list[ExplorationCandidate]:
    target = case.tags
    ranked = sorted(records, key=lambda r: _overlap_score(r.tags, target), reverse=True)
    high = [r for r in ranked if _overlap_score(r.tags, target) >= 2][:6]
    medium = [r for r in ranked if _overlap_score(r.tags, target) == 1][:5]
    low = [r for r in ranked if _overlap_score(r.tags, target) == 0]

    irr = RNG.sample(low, k=min(5, len(low)))

    cands: list[ExplorationCandidate] = []
    for r in high:
        cands.append(_to_candidate(r, source="graph", symbol_idx=0))
        # duplicated signal: same file/symbol but different source channel
        cands.append(_to_candidate(r, source="grep", symbol_idx=0))
    for r in medium:
        cands.append(_to_candidate(r, source="vector", symbol_idx=1 if len(r.symbols) > 1 else 0))
    for r in irr:
        cands.append(_to_candidate(r, source="vector", symbol_idx=0))

    RNG.shuffle(cands)
    return cands


def _cases() -> list[SelectorCase]:
    return [
        SelectorCase(
            bucket="easy",
            instruction="Select candidates most central to exploration scoping and candidate selection logic.",
            intent="exploration scoping selection core logic",
            tags=("exploration", "scoper", "selector"),
            limit=8,
        ),
        SelectorCase(
            bucket="easy",
            instruction="Find core files for query intent parsing and refinement behavior.",
            intent="query intent parser refinement",
            tags=("query", "intent", "parser"),
            limit=8,
        ),
        SelectorCase(
            bucket="medium",
            instruction="Choose files likely responsible for runtime orchestration around exploration.",
            intent="runtime orchestration exploration flow",
            tags=("runtime", "exploration", "runner"),
            limit=9,
        ),
        SelectorCase(
            bucket="medium",
            instruction="Pick high-signal prompt system files tied to retrieval and selection.",
            intent="prompt retrieval selector",
            tags=("prompt", "retrieval", "selector"),
            limit=9,
        ),
        SelectorCase(
            bucket="hard",
            instruction="Distinguish central filtering logic from peripheral observability helpers in exploration.",
            intent="central filtering exploration not observability",
            tags=("exploration", "candidate", "filter"),
            limit=10,
        ),
        SelectorCase(
            bucket="hard",
            instruction="Select modules that directly drive candidate narrowing, not adjacent tooling.",
            intent="candidate narrowing direct modules",
            tags=("candidate", "selector", "scoper"),
            limit=10,
        ),
        SelectorCase(
            bucket="adversarial",
            instruction="Something is broken; pick likely deep-inspection candidates.",
            intent="ambiguous bug triage",
            tags=("broken", "issue", "unknown"),
            limit=8,
        ),
        SelectorCase(
            bucket="adversarial",
            instruction="Prioritize genuinely relevant candidates despite misleading similarly named files.",
            intent="misleading names high precision",
            tags=("selector", "prompt", "model"),
            limit=9,
        ),
    ]


def _to_rows(cands: list[ExplorationCandidate]) -> list[dict]:
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


def _indices_for_selected(
    all_candidates: list[ExplorationCandidate], selected: list[ExplorationCandidate]
) -> list[int]:
    selected_keys = {(c.file_path, c.symbol or "", c.source) for c in selected}
    out: list[int] = []
    for i, c in enumerate(all_candidates):
        key = (c.file_path, c.symbol or "", c.source)
        if key in selected_keys:
            out.append(i)
    return out


def _pairwise_stability_matrix(index_sets: list[list[int]]) -> list[list[float]]:
    out: list[list[float]] = []
    sets = [set(x) for x in index_sets]
    for a in sets:
        row: list[float] = []
        for b in sets:
            if not a and not b:
                row.append(1.0)
                continue
            den = len(a | b)
            row.append(round((len(a & b) / den) if den else 1.0, 4))
        out.append(row)
    return out


def _index_set_variance(index_sets: list[list[int]]) -> dict:
    frozen = [tuple(sorted(x)) for x in index_sets]
    unique = sorted(set(frozen))
    counts = {str(list(u)): frozen.count(u) for u in unique}
    return {
        "unique_index_sets": len(unique),
        "set_frequency": counts,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=2, help="Number of live selector trials per case.")
    ap.add_argument("--max-cases", type=int, default=8, help="Max number of cases to run.")
    args = ap.parse_args()

    py_files = _iter_python_files()
    records = _build_records(py_files)
    cases = _cases()[: max(1, int(args.max_cases))]

    print("# Live CandidateSelector Evaluation")
    print(f"# Project root: {PROJECT_ROOT}")
    print(f"# Python files scanned: {len(py_files)}")
    print(f"# File records with symbols: {len(records)}")

    model_name = get_prompt_model_name_for_task(EXPLORATION_TASK_SELECTOR_BATCH)
    selector = CandidateSelector(
        llm_generate=lambda prompt: call_reasoning_model(prompt, task_name=EXPLORATION_TASK_SELECTOR_BATCH),
        model_name_batch=model_name,
    )

    stable_cases = 0
    for case in cases:
        candidates = _build_candidate_pool(case, records)
        if not candidates:
            continue
        trial_selected: list[list[ExplorationCandidate]] = []
        trial_indices: list[list[int]] = []
        for _ in range(max(1, int(args.trials))):
            try:
                sel = selector.select_batch(
                    case.instruction,
                    case.intent,
                    candidates,
                    seen_files=set(),
                    limit=min(case.limit, len(candidates)),
                )
            except ValueError as exc:
                # Live eval should observe selector behavior; allow explicit empty outputs.
                if "no matchable selections" in str(exc).lower():
                    sel = []
                else:
                    raise
            sel = sel or []
            trial_selected.append(sel)
            trial_indices.append(_indices_for_selected(candidates, sel))

        selected_1 = trial_selected[0]
        idx_1 = trial_indices[0]
        stable = len({tuple(sorted(x)) for x in trial_indices}) == 1
        if stable:
            stable_cases += 1

        # Redundancy proxy: unique file paths among selected.
        unique_paths = len({c.file_path for c in selected_1})
        redundant = len(selected_1) - unique_paths

        print("=" * 100)
        print(f"BUCKET: {case.bucket}")
        print(f"INSTRUCTION: {case.instruction}")
        print(f"INTENT: {case.intent}")
        print("CANDIDATES:")
        print(json.dumps(_to_rows(candidates), indent=2, ensure_ascii=False))
        print("SELECTED_INDICES:")
        print(json.dumps(idx_1, ensure_ascii=False))
        print("SELECTED_CONTENT:")
        print(json.dumps(_to_rows(selected_1), indent=2, ensure_ascii=False))
        print("STABILITY_MATRIX:")
        print(json.dumps(_pairwise_stability_matrix(trial_indices), ensure_ascii=False))
        print("INDEX_SET_VARIANCE:")
        print(json.dumps(_index_set_variance(trial_indices), ensure_ascii=False))
        print(
            "EVAL_NOTES:"
            + json.dumps(
                {
                    "focused_set": len(selected_1) < len(candidates),
                    "avoids_redundancy_signal": redundant == 0,
                    "stable_across_two_runs": stable,
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
                    "stable_cases_two_runs": stable_cases,
                }
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

