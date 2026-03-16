"""
Prompt regression tests: load and validate all prompts via PromptRegistry.

Ensures new and modified prompts load correctly and have expected structure.
Run with: pytest tests/test_prompt_regression.py -v
"""

import pytest

from agent.prompt_system import get_registry


# Prompts that must load and have non-empty instructions
_PROMPT_NAMES = [
    "planner",
    "replanner",
    "critic",
    "retry_planner",
    "query_expansion",
    "context_interpreter",
    "patch_generator",
]


@pytest.mark.parametrize("name", _PROMPT_NAMES)
def test_prompt_loads(name: str):
    """Each prompt loads via registry and has non-empty instructions."""
    registry = get_registry()
    template = registry.get(name)
    assert template is not None, f"Prompt {name} failed to load"
    assert template.instructions, f"Prompt {name} has empty instructions"
    assert template.role == "system", f"Prompt {name} should have role=system, got {template.role}"


@pytest.mark.parametrize("name", ["query_expansion", "context_interpreter", "critic", "retry_planner"])
def test_json_prompts_have_schema(name: str):
    """JSON-output prompts should have output_schema set (or schema in instructions)."""
    registry = get_registry()
    template = registry.get(name)
    assert template is not None
    has_schema = template.output_schema is not None
    has_schema_in_instructions = "Schema:" in template.instructions or "schema" in template.instructions.lower()
    assert has_schema or has_schema_in_instructions, (
        f"Prompt {name} returns JSON but has no output_schema or schema in instructions"
    )


def test_registry_model_types():
    """New prompts have correct model types registered."""
    registry = get_registry()
    from agent.models.model_types import ModelType

    assert registry.get_model_type("query_expansion") == ModelType.SMALL
    assert registry.get_model_type("context_interpreter") == ModelType.REASONING
    assert registry.get_model_type("patch_generator") == ModelType.REASONING
