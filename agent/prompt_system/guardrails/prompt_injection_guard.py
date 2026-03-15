"""Detect prompt injection patterns in user input."""

import re


class PromptInjectionError(Exception):
    """Raised when prompt injection is detected."""

    pass


# Common injection patterns (case-insensitive)
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions",
    r"disregard\s+(all\s+)?(previous|above|prior)\s+instructions",
    r"forget\s+(all\s+)?(previous|above|prior)\s+instructions",
    r"you\s+are\s+now\s+(a\s+)?(different|new)\s+",
    r"pretend\s+you\s+are",
    r"act\s+as\s+if\s+you\s+(are|were)",
    r"new\s+instructions\s*:",
    r"system\s*:\s*",
    r"\[INST\]|\[/INST\]",
    r"<\|im_start\|>|<\|im_end\|>",
    r"override\s+(the\s+)?(system\s+)?prompt",
    r"bypass\s+(the\s+)?(safety|guardrails)",
]


def check_prompt_injection(user_input: str) -> None:
    """
    Check user input for prompt injection patterns.
    Raises PromptInjectionError if detected.
    """
    if not user_input or not isinstance(user_input, str):
        return
    text = user_input.strip()
    if len(text) < 20:
        return
    lower = text.lower()
    for pattern in _INJECTION_PATTERNS:
        if re.search(pattern, lower, re.IGNORECASE):
            raise PromptInjectionError(f"Prompt injection detected: pattern matched '{pattern}'")
