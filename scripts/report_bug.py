#!/usr/bin/env python3
"""
Create a new bug report from template and update the bug index.

Usage:
    python scripts/report_bug.py "short description of the bug"

Creates dev/bugs/backlog/BUG-XXX_slug.md and appends a row to dev/bugs/bug_index.md.
Uses standard library only. Safe: does not overwrite existing files.
"""

import argparse
import re
import sys
from pathlib import Path


def _project_root() -> Path:
    """Project root (parent of scripts/)."""
    return Path(__file__).resolve().parent.parent


def _slugify(text: str) -> str:
    """Convert title to filename slug: lowercase, spaces -> underscores, alphanumeric only."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[-\s]+", "_", slug)
    return slug[:50] if slug else "bug"


def _discover_max_bug_id(bugs_dir: Path) -> int:
    """Scan backlog, in_progress, resolved for BUG-NNN and return max N."""
    max_id = 0
    pattern = re.compile(r"BUG-(\d+)", re.IGNORECASE)
    for subdir in ("backlog", "in_progress", "resolved"):
        folder = bugs_dir / subdir
        if not folder.is_dir():
            continue
        for f in folder.iterdir():
            if f.suffix == ".md":
                m = pattern.search(f.name)
                if m:
                    max_id = max(max_id, int(m.group(1)))
    index_file = bugs_dir / "bug_index.md"
    if index_file.exists():
        content = index_file.read_text()
        for m in pattern.finditer(content):
            max_id = max(max_id, int(m.group(1)))
    return max_id


def _next_bug_id(bugs_dir: Path) -> str:
    """Return next sequential bug ID, e.g. BUG-004."""
    max_id = _discover_max_bug_id(bugs_dir)
    return f"BUG-{max_id + 1:03d}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a new bug report from template and update bug index."
    )
    parser.add_argument(
        "title",
        nargs="?",
        default="",
        help='Short description of the bug (e.g. "retrieval returned empty context")',
    )
    args = parser.parse_args()

    root = _project_root()
    bugs_dir = root / "dev" / "bugs"
    template_path = bugs_dir / "templates" / "bug_report.md"
    backlog_dir = bugs_dir / "backlog"
    index_path = bugs_dir / "bug_index.md"

    if not bugs_dir.is_dir():
        print("Error: dev/bugs/ directory not found.", file=sys.stderr)
        return 1
    if not template_path.exists():
        print("Error: dev/bugs/templates/bug_report.md not found.", file=sys.stderr)
        return 1
    if not backlog_dir.is_dir():
        print("Error: dev/bugs/backlog/ directory not found.", file=sys.stderr)
        return 1
    if not index_path.exists():
        print("Error: dev/bugs/bug_index.md not found.", file=sys.stderr)
        return 1

    title = args.title.strip()
    if not title:
        print("Error: title is required. Usage: python scripts/report_bug.py \"description\"", file=sys.stderr)
        return 1

    bug_id = _next_bug_id(bugs_dir)
    slug = _slugify(title)
    filename = f"{bug_id}_{slug}.md"
    out_path = backlog_dir / filename

    if out_path.exists():
        print(f"Error: file already exists: {out_path}", file=sys.stderr)
        return 1

    template = template_path.read_text()
    content = template.replace("BUG-XXX", bug_id).replace(
        "Short description of the issue", title
    )

    out_path.write_text(content, encoding="utf-8")
    print(f"Created: {out_path}")

    index_content = index_path.read_text()
    new_row = f"| {bug_id} | {title} | TBD | medium | backlog |\n"
    updated = index_content.rstrip() + "\n" + new_row
    index_path.write_text(updated, encoding="utf-8")
    print(f"Updated: {index_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
