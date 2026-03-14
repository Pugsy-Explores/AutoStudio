"""Filesystem adapter using pathlib and open."""

from pathlib import Path


def read_file(path: str) -> str:
    """Read file contents. Raises OSError on failure."""
    p = Path(path).resolve()
    return p.read_text(encoding="utf-8")


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
