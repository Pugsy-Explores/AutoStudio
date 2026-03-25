"""ReAct prompt context: task classification, history windowing, observation caps.

Phase 3 stabilization — keeps the reasoning model within context budget and
enforces read-vs-code action boundaries without new features.
"""

from __future__ import annotations

import json
import os
import re
from typing import Literal

# Survival: keep recent steps only; hard-cap observation text sent to the model.
MAX_STEPS_IN_CONTEXT = 2
MAX_OBS_CHARS = 1200

TaskMode = Literal["read_only", "code_change"]

# Heuristic: default read_only (safer). Escalate to code_change when the user
# clearly asks to change the codebase.
_CODE_CHANGE_HINTS = re.compile(
    r"\b("
    r"fix|implement|add\b|change\b|modify|refactor|remove\b|delete\b|"
    r"write\b|create\b|patch|migrate|update\b|replace\b|rename\b|"
    r"introduce\b|delete\s+the|add\s+a\s+test|write\s+tests|"
    r"make\s+it\s+so|edit\s+the"
    r")\b",
    re.IGNORECASE,
)

_READ_STRONG_HINTS = re.compile(
    r"\b("
    r"explain|understanding|describe|what\s+is|what\s+does|how\s+does|how\s+is|"
    r"where\s+is|find\s+where|trace|walk\s+through|meaning|why\s+does|"
    r"how\s+do\s+.*\s+connect|connect\s+to"
    r")\b",
    re.IGNORECASE,
)


def classify_react_task_mode(instruction: str) -> TaskMode:
    """Classify instruction as read-only exploration vs code-changing work."""
    text = (instruction or "").strip()
    if not text:
        return "read_only"
    if _READ_STRONG_HINTS.search(text) and not _CODE_CHANGE_HINTS.search(text):
        return "read_only"
    if _CODE_CHANGE_HINTS.search(text):
        return "code_change"
    return "read_only"


def normalize_path_for_dedup(path: str) -> str:
    if not path or not isinstance(path, str):
        return ""
    expanded = os.path.expanduser(path.strip())
    try:
        return os.path.normcase(os.path.abspath(expanded))
    except OSError:
        return os.path.normcase(os.path.normpath(expanded))


def truncate_observation(text: str, max_chars: int = MAX_OBS_CHARS) -> str:
    if text is None:
        return ""
    s = str(text)
    if len(s) <= max_chars:
        return s
    return s[: max_chars - 3] + "..."


def format_react_history_for_prompt(
    history: list,
    *,
    max_steps: int = MAX_STEPS_IN_CONTEXT,
    max_obs_chars: int = MAX_OBS_CHARS,
) -> str:
    """Build HISTORY block for the model: last N steps, hard-truncated observations."""
    if not history:
        return "(none yet)"
    window = history[-max_steps:] if len(history) > max_steps else history
    lines: list[str] = []

    for entry in window:
        lines.append(f"Thought: {entry.get('thought', '')}")
        lines.append(f"Action: {entry.get('action', '')}")
        lines.append(f"Args: {json.dumps(entry.get('args', {}))}")
        obs = truncate_observation(entry.get("observation", "") or "", max_obs_chars)
        lines.append(f"Observation: {obs}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_react_task_section(mode: TaskMode) -> str:
    """Injected into react_action prompt: task-type rules + repo search bias."""
    bias = """## REPOSITORY SEARCH BIAS (CRITICAL)

When interpreting search results and choosing files:
- PREFER paths under `agent_v2/` (current agent runtime and schemas).
- DEPRIORITIZE legacy `agent/` tree unless the task explicitly refers to legacy code.
- If both trees match, choose `agent_v2/` first.
"""

    if mode == "read_only":
        return (
            bias
            + """
## TASK TYPE: READ / UNDERSTAND / EXPLAIN (LOCKED)

Allowed actions ONLY: search, open_file, finish

FORBIDDEN in this task (do NOT call):
- edit
- run_tests

Workflow:
1. search → narrow to relevant files (prefer agent_v2/)
2. open_file → read what you need
3. finish → summarize findings in your thought when you output finish (task is done when you can answer the question)

Do NOT run tests or edit files for explanation tasks.

## SUCCESS (read tasks)

Call finish when you have enough information to answer the instruction. Tests are NOT required.
"""
        )

    return (
        bias
        + """
## TASK TYPE: MODIFY CODE

Allowed actions: search, open_file, edit, run_tests, finish

Typical workflow:
1. search → find relevant files
2. open_file → read and understand code
3. edit → apply a precise change
4. run_tests → verify when appropriate
5. finish → when the task is solved and tests pass (if you edited)

## SUCCESS (code tasks)

Call finish when the change is done and tests pass (after edits).
"""
    )


def json_action_list_for_mode(mode: TaskMode) -> str:
    if mode == "read_only":
        return "search | open_file | finish"
    return "search | open_file | edit | run_tests | finish"
