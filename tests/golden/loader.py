"""Golden test loader — load JSON files, no transformations."""

import json
from pathlib import Path
from typing import List

from tests.golden.schema import GoldenTest


def load_json(path: Path) -> List[GoldenTest]:
    """Load a single JSON file. Expects a JSON array of test objects."""
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        return [data]
    return data


def load_from_file(path: str | Path) -> List[GoldenTest]:
    """Load tests from a single JSON file."""
    p = Path(path)
    if not p.exists():
        return []
    raw = load_json(p)
    return [_normalize(t) for t in raw]


def load_from_dir(path: str | Path) -> List[GoldenTest]:
    """Load all .json files from a directory (non-recursive)."""
    p = Path(path)
    if not p.is_dir():
        return []
    tests: List[GoldenTest] = []
    for f in sorted(p.glob("*.json")):
        tests.extend(load_from_file(f))
    return tests


def load(path: str | Path) -> List[GoldenTest]:
    """Load from a file or directory. Single entry point."""
    p = Path(path)
    if p.is_file():
        return load_from_file(p)
    if p.is_dir():
        return load_from_dir(p)
    return []


def _normalize(t: dict) -> GoldenTest:
    """Ensure llm_judge is present when absent (for TypedDict)."""
    if "llm_judge" not in t:
        t = {**t, "llm_judge": None}
    return t
