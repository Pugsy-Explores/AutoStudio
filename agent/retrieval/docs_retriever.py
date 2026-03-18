"""
Docs retrieval lane (Phase 5A).
Deterministic, filesystem-driven discovery + scoring + context building for documentation artifacts.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


_DEFAULT_INCLUDE_EXTS = {".md"}
_OPTIONAL_INCLUDE_EXTS = {".rst", ".txt"}

# NOTE: README files may have no extension or uncommon extensions (README, README.md, README.txt, etc.).
_README_PREFIX = "readme"

_EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "vendor",
}

_EXCLUDED_PATH_PARTS = {
    "tests",
    "test",
}


@dataclass(frozen=True)
class DocsScanStats:
    scanned_files: int
    included_files: int
    excluded_files: int


def _is_test_artifact(path: Path, project_root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(project_root.resolve())
    except Exception:
        return True

    parts_lower = [p.lower() for p in rel.parts]

    if any(p in _EXCLUDED_PATH_PARTS for p in parts_lower):
        return True

    name = rel.name.lower()
    if name.startswith("test_"):
        return True
    ext = rel.suffix.lower()
    if ext and name.endswith(f"_test{ext}"):
        return True
    if name == "conftest.py":
        return True

    return False


def _is_excluded_path(path: Path, project_root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(project_root.resolve())
    except Exception:
        return True

    parts_lower = [p.lower() for p in rel.parts]

    if any(p in _EXCLUDED_DIR_NAMES for p in parts_lower):
        return True

    if _is_test_artifact(path, project_root):
        return True

    return False


def _is_docs_candidate_path(path: Path, project_root: Path) -> bool:
    if not path.is_file():
        return False

    if _is_excluded_path(path, project_root):
        return False

    name_lower = path.name.lower()
    if name_lower.startswith(_README_PREFIX):
        return True

    ext = path.suffix.lower()
    if ext in _DEFAULT_INCLUDE_EXTS or ext in _OPTIONAL_INCLUDE_EXTS:
        return True

    # docs/** and Docs/** are allowed even if extension is unusual, but keep it conservative:
    # only include plain-text-ish files by attempting a small read and rejecting obvious binary.
    parts = [p.lower() for p in path.parts]
    if "docs" in parts:
        return _looks_like_text_file(path)

    return False


def _looks_like_text_file(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            chunk = f.read(2048)
        if b"\x00" in chunk:
            return False
        # If it decodes as UTF-8 reasonably, treat as text.
        chunk.decode("utf-8", errors="strict")
        return True
    except Exception:
        return False


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


def _path_tokens(path: Path, project_root: Path) -> set[str]:
    try:
        rel = path.resolve().relative_to(project_root.resolve())
        parts = "/".join(rel.parts)
    except Exception:
        parts = str(path)
    return set(_tokenize(parts))


def _read_text_prefix(path: Path, max_chars: int) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(max_chars)
    except Exception:
        return ""


def _first_heading(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("#"):
            # strip leading #'s and whitespace
            title = s.lstrip("#").strip()
            if title:
                return title
    return ""


def _is_repo_root_readme(path: Path, project_root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(project_root.resolve())
    except Exception:
        return False
    return len(rel.parts) == 1 and rel.name.lower().startswith(_README_PREFIX)


def _is_under_docs_dir(path: Path) -> bool:
    parts = [p.lower() for p in path.parts]
    return "docs" in parts


def _is_under_examples_dir(path: Path) -> bool:
    parts = [p.lower() for p in path.parts]
    return "examples" in parts


def _score_doc_path(
    query: str,
    path: Path,
    project_root: Path,
    *,
    content_prefix: str,
    heading: str,
) -> float:
    q_tokens = set(_tokenize(query))
    if not q_tokens:
        q_tokens = set()

    p_tokens = _path_tokens(path, project_root)
    content_tokens = set(_tokenize((heading or "") + "\n" + (content_prefix or "")))

    path_overlap = len(q_tokens & p_tokens)
    content_overlap = len(q_tokens & content_tokens)

    score = 0.0

    # Required signals (explicit, inspectable)
    if _is_repo_root_readme(path, project_root):
        score += 100.0
    if _is_under_docs_dir(path):
        score += 25.0

    score += 8.0 * float(path_overlap)
    score += 1.0 * float(min(content_overlap, 10))

    if _is_under_examples_dir(path):
        score -= 10.0

    return score


def _scan_docs_files(project_root: str) -> tuple[list[Path], DocsScanStats]:
    root = Path(project_root).resolve()
    scanned = 0
    included = 0
    excluded = 0

    paths: list[Path] = []
    # Deterministic order: walk root with sorted dirs/files and prune excluded dirs early.
    for dirpath, dirnames, filenames in os.walk(str(root)):
        dirnames[:] = sorted(dirnames)
        filenames = sorted(filenames)
        # Prune excluded directories (prevents descending into large trees like node_modules).
        dirnames[:] = [d for d in dirnames if d.lower() not in _EXCLUDED_DIR_NAMES]
        # Also prune test dirs early.
        dirnames[:] = [d for d in dirnames if d.lower() not in _EXCLUDED_PATH_PARTS]

        for fn in filenames:
            p = Path(dirpath) / fn
            if not p.is_file():
                continue
            scanned += 1
            if _is_docs_candidate_path(p, root):
                included += 1
                paths.append(p)
            else:
                excluded += 1

    return paths, DocsScanStats(scanned_files=scanned, included_files=included, excluded_files=excluded)


def search_docs_candidates(query: str, project_root: str) -> list[dict]:
    """
    Discover docs artifacts deterministically via filesystem scan.

    Returns list of candidates:
      {file, symbol:"", snippet, score, source:"docs_scan", artifact_type:"doc"}
    """
    if not query or not query.strip():
        return []
    root = project_root or os.getcwd()
    paths, _stats = _scan_docs_files(root)

    candidates: list[dict] = []
    for p in paths:
        prefix = _read_text_prefix(p, max_chars=1500)
        heading = _first_heading(prefix)
        preview = heading or "\n".join([ln for ln in prefix.splitlines()[:8] if ln.strip()][:8]).strip()
        if not preview:
            preview = (prefix or "").strip()[:200]

        score = _score_doc_path(query, p, Path(root).resolve(), content_prefix=prefix, heading=heading)
        candidates.append(
            {
                "file": str(p.resolve()),
                "symbol": "",
                "snippet": (preview or "")[:500],
                "score": float(score),
                "source": "docs_scan",
                "artifact_type": "doc",
            }
        )

    candidates.sort(key=lambda c: float(c.get("score") or 0.0), reverse=True)
    return candidates[:20]


def search_docs_candidates_with_stats(query: str, project_root: str) -> tuple[list[dict], dict]:
    """
    Same as search_docs_candidates(), but also returns compact scan stats for observability.
    """
    if not query or not query.strip():
        return [], {"scanned": 0, "included": 0, "excluded": 0}
    root = project_root or os.getcwd()
    paths, stats = _scan_docs_files(root)
    candidates: list[dict] = []
    for p in paths:
        prefix = _read_text_prefix(p, max_chars=1500)
        heading = _first_heading(prefix)
        preview = heading or "\n".join([ln for ln in prefix.splitlines()[:8] if ln.strip()][:8]).strip()
        if not preview:
            preview = (prefix or "").strip()[:200]
        score = _score_doc_path(query, p, Path(root).resolve(), content_prefix=prefix, heading=heading)
        candidates.append(
            {
                "file": str(p.resolve()),
                "symbol": "",
                "snippet": (preview or "")[:500],
                "score": float(score),
                "source": "docs_scan",
                "artifact_type": "doc",
            }
        )
    candidates.sort(key=lambda c: float(c.get("score") or 0.0), reverse=True)
    out = candidates[:20]
    top_ranked = [
        {"file": str((c.get("file") or "")[:140]), "score": float(c.get("score") or 0.0)}
        for c in out[:8]
    ]
    return out, {
        "scanned": stats.scanned_files,
        "included": stats.included_files,
        "excluded": stats.excluded_files,
        "top_ranked": top_ranked,
    }


def build_docs_context(
    query: str,
    project_root: str,
    candidates: list[dict] | None = None,
) -> list[dict]:
    """
    Build ranked_context blocks for docs mode. Reads top-ranked doc files only.

    Returns list of context entries:
      {file, symbol:"", snippet, artifact_type:"doc", title?, line_start?, line_end?}
    """
    if not query or not query.strip():
        return []

    if not candidates:
        candidates = search_docs_candidates(query, project_root)

    root = Path(project_root or os.getcwd()).resolve()
    ranked_context: list[dict] = []

    max_files = 4
    max_chars_per_file = 5000

    for c in (candidates or [])[:max_files]:
        file_str = (c.get("file") or "").strip()
        if not file_str:
            continue
        p = Path(file_str)
        if not p.is_absolute():
            p = (root / file_str).resolve()
        if not p.is_file():
            continue
        if _is_excluded_path(p, root):
            continue
        if not _looks_like_text_file(p):
            continue

        text = _read_text_prefix(p, max_chars=max_chars_per_file)
        if not text.strip():
            continue
        title = _first_heading(text)
        snippet = text.strip()

        # Approximate line range for the included prefix.
        line_count = max(1, snippet.count("\n") + 1)

        entry: dict = {
            "file": str(p.resolve()),
            "symbol": "",
            "snippet": snippet,
            "artifact_type": "doc",
        }
        if title:
            entry["title"] = title
        entry["line_start"] = 1
        entry["line_end"] = line_count
        ranked_context.append(entry)

    # Keep order as ranked by candidates.
    return ranked_context

