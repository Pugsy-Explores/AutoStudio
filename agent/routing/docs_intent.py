"""
Stage 38: Deterministic docs-artifact and mixed docs+code intent detection.

Moved from plan_resolver so production routing has a single ownership surface.
No benchmark-specific logic; bounded token lists only.
"""

from __future__ import annotations

# Re-exported for observability (e.g. two_phase_near_miss) and tests.
DOCS_INTENT_TOKENS: tuple[str, ...] = (
    "readme",
    "docs",
    "documentation",
    "documented",
    "architecture docs",
    "setup docs",
    "install",
    "installation",
    "usage",
    "guide",
)

DOCS_DISCOVERY_VERBS: tuple[str, ...] = (
    "where",
    "locate",
    "find",
    "list",
    "show",
)

# Generic code-intent markers; keep bounded and domain-neutral.
NON_DOCS_TOKENS: tuple[str, ...] = (
    "implemented",
    "implementation",
    "class ",
    "function ",
    "method ",
    "refactor",
    "edit ",
    "change ",
    "patch",
    "bug",
    "stack trace",
    "exception",
    "explain",
    "flow",
    "undocumented",
)


def is_docs_artifact_intent(instruction: str) -> bool:
    """
    Bounded, deterministic docs-artifact intent detector.
    True when the user is asking to locate/read documentation artifacts
    (README/docs/documentation) without mixed code-intent markers.
    """
    if not instruction:
        return False
    i = instruction.strip().lower()
    if not i:
        return False
    has_discovery_verb = any(v in i for v in DOCS_DISCOVERY_VERBS)
    if not has_discovery_verb:
        return False
    has_docs = any(t in i for t in DOCS_INTENT_TOKENS)
    if not has_docs:
        return False
    has_non_docs = any(t in i for t in NON_DOCS_TOKENS)
    return not has_non_docs


def is_two_phase_docs_code_intent(instruction: str) -> bool:
    """
    True when instruction mixes docs-discovery with code-intent (e.g. find docs + explain code).
    Must return False if is_docs_artifact_intent(instruction) is True.
    """
    if not instruction or not instruction.strip():
        return False
    if is_docs_artifact_intent(instruction):
        return False
    lower = instruction.strip().lower()
    has_discovery = any(v in lower for v in DOCS_DISCOVERY_VERBS)
    if not has_discovery:
        return False
    has_docs = any(t in lower for t in DOCS_INTENT_TOKENS)
    if not has_docs:
        return False
    code_markers = ("explain", "flow", "function ", "method ", "class ")
    has_code_intent = any(m in lower for m in code_markers)
    return has_code_intent
