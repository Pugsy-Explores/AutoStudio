"""Filesystem adapter using pathlib and open."""

from pathlib import Path
from typing import Iterable


def _safe_open_text(path: Path):
    # utf-8 is normative across repo; tolerate partial decoding rather than failing exploration.
    return path.open("r", encoding="utf-8", errors="replace")


def read_file(path: str) -> str:
    """Read file contents. Raises OSError on failure."""
    p = Path(path).resolve()
    return p.read_text(encoding="utf-8")


def read_file_head(path: str, *, max_lines: int = 200, max_chars: int = 12000) -> str:
    """
    Read only the first max_lines (and cap to max_chars) from a file.
    This is a bounded-at-read-time primitive (does not load full file).
    """
    p = Path(path).resolve()
    out_lines: list[str] = []
    total = 0
    with _safe_open_text(p) as f:
        for i, line in enumerate(f):
            if i >= max_lines:
                break
            if not line:
                continue
            remaining = max_chars - total
            if remaining <= 0:
                break
            if len(line) > remaining:
                out_lines.append(line[:remaining])
                total += remaining
                break
            out_lines.append(line)
            total += len(line)
    return "".join(out_lines)


def read_file_line_window(
    path: str,
    *,
    center_line: int,
    window: int = 80,
    max_chars: int = 12000,
) -> tuple[str, int, int]:
    """
    Read a bounded window of lines around center_line (1-indexed).
    Returns (content, start_line, end_line).
    """
    p = Path(path).resolve()
    cl = int(center_line or 1)
    if cl < 1:
        cl = 1
    w = int(window or 80)
    if w < 5:
        w = 5
    if w > 400:
        w = 400

    start = max(1, cl - w)
    end = cl + w

    out_lines: list[str] = []
    total = 0
    with _safe_open_text(p) as f:
        for lineno, line in enumerate(f, start=1):
            if lineno < start:
                continue
            if lineno > end:
                break
            remaining = max_chars - total
            if remaining <= 0:
                break
            if len(line) > remaining:
                out_lines.append(line[:remaining])
                total += remaining
                break
            out_lines.append(line)
            total += len(line)
    content = "".join(out_lines)
    return content, start, min(end, start + max(len(out_lines) - 1, 0))


def read_file_line_range(
    path: str,
    *,
    start_line: int,
    end_line: int,
    max_chars: int = 12000,
) -> tuple[str, int, int]:
    """
    Read an inclusive line range (1-indexed), bounded by max_chars.
    Returns (content, start_line, end_line_read).
    """
    p = Path(path).resolve()
    s = int(start_line or 1)
    e = int(end_line or s)
    if s < 1:
        s = 1
    if e < s:
        e = s
    # hard cap range even if caller misbehaves
    if (e - s) > 1200:
        e = s + 1200

    out_lines: list[str] = []
    total = 0
    with _safe_open_text(p) as f:
        for lineno, line in enumerate(f, start=1):
            if lineno < s:
                continue
            if lineno > e:
                break
            remaining = max_chars - total
            if remaining <= 0:
                break
            if len(line) > remaining:
                out_lines.append(line[:remaining])
                total += remaining
                break
            out_lines.append(line)
            total += len(line)
    content = "".join(out_lines)
    end_read = s + max(len(out_lines) - 1, 0)
    return content, s, end_read


def write_file(path: str, content: str) -> None:
    """Write content to file. Raises OSError on failure."""
    p = Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def list_files(directory: str) -> list:
    """List entries in directory. Returns list of path strings (names only)."""
    p = Path(directory).resolve()
    if not p.is_dir():
        raise NotADirectoryError(f"Not a directory: {p}")
    return [str(item.name) for item in p.iterdir()]
