"""Resolve exploration evaluation repos: paths, git clones, and retrieval roots.

Config source: ``EXPLORATION_TEST_REPOS`` in ``agent_v2.config`` (env JSON override).
No hardcoded paths outside config — clone base comes from env or ``artifacts/exploration_test_repos``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def exploration_test_repos_list() -> list[dict[str, Any]]:
    """Return the active repo spec list (env JSON overrides defaults)."""
    from agent_v2 import config as cfg  # noqa: PLC0415

    return cfg.exploration_test_repos_resolved()


def default_clone_base(anchor: Path) -> Path:
    raw = os.environ.get("AGENT_V2_EXPLORATION_TEST_REPOS_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (anchor / "artifacts" / "exploration_test_repos").resolve()


def resolve_git_clone_path(anchor: Path, name: str) -> Path:
    """Directory for a named git clone under the configured clone base."""
    return default_clone_base(anchor) / name


def ensure_git_clone(spec: dict[str, Any], anchor: Path) -> Path | None:
    """Clone ``git`` URL into clone base if missing. Returns path or None."""
    url = str(spec.get("git") or "").strip()
    name = str(spec.get("name") or "").strip()
    if not url or not name:
        return None
    dest = resolve_git_clone_path(anchor, name)
    if dest.is_dir() and (dest / ".git").exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("[exploration_test_repos] cloning %s -> %s", url, dest)
    subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )
    return dest


def resolve_path_root(spec: dict[str, Any], anchor: Path) -> Path | None:
    """Absolute directory for a ``path``-only spec (relative to anchor)."""
    rel = str(spec.get("path") or "").strip().rstrip("/")
    name = str(spec.get("name") or "").strip()
    if not rel or not name:
        return None
    return (anchor / rel).resolve()


def resolve_labeled_roots(anchor: Path) -> list[tuple[str, Path]]:
    """(repo_name, directory) for labeling candidates — includes path + git clones."""
    out: list[tuple[str, Path]] = []
    anchor = anchor.resolve()
    for spec in exploration_test_repos_list():
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        if spec.get("git"):
            p = resolve_git_clone_path(anchor, name)
            if p.is_dir():
                out.append((name, p.resolve()))
        elif spec.get("path"):
            p = resolve_path_root(spec, anchor)
            if p and p.is_dir():
                out.append((name, p))
    # Longest path prefix wins in label_for_path
    out.sort(key=lambda x: len(str(x[1])), reverse=True)
    return out


def label_for_path(abs_file: str, anchor: Path) -> str | None:
    """Best-effort repo label from config (longest matching root)."""
    try:
        p = Path(abs_file).resolve()
    except OSError:
        return None
    for name, root in resolve_labeled_roots(anchor):
        try:
            p.relative_to(root)
            return name
        except ValueError:
            continue
    try:
        p.relative_to(anchor.resolve())
        return "workspace"
    except ValueError:
        return None


def extra_retrieval_roots(anchor: Path) -> tuple[str, ...]:
    """Absolute paths for extra Chroma/graph indices (git clones + path-only repo roots)."""
    roots: list[str] = []
    seen: set[str] = set()
    anchor = anchor.resolve()
    for spec in exploration_test_repos_list():
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        if spec.get("git"):
            p = resolve_git_clone_path(anchor, name)
        elif spec.get("path"):
            p = resolve_path_root(spec, anchor)
        else:
            continue
        if p is None or not p.is_dir():
            continue
        if p.resolve() == anchor:
            continue
        s = str(p.resolve())
        if s not in seen:
            seen.add(s)
            roots.append(s)
    return tuple(roots)


def index_all_configured(anchor: Path, *, verbose: bool = False) -> list[tuple[str, str]]:
    """Index each exploration test repo (path specs use include_dirs; git uses clone root)."""
    from repo_index.indexer import index_repo  # noqa: PLC0415

    results: list[tuple[str, str]] = []
    anchor = anchor.resolve()
    for spec in exploration_test_repos_list():
        name = str(spec.get("name") or "").strip()
        if not name:
            continue
        if spec.get("git"):
            root = ensure_git_clone(spec, anchor)
            if root is None:
                continue
            _sym, db = index_repo(str(root), verbose=verbose, print_summary=verbose)
            results.append((name, db))
        elif spec.get("path"):
            rel = str(spec["path"]).strip().rstrip("/")
            root = anchor / rel
            if not root.is_dir():
                logger.warning("[exploration_test_repos] skip missing path %s", root)
                continue
            # Index only that subtree; output still under root/.symbol_graph
            _sym, db = index_repo(str(root), verbose=verbose, print_summary=verbose)
            results.append((name, db))
    return results
