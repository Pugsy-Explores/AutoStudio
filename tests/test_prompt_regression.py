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
    "edit_proposal_system",
    "edit_proposal_user",
    "retry_planner_user",
    "react_action",
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
    assert registry.get_model_type("edit_proposal_system") == ModelType.REASONING
    assert registry.get_model_type("edit_proposal_user") == ModelType.REASONING
    assert registry.get_model_type("retry_planner_user") == ModelType.REASONING
    assert registry.get_model_type("react_action") == ModelType.REASONING


def test_edit_prompt_equivalence():
    """Registry-rendered edit prompts match expected output (no behavior change after migration)."""
    registry = get_registry()

    # Golden expected strings (current edit_proposal prompts)
    expected_system = """You are editing code. Produce a minimal valid patch.

Your goal is to move the code closer to satisfying the instruction, not to guarantee correctness in a single attempt. The system will run tests and refine; your job is to produce an actionable patch.

VAGUE INSTRUCTIONS:
- Instructions like "fix bug" or "make it work" cause wrong patches.
- You must have: (1) read the target file, (2) identified the exact location (line/symbol), (3) described the specific change.
- If the instruction is vague, prefer already_correct or a minimal safe no-op. Do not guess.

HARD CONSTRAINTS — violations cause patch rejection:
1. Target anchoring: You are editing ONLY the specified target file. Do not propose changes for other files. No cross-file edits.
2. Exact snippet for text_sub: "old" MUST be an exact substring of the file. Copy character-for-character from the Full file content below. No approximations.
3. No noop: old != new for text_sub (the change must differ from the original).

GROUNDING:
- When replacing code, copy exact text from the file.
- When adding or modifying logic, ensure the change integrates with existing code.
- Avoid cosmetic or irrelevant edits.
- It is acceptable to make a best-effort modification based on the instruction, even if you are not fully certain it will pass all tests. Limit changes to the smallest possible region that affects the target behavior.

ALREADY CORRECT:
- If the code already satisfies the instruction and no change is needed, return: {"action": "already_correct", "already_correct": true}
- Do NOT force a change when the existing code is correct. Unnecessary edits cause regressions.

Output exactly one JSON object with one of:
- action: "text_sub" for string replacement — with "old" (exact substring), "new" (replacement)
- action: "insert" for adding code at a symbol — with "symbol", "target_node": "function_body_start", "code"
- action: "already_correct" — when the code already satisfies the instruction
- confident: (optional) true if you are confident in the fix, false if best-effort

Output ONLY the JSON object, no markdown, no explanation."""

    variables = {
        "instruction": "Add a log statement",
        "target_file": "src/foo.py",
        "symbol": "(any)",
        "evidence": "def bar():\n    pass",
        "full_content": "def bar():\n    pass\n",
    }
    expected_user = """Instruction:
Add a log statement

You are editing file: src/foo.py
You MUST ONLY modify this file. Do not propose changes for other files.

Target file: src/foo.py
Symbol: (any)

Relevant context (when replacing, copy exact text from Full file content below):
def bar():
    pass

Full file content:
```
def bar():
    pass

```

Produce a minimal valid patch (JSON only). For text_sub: "old" must be an exact copy from the file above.
If the instruction does not specify what to change (e.g. "fix bug"), do not guess. Return already_correct or a minimal safe change."""

    system = registry.get_instructions("edit_proposal_system")
    user = registry.get_instructions("edit_proposal_user", variables=variables)

    assert system == expected_system, "edit_proposal_system content changed after migration"
    assert user == expected_user, "edit_proposal_user rendered output changed after migration"


def test_retry_planner_prompt_equivalence():
    """Registry-rendered retry_planner user prompt matches expected output (no behavior change after migration)."""
    registry = get_registry()
    variables = {
        "goal": "Fix the bug",
        "failure_type": "bad_patch",
        "affected_step": "EDIT",
        "suggestion": "Use exact text from file",
    }
    expected_user = """Goal: Fix the bug
Diagnosis:
  failure_type: bad_patch
  affected_step: EDIT
  suggestion: Use exact text from file

Produce retry hints as JSON."""

    user = registry.get_instructions("retry_planner_user", variables=variables)
    assert user == expected_user, "retry_planner_user rendered output changed after migration"


def test_react_action_prompt_variables():
    """react_action prompt substitutes instruction and react_history correctly."""
    registry = get_registry()
    rendered = registry.get_instructions(
        "react_action",
        variables={"instruction": "Fix the bug in foo.py", "react_history": "(none yet)"},
    )
    assert "Fix the bug in foo.py" in rendered
    assert "(none yet)" in rendered
    assert "STRICT JSON ONLY" in rendered or "thought" in rendered
    assert "search" in rendered and "edit" in rendered and "finish" in rendered
