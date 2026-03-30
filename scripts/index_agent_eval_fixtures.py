#!/usr/bin/env python3
"""
Build SQLite + repo_map for each agent_eval fixture repo in-tree (Stage 13.0).

Uses the same entrypoint as production/tests: ``repo_index.indexer.index_repo``.
Writes a JSON coverage report under ``artifacts/agent_eval/index_coverage/``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "agent_eval" / "fixtures"
OUT_REPORT = REPO_ROOT / "artifacts" / "agent_eval" / "index_coverage" / "coverage_report.json"

SKIP_POLICY = (
    "Python files under any path segment in the indexer exclude set are skipped: "
    "__pycache__, .pytest_cache, .mypy_cache, node_modules, .git, dist, build, .eggs, "
    ".venv, venv, artifacts, .tox, .nox, htmlcov, site-packages, .symbol_graph. "
    "Additionally, root .gitignore is applied when ignore_gitignore=True (default)."
)


def _discover_repo_roots() -> list[Path]:
    roots: list[Path] = []
    for subdir in ("mini_repos", "adversarial_mini_repos", "holdout_mini_repos", "pinned_repos"):
        d = FIXTURES / subdir
        if d.is_dir():
            roots.extend(p for p in sorted(d.iterdir()) if p.is_dir())
    return roots


def _py_scan_stats(root: Path) -> tuple[int, int]:
    from repo_index.indexer import _relative_path_has_excluded_component

    root = root.resolve()
    all_py = list(root.rglob("*.py"))
    kept = [p for p in all_py if not _relative_path_has_excluded_component(p, root)]
    return len(all_py), len(kept)


def main() -> int:
    os.environ.setdefault("INDEX_EMBEDDINGS", "0")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    from repo_index.indexer import index_repo

    roots = _discover_repo_roots()
    per_repo: list[dict] = []
    for root in roots:
        total_py, kept_py = _py_scan_stats(root)
        symbols, db_path = index_repo(str(root.resolve()), verbose=False)
        sg = root / ".symbol_graph"
        files_indexed: set[str] = set()
        for s in symbols:
            fp = s.get("file")
            if fp:
                files_indexed.add(fp)
        rel_samples: list[str] = []
        for f in sorted(files_indexed)[:8]:
            try:
                rel_samples.append(str(Path(f).resolve().relative_to(root.resolve())))
            except ValueError:
                rel_samples.append(f)
        per_repo.append(
            {
                "repo_root": str(root.relative_to(REPO_ROOT)),
                "py_files_rglob_total": total_py,
                "py_files_after_path_excludes": kept_py,
                "excluded_by_path_filter_approx": max(0, total_py - kept_py),
                "symbol_count": len(symbols),
                "unique_py_files_in_symbol_records": len(files_indexed),
                "index_sqlite": db_path,
                "index_sqlite_exists": Path(db_path).exists(),
                "repo_map_exists": (sg / "repo_map.json").exists(),
                "symbols_json_exists": (sg / "symbols.json").exists(),
                "sample_relative_paths": rel_samples,
            }
        )

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "fixture_base": str(FIXTURES.relative_to(REPO_ROOT)),
        "repo_count": len(per_repo),
        "skip_and_exclude_policy": SKIP_POLICY,
        "repositories": per_repo,
    }
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    OUT_REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"ok": True, "report": str(OUT_REPORT), "repos_indexed": len(per_repo)},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
