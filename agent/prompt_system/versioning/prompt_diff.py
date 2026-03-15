"""Compare two prompt versions: unified diff + semantic summary."""

from dataclasses import dataclass

from agent.prompt_system.versioning.prompt_version_store import get_prompt


@dataclass
class PromptDiff:
    """Result of comparing two prompt versions."""

    name: str
    v1: str
    v2: str
    unified_diff: str
    summary: str


def _compute_unified_diff(a: str, b: str) -> str:
    """Produce unified diff of two strings."""
    import difflib

    lines_a = a.splitlines(keepends=True)
    lines_b = b.splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile="v1",
            tofile="v2",
            lineterm="",
        )
    )
    return "".join(diff)


def compare_prompts(name: str, v1: str, v2: str) -> PromptDiff:
    """
    Compare two prompt versions.
    Returns PromptDiff with unified diff and a brief summary.
    """
    t1 = get_prompt(name, version=v1)
    t2 = get_prompt(name, version=v2)

    diff_str = _compute_unified_diff(t1.instructions, t2.instructions)
    summary = f"Instructions: {len(t1.instructions)} -> {len(t2.instructions)} chars"
    if t1.constraints != t2.constraints:
        summary += "; constraints changed"
    if t1.output_schema != t2.output_schema:
        summary += "; output_schema changed"

    return PromptDiff(
        name=name,
        v1=v1,
        v2=v2,
        unified_diff=diff_str,
        summary=summary,
    )
