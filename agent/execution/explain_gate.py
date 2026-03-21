"""Context gate before EXPLAIN: avoid LLM call when ranked_context is empty."""

import logging
import re

from agent.contracts.error_codes import REASON_CODE_INSUFFICIENT_GROUNDING
from agent.memory.state import AgentState
from agent.retrieval.result_contract import RETRIEVAL_RESULT_TYPE_SYMBOL_BODY

logger = logging.getLogger(__name__)

# Re-export for step_dispatcher
__all__ = [
    "REASON_CODE_INSUFFICIENT_GROUNDING",
    "GRAPH_PLACEHOLDER_SNIPPET_PREFIX",
    "code_explain_grounding_ready",
    "ensure_context_before_explain",
    "has_substantive_code_context",
    "ranked_row_is_substantive_for_code_explain",
]

# Must match graph/localization placeholder lines (see localization_engine, step_dispatcher filter).
GRAPH_PLACEHOLDER_SNIPPET_PREFIX = "Symbol from graph:"

# Stage 47: long doc/README-style prose without obvious code tokens (code-lane EXPLAIN still useful).
_MIN_PROSE_CHARS = 48
_MIN_PROSE_WORDS = 8


def _snippet_has_code_shape(s: str) -> bool:
    """Deterministic code-ish signals (not an LLM)."""
    if not s:
        return False
    sl = s
    if any(
        k in sl
        for k in (
            "def ",
            "class ",
            "import ",
            "from ",
            "\n    ",
            "{\n",
            "}\n",
            "();",
            "=>",
            "#include",
            "\t",
        )
    ):
        return True
    if "(" in s and ")" in s and len(s) > 10:
        return True
    if "{" in s or "}" in s:
        return True
    return False


def _snippet_is_long_prose(s: str) -> bool:
    words = s.split()
    return len(s) >= _MIN_PROSE_CHARS and len(words) >= _MIN_PROSE_WORDS


def _snippet_is_bare_identifier_token(s: str) -> bool:
    """Single token like 'run' or 'Settings' with no code structure."""
    t = s.strip()
    if not t or " " in t or "\n" in t:
        return False
    if len(t) > 32:
        return False
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", t))


def ranked_row_is_substantive_for_code_explain(row: dict) -> bool:
    """
    True if this ranked_context row carries enough for code-lane EXPLAIN (deterministic).

    Rejects graph stub lines, empty/whitespace-only snippets, and bare symbol labels.
    Accepts implementation bodies, symbol-body retrieval rows, real code-shaped snippets,
    or long prose (documentation).
    """
    if not isinstance(row, dict):
        return False
    if row.get("implementation_body_present"):
        return True
    if row.get("retrieval_result_type") == RETRIEVAL_RESULT_TYPE_SYMBOL_BODY:
        return True
    snip = (row.get("snippet") or "").strip()
    if not snip:
        return False
    if snip.startswith(GRAPH_PLACEHOLDER_SNIPPET_PREFIX):
        return False
    if _snippet_is_bare_identifier_token(snip):
        return False
    if _snippet_has_code_shape(snip):
        return True
    if _snippet_is_long_prose(snip):
        return True
    return False


def has_substantive_code_context(ranked_context: list | None) -> bool:
    """True if at least one ranked row is substantive for code-lane EXPLAIN."""
    if not ranked_context:
        return False
    return any(ranked_row_is_substantive_for_code_explain(r) for r in ranked_context if isinstance(r, dict))


def code_explain_grounding_ready(step: dict, state: AgentState) -> tuple[bool, dict]:
    """
    Check grounding signals for code-lane EXPLAIN (after Stage 47 substantive gate).
    Returns (ready, signals). Docs lane is not subject to code grounding checks.
    """
    if not isinstance(step, dict):
        step = {}
    artifact_mode = (step.get("artifact_mode") or "code").strip().lower()
    if artifact_mode == "docs":
        return True, {}

    ranked = state.context.get("ranked_context") or []
    if not ranked:
        return False, {
            "reason_code": REASON_CODE_INSUFFICIENT_GROUNDING,
            "signal": "empty_ranked_context",
        }

    for row in ranked:
        if not isinstance(row, dict):
            continue
        if row.get("implementation_body_present") is True:
            return True, {"grounding": "typed_symbol_body"}
        if row.get("retrieval_result_type") == RETRIEVAL_RESULT_TYPE_SYMBOL_BODY:
            return True, {"grounding": "typed_symbol_body"}

    if has_substantive_code_context(ranked):
        return True, {"grounding": "heuristic"}

    return False, {
        "reason_code": REASON_CODE_INSUFFICIENT_GROUNDING,
        "signal": "no_grounding_evidence",
    }


def ensure_context_before_explain(step: dict, state: AgentState) -> tuple[bool, dict | None]:
    """
    Return (has_context, synthetic_search_step_or_none).
    Do NOT call the model.
    If ranked_context is empty, returns (False, synthetic SEARCH step).

    Note: non-empty but placeholder-only ranked_context is handled in dispatch via
    has_substantive_code_context (Stage 47); this helper remains length-only for inject decisions.
    """
    ranked = state.context.get("ranked_context") or []
    if ranked:
        return True, None
    return False, {
        "action": "SEARCH",
        "description": step.get("description", ""),
        "id": step.get("id"),
    }
