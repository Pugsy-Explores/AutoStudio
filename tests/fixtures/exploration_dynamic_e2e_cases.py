from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExplorationE2ECase:
    id: str
    instruction: str
    focus_area: str
    target: dict[str, str]
    expected_behavior: dict[str, object]
    step_expectations: dict[int, list[str]]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _collect_symbols() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    root = _repo_root() / "agent_v2"
    functions: list[tuple[str, str]] = []
    classes: list[tuple[str, str]] = []
    for py in sorted(root.rglob("*.py")):
        rel = py.relative_to(_repo_root()).as_posix()
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("__"):
                functions.append((node.name, rel))
            elif isinstance(node, ast.ClassDef) and not node.name.startswith("__"):
                classes.append((node.name, rel))
    return functions, classes


def _pick(rows: list[tuple[str, str]], idx: int, fallback: tuple[str, str]) -> tuple[str, str]:
    if not rows:
        return fallback
    return rows[idx % len(rows)]


def build_e2e_dynamic_cases() -> list[ExplorationE2ECase]:
    """
    Build real E2E exploration-stage cases from current repository symbols.
    These are designed to run through AgentRuntime.explore(...) with no mocks.
    """
    funcs, classes = _collect_symbols()
    f0 = _pick(funcs, 5, ("create_runtime", "agent_v2/runtime/bootstrap.py"))
    f1 = _pick(funcs, 11, ("create_exploration_runner", "agent_v2/runtime/bootstrap.py"))
    f2 = _pick(funcs, 17, ("run", "agent_v2/runtime/exploration_runner.py"))
    c0 = _pick(classes, 2, ("ExplorationRunner", "agent_v2/runtime/exploration_runner.py"))
    c1 = _pick(classes, 6, ("ExplorationEngineV2", "agent_v2/exploration/exploration_engine_v2.py"))

    return [
        ExplorationE2ECase(
            id="e2e_expand_runtime_to_engine",
            instruction=(
                f"Trace how {c0[0]} delegates to {c1[0]} and explain the cross-file execution flow "
                f"starting from {f0[0]}."
            ),
            focus_area="expand",
            target={"symbol": c1[0], "file": c1[1]},
            expected_behavior={
                "expected_actions": ["expand"],
                "required_patterns": ["must_expand_on_caller_gap", "must_cross_file_traverse"],
                "forbidden_patterns": ["repeated_same_query", "premature_refine"],
            },
            step_expectations={1: ["must_expand"]},
        ),
        ExplorationE2ECase(
            id="e2e_refine_disambiguate_runtime_entry",
            instruction=(
                f"Disambiguate where {f1[0]} is used versus where {f2[0]} is called, "
                "then refine to the most relevant runtime path."
            ),
            focus_area="refine",
            target={"symbol": f1[0], "file": f1[1]},
            expected_behavior={
                "expected_actions": ["refine"],
                "required_patterns": ["must_refine_on_ambiguous_target", "must_avoid_repeated_queries"],
                "forbidden_patterns": ["blind_expand_without_disambiguation", "repeated_same_query"],
            },
            step_expectations={1: ["must_refine"]},
        ),
        ExplorationE2ECase(
            id="e2e_multihop_runner_dependencies",
            instruction=(
                f"Follow the multi-hop path from {f0[0]} to runtime construction and into {c0[0]} "
                "dependencies, including parser/analyzer wiring."
            ),
            focus_area="multi_hop",
            target={"symbol": f0[0], "file": f0[1]},
            expected_behavior={
                "expected_actions": ["expand", "expand"],
                "required_patterns": ["must_expand_multi_hop_path", "must_use_memory_between_steps"],
                "forbidden_patterns": ["single_hop_answer", "repeated_same_query"],
            },
            step_expectations={1: ["must_expand"]},
        ),
    ]

