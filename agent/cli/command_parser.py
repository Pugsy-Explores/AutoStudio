"""Parse slash-commands and map to router intents. Prepends intent hint to instruction."""

import re
from dataclasses import dataclass
from typing import Literal

RouterIntent = Literal["EXPLAIN", "EDIT", "SEARCH", "NAVIGATE"]


@dataclass
class ParsedCommand:
    """Result of parsing a user input."""

    intent: RouterIntent | None
    instruction: str
    raw: str


# Slash-command patterns: /command [args]
SLASH_PATTERNS = [
    (r"^/explain\s+(.+)$", "EXPLAIN", lambda m: f"Explain how {m.group(1).strip()} works"),
    (r"^/fix\s+(.+)$", "EDIT", lambda m: f"Fix: {m.group(1).strip()}"),
    (r"^/refactor\s+(.+)$", "EDIT", lambda m: f"Refactor: {m.group(1).strip()}"),
    (r"^/add-logging\s*$", "EDIT", lambda m: "Add logging to the relevant code"),
    (r"^/add-logging\s+(.+)$", "EDIT", lambda m: f"Add logging to {m.group(1).strip()}"),
    (r"^/find\s+(.+)$", "SEARCH", lambda m: f"Find {m.group(1).strip()}"),
]


def parse_command(user_input: str) -> ParsedCommand:
    """
    Parse user input. If it starts with a slash-command, extract intent and instruction.
    Otherwise return instruction as-is with no intent hint.
    """
    raw = (user_input or "").strip()
    if not raw:
        return ParsedCommand(intent=None, instruction="", raw=raw)

    for pattern, intent, instruction_fn in SLASH_PATTERNS:
        m = re.match(pattern, raw, re.IGNORECASE)
        if m:
            instruction = instruction_fn(m)
            return ParsedCommand(intent=intent, instruction=instruction, raw=raw)

    # No slash-command: pass through as instruction
    return ParsedCommand(intent=None, instruction=raw, raw=raw)


def to_instruction_with_hint(parsed: ParsedCommand) -> str:
    """
    Produce instruction string with optional intent hint for the router.
    The router receives hint-augmented instruction; hint format is [INTENT: ...].
    """
    if not parsed.instruction:
        return ""
    if parsed.intent:
        return f"[{parsed.intent}] {parsed.instruction}"
    return parsed.instruction
