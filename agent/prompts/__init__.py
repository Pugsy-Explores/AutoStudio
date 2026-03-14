"""Centralized prompts: loaded from YAML files (literal newlines) in this package."""

from pathlib import Path

import yaml

_PROMPTS_DIR = Path(__file__).resolve().parent


def _load(name: str) -> dict:
    """Load prompt YAML by name; return dict of string values (prompt keys)."""
    path = _PROMPTS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def get_prompt(name: str, key: str | None = None) -> str | dict:
    """
    Get prompt content from a YAML file in this package.
    name: file stem (e.g. 'query_rewrite', 'query_rewrite_with_context').
    key: optional key; if given, return that key's value (str); else return full dict.
    """
    data = _load(name)
    if key is not None:
        return data[key]
    return data
