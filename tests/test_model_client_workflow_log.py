"""Tests for workflow log extraction of planner template variables (instruction, context_block)."""

from agent.models.model_client import _extract_prompt_context
from agent.prompt_system.registry import get_registry


def test_extract_prompt_context_qwen_split_planner_decision_act() -> None:
    """Model-specific YAML: system + user with USER INSTRUCTION: / CONTEXT / TASK."""
    system = "You are a Decision Module.\nOUTPUT FORMAT\n{\"decision\": \"act\"}"
    user = """USER INSTRUCTION:
Fix the frobulator

--------------------------------
CONTEXT
--------------------------------
USER TASK INTENT: test scope
KEY FINDINGS: none

--------------------------------
TASK
--------------------------------
Decide the NEXT BEST ACTION."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    ctx = _extract_prompt_context(messages)
    assert ctx.get("instruction") == "Fix the frobulator"
    assert "USER TASK INTENT: test scope" in (ctx.get("context_block") or "")
    assert "KEY FINDINGS: none" in (ctx.get("context_block") or "")


def test_extract_prompt_context_flat_user_instruction_latest() -> None:
    """Single-message style: USER INSTRUCTION (latest) and context in one body."""
    body = """
--------------------------------
USER TASK INTENT: scope x

--------------------------------
USER INSTRUCTION (latest):
Do the thing

--------------------------------
OUTPUT FORMAT (STRICT JSON):
{}
"""
    messages = [{"role": "system", "content": body.strip()}]
    ctx = _extract_prompt_context(messages)
    assert ctx.get("instruction") == "Do the thing"
    assert "USER TASK INTENT: scope x" in (ctx.get("context_block") or "")


def test_extract_prompt_context_flat_context_last_dashed_block() -> None:
    """Context without USER TASK INTENT prefix: last --- segment before USER INSTRUCTION (latest)."""
    body = """
SOME RULES
--------------------------------
CURRENT UNDERSTANDING:
all good

--------------------------------
USER INSTRUCTION (latest):
ping

--------------------------------
OUTPUT FORMAT
"""
    messages = [{"role": "system", "content": body.strip()}]
    ctx = _extract_prompt_context(messages)
    assert ctx.get("instruction") == "ping"
    assert "CURRENT UNDERSTANDING:" in (ctx.get("context_block") or "")


def test_planner_decision_act_qwen_yaml_braces_allow_str_format_substitution() -> None:
    """
    system_prompt JSON examples must use {{ }} so str.format does not fail; otherwise
    user_prompt keeps literal {{instruction}} and workflow logs show placeholders.
    """
    reg = get_registry()
    sys_p, usr_p = reg.render_prompt_parts(
        "planner.decision.act",
        version="latest",
        variables={
            "instruction": "my task",
            "context_block": "ctx body",
            "req_decision": "",
        },
        model_name="Qwen2.5-Coder-7B",
    )
    assert "{instruction}" not in (usr_p or "")
    assert "{context_block}" not in (usr_p or "")
    assert "my task" in (usr_p or "")
    assert "ctx body" in (usr_p or "")
    msgs = [
        {"role": "system", "content": sys_p or ""},
        {"role": "user", "content": usr_p or ""},
    ]
    ctx = _extract_prompt_context(msgs)
    assert ctx.get("instruction") == "my task"
    assert "ctx body" in (ctx.get("context_block") or "")


def test_extract_prompt_context_json_blob_does_not_log_incidental_selected_indices() -> None:
    """
    Selector batch user text often embeds JSON-looking snippets in code (e.g. test data).
    Naive { ... } scanning must not surface those as template variable `selected_indices`.
    """
    user = """instruction: task

[0]
file: x.py
outline:
- foo
def test():
    return {"selected_indices": [0], "ok": true}
"""
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": user}]
    ctx = _extract_prompt_context(messages)
    assert "selected_indices" not in ctx

