"""Dynamic retrieval eval cases from real repo trees (no synthetic symbols).

Used by ``test_retrieval_pipeline_behavior.py`` and ``scripts/eval_retrieval_pipeline.py``.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


def _parse_top_level(path: Path) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    try:
        src = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(src, filename=str(path))
    except (OSError, SyntaxError):
        return [], []
    classes: list[tuple[str, int]] = []
    funcs: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            classes.append((node.name, node.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            funcs.append((node.name, node.lineno))
    return classes, funcs


def _infer_category(_path: Path, _sym_name: str, sym_kind: str) -> str:
    """Coverage bucket: AST shape only (no path/symbol keyword heuristics)."""
    return "class_lookup" if sym_kind == "class" else "function_trace"


def _instruction_for(category: str, sym_name: str, sym_kind: str, tag: str) -> str:
    if category == "vague_query":
        return f"Where is logic related to {sym_name} in the {tag} area of the codebase?"
    if sym_kind == "class":
        return f"Find the {sym_name} class definition and its public interface"
    return f"Locate the {sym_name} function implementation"


@dataclass
class RetrievalEvalCase:
    case_id: str
    instruction: str
    expected_symbol: str
    expected_file_hint: str
    keywords: list[str] = field(default_factory=list)
    alt_file_hints: list[str] = field(default_factory=list)
    repo: str = "local_repo"
    category: str = "function_trace"
    # Fail ranking assertion if expected file rank exceeds this (5 = local default; 10 for noisy multi-repo).
    rank_fail_after: int = 5


def build_default_local_cases(anchor: Path, *, max_cases: int = 14) -> list[RetrievalEvalCase]:
    """Original harness: scan agent_v2/ modules under AutoStudio anchor."""
    agent = anchor / "agent_v2"
    if not agent.is_dir():
        raise RuntimeError(f"agent_v2/ not found under {anchor}")

    _TARGET_MODULES: list[tuple[str, str]] = [
        ("exploration/exploration_engine_v2.py", "engine"),
        ("exploration/candidate_selector.py", "selector"),
        ("exploration/exploration_scoper.py", "scoper"),
        ("exploration/query_intent_parser.py", "intent_parser"),
        ("exploration/exploration_working_memory.py", "working_memory"),
        ("exploration/graph_expander.py", "graph_expander"),
        ("runtime/dispatcher.py", "dispatcher"),
        ("runtime/exploration_runner.py", "runner"),
        ("schemas/exploration.py", "schema"),
        ("config.py", "config"),
    ]

    cases: list[RetrievalEvalCase] = []
    seen_symbols: set[str] = set()

    for rel_path, tag in _TARGET_MODULES:
        full = agent / rel_path
        if not full.is_file():
            continue
        classes, funcs = _parse_top_level(full)
        candidates_sym = [(n, ln, "class") for n, ln in classes] + [(n, ln, "fn") for n, ln in funcs]
        for sym_name, _, sym_kind in candidates_sym:
            if sym_name in seen_symbols:
                continue
            seen_symbols.add(sym_name)
            hint = str(full.relative_to(anchor))
            kws = [sym_name, tag]
            cat = _infer_category(full, sym_name, "class" if sym_kind == "class" else "fn")
            instruction = _instruction_for(cat, sym_name, "class" if sym_kind == "class" else "fn", tag)
            cases.append(
                RetrievalEvalCase(
                    case_id=f"{tag}_{sym_name}",
                    instruction=instruction,
                    expected_symbol=sym_name,
                    expected_file_hint=hint,
                    keywords=kws,
                    repo="local_repo",
                    category=cat,
                    rank_fail_after=5,
                )
            )
            if len(cases) >= max_cases:
                break
        if len(cases) >= max_cases:
            break

    if not cases:
        raise RuntimeError("Could not generate any test cases — is agent_v2/ populated?")

    return cases


def build_cases_for_repo_root(
    repo_name: str,
    repo_root: Path,
    anchor: Path,
    *,
    max_cases: int = 8,
    max_files: int = 120,
) -> list[RetrievalEvalCase]:
    """Scan a Python tree (e.g. mini project clone) and emit cases with categories."""
    if not repo_root.is_dir():
        return []

    cases: list[RetrievalEvalCase] = []
    seen: set[str] = set()
    vague_slots = max(1, max_cases // 8)

    py_files = sorted(
        p for p in repo_root.rglob("*.py")
        if "__pycache__" not in p.parts and "/test" not in str(p).lower() and not p.name.startswith("test_")
    )[:max_files]

    for full in py_files:
        if len(cases) >= max_cases:
            break
        try:
            rel = str(full.relative_to(repo_root))
        except ValueError:
            rel = full.name
        classes, funcs = _parse_top_level(full)
        for sym_name, _, sym_kind in [(n, ln, "class") for n, ln in classes] + [
            (n, ln, "fn") for n, ln in funcs
        ]:
            if len(cases) >= max_cases:
                break
            if sym_name in seen:
                continue
            seen.add(sym_name)
            tag = rel.replace("/", "_")[:40]
            cat = _infer_category(full, sym_name, sym_kind)
            instruction = _instruction_for(cat, sym_name, sym_kind, tag)
            if vague_slots > 0 and len(cases) % 7 == 0:
                cat = "vague_query"
                instruction = _instruction_for("vague_query", sym_name, sym_kind, tag)
                vague_slots -= 1
            cases.append(
                RetrievalEvalCase(
                    case_id=f"{repo_name}_{tag}_{sym_name}",
                    instruction=instruction,
                    expected_symbol=sym_name,
                    expected_file_hint=rel,
                    keywords=[sym_name, tag],
                    repo=repo_name,
                    category=cat,
                    rank_fail_after=10,
                )
            )
    return cases


def build_multi_repo_eval_cases(
    anchor: Path,
    *,
    max_per_repo: int = 8,
    max_total: int = 40,
) -> list[RetrievalEvalCase]:
    """Cases for each configured exploration test repo (path + optional git clones)."""
    from agent_v2.exploration_test_repos import (  # noqa: PLC0415
        exploration_test_repos_list,
        resolve_git_clone_path,
        resolve_path_root,
    )

    all_cases: list[RetrievalEvalCase] = []
    for spec in exploration_test_repos_list():
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        if spec.get("path"):
            root = resolve_path_root(spec, anchor)
            if root and root.is_dir():
                if name == "local_repo":
                    all_cases.extend(build_default_local_cases(anchor, max_cases=min(max_per_repo, 14)))
                else:
                    all_cases.extend(
                        build_cases_for_repo_root(name, root, anchor, max_cases=max_per_repo)
                    )
        elif spec.get("git"):
            p = resolve_git_clone_path(anchor, name)
            if p.is_dir():
                all_cases.extend(build_cases_for_repo_root(name, p, anchor, max_cases=max_per_repo))
        if len(all_cases) >= max_total:
            break
    return all_cases[:max_total]
