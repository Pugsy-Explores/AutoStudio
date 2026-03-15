"""Version store: get_prompt(name, version), list_versions(name)."""

from pathlib import Path

from agent.prompt_system.loader import load_prompt
from agent.prompt_system.prompt_template import PromptTemplate

_PROMPT_VERSIONS_DIR = Path(__file__).resolve().parent.parent.parent / "prompt_versions"


def list_versions(name: str) -> list[str]:
    """List available versions for a prompt (e.g. ['v1', 'v2'])."""
    dir_path = _PROMPT_VERSIONS_DIR / name
    if not dir_path.is_dir():
        return []
    versions = []
    for p in dir_path.iterdir():
        if p.suffix == ".yaml" and p.stem:
            versions.append(p.stem)
    return sorted(versions)


def get_prompt(name: str, version: str = "latest") -> PromptTemplate:
    """
    Get prompt by name and version.
    version='latest' resolves to the highest available (e.g. v2 over v1).
    """
    if version == "latest":
        versions = list_versions(name)
        version = versions[-1] if versions else "v1"
    return load_prompt(name, version=version)
