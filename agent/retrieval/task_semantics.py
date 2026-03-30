"""Generic, task-id-free signals for retrieval and editing (docs/code alignment, path hints)."""

from __future__ import annotations

import re


def instruction_suggests_docs_consistency(instruction: str) -> bool:
    """True when the instruction suggests aligning documentation with code."""
    if not instruction:
        return False
    low = instruction.lower()
    return any(
        x in low
        for x in (
            "agree",
            "align",
            "match",
            "consistency",
            "readme",
            ".md",
            "documented",
        )
    )


def validation_check_script_paths_in_instruction(instruction: str) -> list[str]:
    """Paths like check_*.py, scripts/verify_*.py, bin/assert_*.py when named in the instruction."""
    if not instruction:
        return []
    out: list[str] = []
    for pat in (
        r"\b((?:[\w./]+/)?check_[\w]+\.py)\b",
        r"\b(scripts/[\w]+\.py)\b",
        r"\b(bin/assert_[\w]+\.py)\b",
        r"\b(bin/check_[\w]+\.py)\b",
        r"\b(bin/verify_[\w]+\.py)\b",
        r"\b(scripts/assert_[\w]+\.py)\b",
    ):
        for m in re.finditer(pat, instruction, re.I):
            p = m.group(1).strip().replace("\\", "/")
            if p and p not in out:
                out.append(p)
    return out


def instruction_asks_to_modify_validation_script(instruction: str) -> bool:
    """True if instruction explicitly says to modify the test/check/assert script."""
    if not instruction:
        return False
    low = instruction.lower()
    return any(
        x in low
        for x in (
            "modify the test",
            "update the test",
            "change the test",
            "edit the test",
            "modify the assert",
            "update the assert",
            "modify the check script",
            "update the check script",
            "edit bin/assert",
            "edit scripts/assert",
        )
    )


def instruction_edit_target_paths(instruction: str) -> list[str]:
    """
    Paths that are the explicit edit target (e.g. "Fix X in src/valid/check.py").
    Excludes validation scripts (scripts/run_verify.py) when instruction says "Fix X in path".
    Used to prefer edit targets over validation scripts in plan_diff.
    """
    if not instruction:
        return []
    out: list[str] = []
    # "Fix X in path/to/file.py" or "Add Y in path/to/file.py"
    for pat in (
        r"(?:fix|add|edit|modify|change)\s+[\w.]+\s+in\s+([\w./\\]+\.(?:py|pyi|md))\b",
        r"in\s+([\w./\\]+\.(?:py|pyi|md))\s+(?:so|that|to)",
        r"([\w./\\]+\.(?:py|pyi|md))\s+(?:so|that|to)\s+(?:it|the)",
    ):
        for m in re.finditer(pat, instruction, re.I):
            p = m.group(1).strip()
            if p and p not in out:
                out.append(p)
    return out


def instruction_path_hints(instruction: str) -> list[str]:
    """Path literals mentioned in the instruction (Python, markdown, inferred modules)."""
    if not instruction:
        return []
    out = list(re.findall(r"[\w./\\]+\.py\b", instruction))
    out.extend(re.findall(r"[\w./\\]+\.md\b", instruction))
    for m in re.finditer(r"\b([\w./]+)\.([a-zA-Z_]\w*)\b", instruction):
        pathish, _name = m.group(1), m.group(2)
        if "/" in pathish and not pathish.endswith((".py", ".pyi", ".md")):
            cand = f"{pathish}.py"
            if cand not in out:
                out.append(cand)
    for m in re.finditer(r"([\w./]+)\.([a-zA-Z_]\w*)\s*\(", instruction):
        pathish, _name = m.group(1), m.group(2)
        if "/" in pathish and not pathish.endswith((".py", ".pyi", ".md")):
            cand = f"{pathish}.py"
            if cand not in out:
                out.append(cand)
    if "readme" in instruction.lower() and not any("readme" in x.lower() for x in out):
        out.append("README.md")
    for p in validation_check_script_paths_in_instruction(instruction):
        if p not in out:
            out.append(p)
    return out
