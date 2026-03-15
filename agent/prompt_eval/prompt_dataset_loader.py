"""Load prompt evaluation dataset and yield test cases."""

import json
from pathlib import Path
from typing import Iterator

_DEFAULT_DATASET = Path(__file__).resolve().parent.parent.parent / "tests" / "prompt_eval_dataset.json"


def load_dataset(path: Path | str | None = None) -> list[dict]:
    """Load dataset JSON. Returns list of test cases."""
    p = Path(path) if path else _DEFAULT_DATASET
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def iter_test_cases(path: Path | str | None = None) -> Iterator[dict]:
    """Yield test cases from dataset."""
    for case in load_dataset(path):
        yield case
