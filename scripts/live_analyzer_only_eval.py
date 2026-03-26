#!/usr/bin/env python3
"""
Live evaluation for Analyzer module only.

Scope:
- Uses UnderstandingAnalyzer only (no reader/inspector/fetcher/engine).
- Inputs are instruction + intent + context_blocks.
- context_blocks are built from real repository code slices.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.models.model_client import call_reasoning_model_messages
from agent.models.model_config import get_prompt_model_name_for_task
from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_ANALYZER
from agent_v2.exploration.understanding_analyzer import UnderstandingAnalyzer
from agent_v2.schemas.exploration import ContextBlock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = [PROJECT_ROOT / "agent", PROJECT_ROOT / "agent_v2"]


@dataclass(frozen=True)
class SymbolSlice:
    file_path: str
    symbol: str
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class AnalyzerCase:
    bucket: str
    instruction: str
    intent: str
    context_blocks: list[ContextBlock]
    expected_understanding: str | None = None
    expected_relevance: str | None = None
    stability_runs: int = 1


def _iter_py_files() -> list[Path]:
    out: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*.py"):
            rel = p.relative_to(PROJECT_ROOT).as_posix()
            if "/tests/" in rel:
                continue
            out.append(p)
    return sorted(out)


def _collect_symbols() -> list[SymbolSlice]:
    symbols: list[SymbolSlice] = []
    for p in _iter_py_files():
        rel = p.relative_to(PROJECT_ROOT).as_posix()
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(src)
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                symbols.append(
                    SymbolSlice(
                        file_path=rel,
                        symbol=node.name,
                        kind="function",
                        start=max(1, int(node.lineno)),
                        end=max(int(node.lineno), int(getattr(node, "end_lineno", node.lineno))),
                    )
                )
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                symbols.append(
                    SymbolSlice(
                        file_path=rel,
                        symbol=node.name,
                        kind="class",
                        start=max(1, int(node.lineno)),
                        end=max(int(node.lineno), int(getattr(node, "end_lineno", node.lineno))),
                    )
                )
    return sorted(symbols, key=lambda x: ((x.end - x.start), x.file_path, x.start, x.symbol), reverse=True)


def _read_lines(rel_path: str, start: int, end: int) -> str:
    fp = PROJECT_ROOT / rel_path
    if not fp.exists():
        return ""
    lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines()
    s = max(1, start)
    e = max(s, end)
    s_idx = s - 1
    e_idx = min(len(lines), e)
    return "\n".join(lines[s_idx:e_idx])


def _make_block(s: SymbolSlice, *, max_lines: int = 60, reason: str = "repo_slice") -> ContextBlock:
    end = min(s.end, s.start + max_lines - 1)
    return ContextBlock(
        file_path=str((PROJECT_ROOT / s.file_path).resolve()),
        start=s.start,
        end=end,
        content=_read_lines(s.file_path, s.start, end),
        origin_reason=reason,
        symbol=s.symbol,
        relationship_refs=[],
    )


def _has_substantive_content(block: ContextBlock) -> bool:
    text = (block.content or "").strip()
    if not text:
        return False
    # Require at least one non-comment, non-empty line.
    for ln in text.splitlines():
        t = ln.strip()
        if not t:
            continue
        if t.startswith("#"):
            continue
        return True
    return False


def _build_cases(symbols: list[SymbolSlice]) -> list[AnalyzerCase]:
    if len(symbols) < 6:
        raise RuntimeError("Not enough repo symbols discovered to build dynamic analyzer tests.")
    a = symbols[0]
    b = symbols[1]
    c = symbols[2]
    d = symbols[3]
    e = symbols[4]
    f = symbols[5]

    # easy: clear, sufficient
    easy_block = _make_block(a, max_lines=80, reason="primary_relevant")
    easy = AnalyzerCase(
        bucket="easy",
        instruction=f"Find where {a.symbol} is defined and explain what it does",
        intent="find_definition",
        context_blocks=[easy_block],
        expected_understanding="sufficient",
        expected_relevance="high",
        stability_runs=2,
    )

    # medium: partial context, one relevant short block + one related/noisy block
    medium_primary = _make_block(a, max_lines=10, reason="partial_relevant")
    medium_noise = _make_block(d, max_lines=20, reason="secondary_noise")
    medium = AnalyzerCase(
        bucket="medium",
        instruction=f"Understand how {a.symbol} works end-to-end",
        intent="understand_flow",
        context_blocks=[medium_primary, medium_noise],
        expected_understanding="partial",
        expected_relevance=None,
        stability_runs=2,
    )

    # hard: mixed relevant + irrelevant
    hard_blocks = [
        _make_block(b, max_lines=35, reason="possibly_relevant"),
        _make_block(c, max_lines=35, reason="possibly_relevant"),
        _make_block(e, max_lines=35, reason="noise"),
        _make_block(f, max_lines=35, reason="noise"),
    ]
    hard = AnalyzerCase(
        bucket="hard",
        instruction=f"Understand how {b.symbol} interacts with {c.symbol}",
        intent="trace_usage",
        context_blocks=hard_blocks,
        expected_understanding=None,
        expected_relevance=None,
        stability_runs=2,
    )

    # adversarial: mostly unrelated blocks
    adversarial_blocks = [
        _make_block(d, max_lines=25, reason="unrelated_1"),
        _make_block(e, max_lines=25, reason="unrelated_2"),
        _make_block(f, max_lines=25, reason="unrelated_3"),
    ]
    adversarial = AnalyzerCase(
        bucket="adversarial",
        instruction=f"Explain implementation details of {a.symbol}",
        intent="deep_explain",
        context_blocks=adversarial_blocks,
        expected_understanding="insufficient",
        expected_relevance=None,
        stability_runs=3,
    )

    # bonus: clean vs noisy comparison for same instruction
    compare_clean = AnalyzerCase(
        bucket="bonus_clean",
        instruction=f"Trace usage of {b.symbol}",
        intent="trace_usage",
        context_blocks=[_make_block(b, max_lines=60, reason="clean")],
        expected_understanding=None,
        expected_relevance=None,
        stability_runs=2,
    )
    compare_noisy = AnalyzerCase(
        bucket="bonus_noisy",
        instruction=f"Trace usage of {b.symbol}",
        intent="trace_usage",
        context_blocks=[_make_block(b, max_lines=20, reason="clean_partial"), _make_block(f, max_lines=40, reason="noise")],
        expected_understanding=None,
        expected_relevance=None,
        stability_runs=2,
    )

    cases = [easy, medium, hard, adversarial, compare_clean, compare_noisy]
    filtered: list[AnalyzerCase] = []
    for case in cases:
        valid_blocks = [b for b in case.context_blocks if _has_substantive_content(b)]
        if not valid_blocks:
            # Keep at least one block to preserve test coverage shape, even if weak.
            valid_blocks = case.context_blocks[:1]
        filtered.append(
            AnalyzerCase(
                bucket=case.bucket,
                instruction=case.instruction,
                intent=case.intent,
                context_blocks=valid_blocks,
                expected_understanding=case.expected_understanding,
                expected_relevance=case.expected_relevance,
                stability_runs=case.stability_runs,
            )
        )
    return filtered


def _context_summary(blocks: list[ContextBlock]) -> list[dict[str, Any]]:
    return [{"file": b.file_path, "range": f"{b.start}-{b.end}", "reason": b.origin_reason} for b in blocks]


def _output_contract_checks(out: dict[str, Any]) -> dict[str, Any]:
    allowed = {"relevance", "confidence", "sufficient", "evidence_sufficiency", "knowledge_gaps", "summary"}
    keys = set(out.keys())
    return {
        "valid_fields_only": keys <= allowed,
        "has_no_control_logic": ("next_action" not in out and "status" not in out and "needs" not in out),
    }


def _run_case(analyzer: UnderstandingAnalyzer, case: AnalyzerCase) -> dict[str, Any]:
    # Hard guard: never call analyzer with missing required inputs.
    if not (case.instruction or "").strip():
        raise ValueError(f"{case.bucket}: instruction is empty")
    if not (case.intent or "").strip():
        raise ValueError(f"{case.bucket}: intent is empty")
    if not case.context_blocks:
        raise ValueError(f"{case.bucket}: context_blocks is empty")
    outputs: list[dict[str, Any]] = []
    errors: list[str] = []
    for _ in range(max(1, case.stability_runs)):
        try:
            out = analyzer.analyze(
                case.instruction,
                intent=case.intent,
                context_blocks=case.context_blocks,
            ).model_dump()
            outputs.append(out)
        except Exception as exc:
            errors.append(str(exc))

    first = outputs[0] if outputs else {}
    stability_same = (all(o == first for o in outputs[1:]) if len(outputs) > 1 else True) and not errors
    checks = _output_contract_checks(first) if first else {
        "valid_fields_only": False,
        "has_no_control_logic": False,
    }
    expected_eval = {
        "expected_understanding": case.expected_understanding,
        "expected_relevance": case.expected_relevance,
        "understanding_match": (
            None
            if case.expected_understanding is None
            else (first.get("evidence_sufficiency") == case.expected_understanding if first else False)
        ),
        "relevance_match": (
            None
            if case.expected_relevance is None
            else (first.get("relevance") == case.expected_relevance if first else False)
        ),
    }
    return {
        "bucket": case.bucket,
        "instruction": case.instruction,
        "intent": case.intent,
        "context_blocks_count": len(case.context_blocks),
        "context_summary": _context_summary(case.context_blocks),
        "analyzer_output": first if first else None,
        "analyzer_errors": errors,
        "stability_runs": case.stability_runs,
        "stability_same_output": stability_same,
        "output_contract_checks": checks,
        "bucket_expectation_eval": expected_eval,
    }


def main() -> int:
    symbols = _collect_symbols()
    cases = _build_cases(symbols)

    model_name = get_prompt_model_name_for_task(EXPLORATION_TASK_ANALYZER)
    analyzer = UnderstandingAnalyzer(
        llm_generate_messages=lambda messages: call_reasoning_model_messages(
            messages, task_name=EXPLORATION_TASK_ANALYZER
        ),
        model_name=model_name,
    )

    print("# Live Analyzer-Only Evaluation")
    print(f"# project_root={PROJECT_ROOT}")
    print(f"# discovered_symbols={len(symbols)}")

    results = []
    for case in cases:
        result = _run_case(analyzer, case)
        results.append(result)
        print("=" * 120)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    print("=" * 120)
    summary = {
        "cases": len(results),
        "all_stable": all(r["stability_same_output"] for r in results),
        "all_contract_clean": all(
            r["output_contract_checks"]["valid_fields_only"] and r["output_contract_checks"]["has_no_control_logic"]
            for r in results
        ),
        "expectation_matches": {
            "understanding": [
                r["bucket"]
                for r in results
                if r["bucket_expectation_eval"]["understanding_match"] is True
            ],
            "relevance": [
                r["bucket"]
                for r in results
                if r["bucket_expectation_eval"]["relevance_match"] is True
            ],
        },
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
