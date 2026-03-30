"""Serena adapter: search_code (MCP) → list[dict] in legacy format.

MCP is the primary path. When MCP is unavailable, the old serena_adapter has a
_grep_fallback restricted to *.py — we do NOT propagate that implicit fallback.

Explicit fallback: if SERENA_FALLBACK_RG_GLOB env is set (e.g. "*.py,*.ts"),
we call rg directly with the specified glob. Otherwise we return [] + warning.

This makes the fallback decision observable and configurable, not hidden.
"""

from __future__ import annotations

import logging
import os
import subprocess

logger = logging.getLogger(__name__)

# Set to "1" to disable the implicit *.py grep fallback inside serena_adapter.
# We enforce this in the adapter by temporarily overriding the env var.
_FORCE_DISABLE_GREP_FALLBACK_ENV = "SERENA_GREP_FALLBACK"


def fetch_serena(
    query: str,
    project_root: str,
    top_k: int = 15,
) -> tuple[list[dict], list[str]]:
    """Return (rows, warnings). rows shape: {file, symbol, line, snippet, source, metadata}."""
    # Ensure project dir is set for serena_adapter
    os.environ.setdefault("SERENA_PROJECT_DIR", project_root)

    # Suppress the implicit *.py-only grep fallback inside serena_adapter.
    # If users want a fallback they should set SERENA_FALLBACK_RG_GLOB explicitly.
    _orig_fallback = os.environ.get(_FORCE_DISABLE_GREP_FALLBACK_ENV)
    os.environ[_FORCE_DISABLE_GREP_FALLBACK_ENV] = "0"

    try:
        from agent.tools.serena_adapter import search_code  # noqa: PLC0415

        out = search_code(query)
        raw = (out or {}).get("results") or []
        err = (out or {}).get("error", "")

        if err and not raw:
            # Explicit fallback path if configured
            fallback_glob = os.environ.get("SERENA_FALLBACK_RG_GLOB", "")
            if fallback_glob:
                return _rg_fallback(query, project_root, fallback_glob, top_k)
            return [], [f"serena_unavailable:{err}"]

        results: list[dict] = []
        for i, r in enumerate(raw[:top_k]):
            results.append({
                "file": r.get("file") or "",
                "symbol": r.get("symbol") or "",
                "line": r.get("line") or 0,
                "snippet": (r.get("snippet") or "")[:500],
                "source": "serena",
                "metadata": {
                    "rank_in_source": i,
                    "raw_score": None,
                    "source_specific": {},
                },
            })

        logger.debug("[adapter.serena] query=%r → %d rows", query, len(results))
        return results, []

    except Exception as exc:
        logger.warning("[adapter.serena] error: %s", exc)
        return [], [f"serena_error:{type(exc).__name__}:{exc}"]

    finally:
        # Restore original env state
        if _orig_fallback is None:
            os.environ.pop(_FORCE_DISABLE_GREP_FALLBACK_ENV, None)
        else:
            os.environ[_FORCE_DISABLE_GREP_FALLBACK_ENV] = _orig_fallback


def _rg_fallback(
    query: str,
    project_root: str,
    glob: str,
    top_k: int,
) -> tuple[list[dict], list[str]]:
    """Explicit rg fallback with a configurable glob pattern (SERENA_FALLBACK_RG_GLOB).

    Runs: rg --glob <glob_pattern> -l <query> <project_root>
    Returns file-level hits only (no snippet extraction).
    """
    globs = [g.strip() for g in glob.split(",") if g.strip()]
    if not globs:
        return [], ["serena_fallback_rg:empty_glob"]

    try:
        cmd = ["rg"]
        for g in globs:
            cmd += ["--glob", g]
        cmd += ["-l", "--max-count", "1", query, project_root]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        files = [f.strip() for f in proc.stdout.splitlines() if f.strip()][:top_k]

        results: list[dict] = [
            {
                "file": f,
                "symbol": "",
                "line": 0,
                "snippet": f"(rg fallback — file matches '{query}')",
                "source": "serena",
                "metadata": {
                    "rank_in_source": i,
                    "raw_score": None,
                    "source_specific": {"fallback": "rg", "glob": glob},
                },
            }
            for i, f in enumerate(files)
        ]
        logger.debug("[adapter.serena.rg_fallback] query=%r → %d files", query, len(results))
        return results, ["serena_using_rg_fallback"]

    except Exception as exc:
        return [], [f"serena_fallback_rg_error:{exc}"]
