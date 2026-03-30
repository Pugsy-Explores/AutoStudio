"""Git snapshot + diff extraction for benchmark workspaces."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any


def try_git_init_commit(workspace: Path) -> dict[str, Any]:
    """Initialize a git repo and commit all files; no-op if git missing or fails."""
    meta: dict[str, Any] = {"ok": False, "reason": None}
    try:
        subprocess.run(
            ["git", "init"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        subprocess.run(
            ["git", "config", "user.email", "bench@local"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        subprocess.run(
            ["git", "config", "user.name", "bench"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        p = subprocess.run(
            ["git", "commit", "-m", "agent_eval_baseline", "--allow-empty"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if p.returncode != 0:
            meta["reason"] = p.stderr[:500]
            return meta
        meta["ok"] = True
    except FileNotFoundError:
        meta["reason"] = "git_not_found"
    except Exception as e:
        meta["reason"] = str(e)
    return meta


def git_diff_after(workspace: Path) -> tuple[str, list[str], dict[str, int]]:
    """
    Return (unified_diff, changed_files, diff_stat {insertions, deletions}).
    """
    diff = ""
    files: list[str] = []
    stat = {"insertions": 0, "deletions": 0}
    try:
        p = subprocess.run(
            ["git", "diff", "--no-color", "HEAD"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        diff = p.stdout or ""
        p2 = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        files = [ln.strip() for ln in (p2.stdout or "").splitlines() if ln.strip()]
        p3 = subprocess.run(
            ["git", "diff", "--numstat", "HEAD"],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        for line in (p3.stdout or "").splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                ins, dele = parts[0], parts[1]
                if ins != "-" and re.match(r"^\d+$", ins):
                    stat["insertions"] += int(ins)
                if dele != "-" and re.match(r"^\d+$", dele):
                    stat["deletions"] += int(dele)
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return diff, files, stat


def heuristic_unrelated_files(
    changed: list[str],
    repo_path_hint: str,
) -> list[str]:
    """Flag edits under index metadata / cache (heuristic unrelated to task intent)."""
    if not changed:
        return []
    unrelated: list[str] = []
    for f in changed:
        if ".symbol_graph" in f or "__pycache__" in f or f.startswith(".git"):
            unrelated.append(f)
    return unrelated


def scan_bad_edit_patterns(diff_text: str) -> list[str]:
    """Lightweight diff signals for audit."""
    bad: list[str] = []
    if not diff_text:
        return bad
    if "<<<<<<<" in diff_text or ">>>>>>>" in diff_text:
        bad.append("conflict_markers")
    if diff_text.count("pass") > 8 and "def test" not in diff_text:
        bad.append("suspicious_pass_stubs")
    return bad


def retrieval_miss_signals_from_loop(loop_snapshot: dict[str, Any]) -> list[str]:
    sig: list[str] = []
    prs = loop_snapshot.get("phase_results") or []
    if not isinstance(prs, list):
        return sig
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        co = pr.get("context_output") or {}
        if not isinstance(co, dict):
            continue
        rc = co.get("ranked_context")
        if isinstance(rc, list) and len(rc) == 0:
            sig.append("empty_ranked_context")
        rs = co.get("retrieved_symbols")
        if isinstance(rs, list) and len(rs) == 0:
            sig.append("empty_retrieved_symbols")
    return sig
