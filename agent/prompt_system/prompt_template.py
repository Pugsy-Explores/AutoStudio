"""Structured prompt representation: separates content from governance."""

from dataclasses import dataclass


@dataclass
class PromptTemplate:
    """Structured prompt object instead of plain string."""

    name: str
    version: str
    role: str
    instructions: str
    constraints: list[str]
    output_schema: dict | None
    # For multi-part prompts (e.g. query_rewrite_with_context: main, end)
    extra: dict[str, str] | None = None
