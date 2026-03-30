"""Manifest-driven retrieval cases for concurrency, DB, CLI/utility, entrypoints, vague queries.

Repo names match ``EXPLORATION_TEST_REPOS`` in ``agent_v2.config`` (``mini_projects``,
``concurrency_repo``). Clones live under ``artifacts/exploration_test_repos/<name>``. Path-only
entries are applied via ``AGENT_V2_EXPLORATION_TEST_REPOS_JSON`` so ``label_for_path`` and
``extra_retrieval_roots`` stay aligned with retrieval.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from tests.retrieval.case_generation import RetrievalEvalCase

logger = logging.getLogger(__name__)

_MANIFEST = Path(__file__).resolve().parent / "pattern_sources.json"


def pattern_manifest_path() -> Path:
    return _MANIFEST


def load_pattern_manifest() -> dict[str, Any]:
    raw = _MANIFEST.read_text(encoding="utf-8")
    return json.loads(raw)


def ensure_pattern_repos(anchor: Path) -> dict[str, Path]:
    """Clone repos listed in the manifest; return ``name`` -> clone root."""
    from agent_v2.exploration_test_repos import ensure_git_clone  # noqa: PLC0415

    data = load_pattern_manifest()
    repos = data.get("repos") or []
    out: dict[str, Path] = {}
    for spec in repos:
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        p = ensure_git_clone(dict(spec), anchor.resolve())
        if p is None or not p.is_dir():
            raise RuntimeError(f"Pattern repo clone failed or missing: {name!r}")
        out[name] = p.resolve()
    return out


def pattern_exploration_test_repos_json(anchor: Path) -> str:
    """Path-only specs (relative to anchor) for ``AGENT_V2_EXPLORATION_TEST_REPOS_JSON``."""
    anchor = anchor.resolve()
    roots = ensure_pattern_repos(anchor)
    specs: list[dict[str, str]] = []
    for name, root in sorted(roots.items()):
        rel = root.relative_to(anchor)
        specs.append({"name": name, "path": rel.as_posix()})
    return json.dumps(specs)


def apply_pattern_coverage_env(anchor: Path) -> None:
    """Register pattern clones for labeling + multi-root retrieval (call before engine import)."""
    anchor = anchor.resolve()
    os.environ["AGENT_V2_EXPLORATION_TEST_REPOS_JSON"] = pattern_exploration_test_repos_json(anchor)
    os.environ["AGENT_V2_APPEND_EXPLORATION_TEST_REPOS_TO_RETRIEVAL"] = "1"
    roots = ensure_pattern_repos(anchor)
    # Always set: a pre-existing RETRIEVAL_EXTRA_PROJECT_ROOTS in the shell would otherwise skip
    # clones and starve multi-root retrieval for this harness.
    os.environ["RETRIEVAL_EXTRA_PROJECT_ROOTS"] = ",".join(
        str(p.resolve()) for _, p in sorted(roots.items())
    )


def _default_instruction(pattern: str, sym: str, kind: str) -> str:
    if pattern == "concurrency":
        if kind == "class":
            return f"Locate the class that runs the concurrent pipeline and manages workers and queues ({sym})."
        return f"Locate concurrent pipeline execution code for {sym}."
    if pattern == "class_lookup":
        return f"Find the {sym} class definition and its public interface."
    if pattern == "function_trace":
        return f"Locate the {sym} function implementation."
    if pattern == "database":
        return f"Locate database access and SQL-related code for {sym}."
    if pattern == "utility":
        return f"Locate the CLI or interactive command implementation for {sym}."
    if pattern == "entrypoint":
        return f"Locate the application entry point that starts the main pipeline ({sym})."
    if pattern == "config_constants":
        return f"Locate where {sym} is defined (module-level constant or configuration path)."
    if pattern == "cross_reference":
        return f"Find the definition of {sym} (symbol used across modules)."
    if pattern == "vague_query":
        return f"Where is logic related to {sym} in this area of the codebase?"
    return f"Locate the {sym} implementation."


def build_pattern_coverage_cases(anchor: Path) -> list[RetrievalEvalCase]:
    """Build eval cases from ``pattern_sources.json``; raises if manifest paths drift."""
    data = load_pattern_manifest()
    roots = ensure_pattern_repos(anchor.resolve())
    cases: list[RetrievalEvalCase] = []
    for c in data.get("cases") or []:
        repo = str(c.get("repo") or "").strip()
        rel_file = str(c.get("relative_file") or "").strip().replace("\\", "/")
        sym = str(c.get("expected_symbol") or "").strip()
        pattern = str(c.get("pattern") or "function_trace").strip()
        kind = str(c.get("kind") or "function").strip()
        cid = str(c.get("id") or "case").strip()
        if not repo or not rel_file or not sym:
            continue
        root = roots.get(repo)
        if root is None:
            raise KeyError(f"Unknown repo {repo!r} in pattern case {cid!r}")
        full = (root / rel_file).resolve()
        if not full.is_file():
            raise FileNotFoundError(
                f"Pattern manifest drift: expected file missing for {cid}: {full}"
            )
        instruction = str(c.get("instruction") or "").strip() or _default_instruction(pattern, sym, kind)
        cases.append(
            RetrievalEvalCase(
                case_id=f"pattern_{cid}",
                instruction=instruction,
                expected_symbol=sym,
                expected_file_hint=rel_file,
                keywords=[sym, pattern, repo],
                repo=repo,
                category=pattern,
                rank_fail_after=10,
            )
        )
    if not cases:
        raise RuntimeError("pattern_sources.json produced no cases")
    return cases


def index_pattern_repos(anchor: Path, *, verbose: bool = False) -> list[tuple[str, str]]:
    """Index each pattern clone (``.symbol_graph`` under each root)."""
    from repo_index.indexer import index_repo  # noqa: PLC0415

    results: list[tuple[str, str]] = []
    for name, root in sorted(ensure_pattern_repos(anchor.resolve()).items()):
        _sym, db = index_repo(str(root), verbose=verbose, print_summary=verbose)
        results.append((name, db))
    return results


def pattern_repos_indexed(anchor: Path) -> bool:
    """True if each clone has a ``.symbol_graph`` directory."""
    anchor = anchor.resolve()
    try:
        roots = ensure_pattern_repos(anchor)
    except Exception:
        return False
    for _n, root in roots.items():
        if not (root / ".symbol_graph").is_dir():
            return False
    return bool(roots)
