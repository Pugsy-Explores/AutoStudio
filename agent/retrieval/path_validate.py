"""Path validation for retrieval_pipeline_v2.

Safety contract — does exactly three things:
  1. File exists on disk.
  2. Path lies under project_root (or any extra_roots).
  3. Path does not contain blocked internal segments.

Does NOT:
  - Sort or score results.
  - Filter by extension.
  - Apply any heuristic or keyword logic.

Order of input list is preserved.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Internal/non-source path segments that are never valid retrieval targets.
_BLOCKED_SEGMENTS: frozenset[str] = frozenset({
    ".symbol_graph",
    "__pycache__",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
})


def _is_blocked(path: Path) -> bool:
    return any(part in _BLOCKED_SEGMENTS for part in path.parts)


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_paths(
    rows: list[dict],
    project_root: str,
    extra_roots: tuple[str, ...] = (),
) -> list[dict]:
    """Keep rows whose 'file' is a valid, reachable source file.

    Args:
        rows: list of dicts with 'file' key (absolute or relative).
        project_root: primary root; paths must be under this or extra_roots.
        extra_roots: additional allowed roots (e.g. source_root from state).

    Returns:
        Subset of rows that pass all three checks, in original order.
        'file' field is normalised to absolute path in returned rows.
    """
    root = Path(project_root).resolve()
    allowed: list[Path] = [root]
    for r in extra_roots:
        if r and str(r).strip():
            try:
                allowed.append(Path(r).resolve())
            except OSError:
                pass

    validated: list[dict] = []
    for row in rows:
        raw = (row.get("file") or row.get("path") or "").strip()
        if not raw:
            continue

        p = Path(raw)
        if not p.is_absolute():
            p = (root / raw).resolve()
        else:
            p = p.resolve()

        if not any(_is_under(p, r) for r in allowed):
            logger.debug("[path_validate] outside roots: %s", p)
            continue

        if _is_blocked(p):
            logger.debug("[path_validate] blocked segment: %s", p)
            continue

        if not p.is_file():
            logger.debug("[path_validate] not a file: %s", p)
            continue

        out = dict(row)
        out["file"] = str(p)
        validated.append(out)

    return validated
