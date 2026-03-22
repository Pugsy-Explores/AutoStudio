"""General edit proposal generator. Uses model to produce patches from context.

No domain heuristics, no pattern matching, no rule-based fixes.
Let generator propose, validator filter.

Invariant: evidence_span ⊆ full_file_content before EDIT generation.
When violated, evidence is recomputed from full_file_content (file-derived).
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from agent.models.model_client import call_reasoning_model
from agent.prompt_system import get_registry
from config.agent_runtime import SERENA_PROJECT_DIR
from config.editing_config import (
    EDIT_PROPOSAL_EVIDENCE_MAX,
    EDIT_PROPOSAL_MAX_CONTENT,
    EDIT_PROPOSAL_SYMBOL_BLOCK_MAX,
)

logger = logging.getLogger(__name__)

# Placeholder when binding has no evidence
_NO_EVIDENCE_PLACEHOLDER = "(no excerpt)"


def _extract_symbol_block_from_file(content: str, symbol: str) -> str:
    """Extract def/class block for symbol from file content.
    Returns a substring of content (file-derived, so always in content).
    """
    if not content.strip():
        return content[:EDIT_PROPOSAL_SYMBOL_BLOCK_MAX] if content else ""
    if not symbol or not symbol.strip():
        return content[:EDIT_PROPOSAL_SYMBOL_BLOCK_MAX]

    lines = content.split("\n")
    start_idx = None
    base_indent = None

    for i, line in enumerate(lines):
        stripped = line.strip()
        if re.match(rf"^(def|class)\s+{re.escape(symbol)}\s*[\(:]", stripped):
            start_idx = i
            base_indent = len(line) - len(line.lstrip()) if line.strip() else 0
            break

    if start_idx is None:
        return content[:EDIT_PROPOSAL_SYMBOL_BLOCK_MAX]

    result = [lines[start_idx]]
    for i in range(start_idx + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            result.append(line)
            continue
        curr_indent = len(line) - len(line.lstrip())
        if curr_indent <= base_indent and re.match(r"^\s*(def|class)\s+", line):
            break
        result.append(line)
        if len(result) > 35:
            break

    return "\n".join(result)


def _ensure_evidence_file_consistency(
    full_content: str,
    evidence_text: str,
    symbol: str,
) -> str:
    """
    Enforce invariant: evidence_span ⊆ full_file_content.
    If violated, recompute evidence from full_content (file-derived).
    Returns evidence guaranteed to be a substring of full_content.
    """
    if not evidence_text or evidence_text.strip() == _NO_EVIDENCE_PLACEHOLDER:
        return _extract_symbol_block_from_file(full_content, symbol) or full_content[:EDIT_PROPOSAL_SYMBOL_BLOCK_MAX]

    stripped = evidence_text.strip()
    if stripped and stripped in full_content:
        idx = full_content.find(stripped)
        return full_content[idx : idx + len(stripped)]

    logger.info(
        "[edit_proposal] evidence not in file, refreshing from full_content (symbol=%s)",
        symbol or "(none)",
    )
    return _extract_symbol_block_from_file(full_content, symbol) or full_content[:EDIT_PROPOSAL_SYMBOL_BLOCK_MAX]


def _build_proposal_from_binding(
    binding: dict,
    instruction: str,
    project_root: str,
) -> dict | None:
    """Create a proposal dict from edit_binding for model input."""
    file_path = binding.get("file")
    if not file_path:
        return None
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(project_root) / file_path
    if not p.is_file():
        return None
    try:
        full_content = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    evidence = binding.get("evidence", [])
    evidence_text = (
        "\n".join(str(e) for e in evidence) if evidence else _NO_EVIDENCE_PLACEHOLDER
    )
    symbol = binding.get("symbol", "")

    evidence_text = _ensure_evidence_file_consistency(
        full_content, evidence_text, symbol
    )

    return {
        "file": str(p.relative_to(Path(project_root).resolve())).replace("\\", "/")
        if Path(project_root).resolve() in p.resolve().parents
        else file_path,
        "symbol": symbol,
        "instruction": instruction,
        "full_content": full_content,
        "evidence": evidence_text,
    }


def _compute_edit_generation_debug(
    patch: dict,
    full_content: str,
    target_file: str,
) -> dict:
    """Compute observability fields for patch_debug. No heuristics, pure checks."""
    old_present = None
    is_noop = None
    grounded = None
    if patch and patch.get("action") == "text_sub":
        old_snippet = patch.get("old", "")
        new_snippet = patch.get("new", "")
        old_present = old_snippet in full_content if old_snippet else False
        is_noop = (old_snippet == new_snippet) if (old_snippet and new_snippet is not None) else False
        grounded = old_present
    return {
        "target_file": target_file,
        "old_present": old_present,
        "is_noop": is_noop,
        "grounded": grounded,
    }


def _parse_model_patch(raw: str) -> tuple[dict | None, dict]:
    """Extract JSON patch from model output. Returns (patch_dict | None, meta)."""
    if not raw or not raw.strip():
        return (None, {})
    text = raw.strip()
    # Strip markdown code block if present
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1).strip()
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return (None, {})
    if not isinstance(obj, dict):
        return (None, {})
    meta: dict = {}
    if "confident" in obj:
        meta["confident"] = bool(obj["confident"])
    action = obj.get("action")
    if action == "text_sub":
        old = obj.get("old", "")
        new = obj.get("new", "")
        if not str(old).strip():
            return (None, meta)
        return ({"action": "text_sub", "old": str(old), "new": str(new)}, meta)
    if action == "insert":
        symbol = obj.get("symbol", "")
        code = obj.get("code", "")
        target_node = obj.get("target_node", "function_body_start")
        if not symbol or not str(code).strip():
            return (None, meta)
        valid_nodes = (
            "function_body_start", "function_body", "class_body_start", "class_body",
            "statement", "statement_after", "if_block", "try_block", "with_block", "for_block",
        )
        if target_node not in valid_nodes:
            target_node = "function_body_start"
        return ({
            "action": "insert",
            "symbol": symbol,
            "target_node": target_node,
            "code": str(code).strip(),
        }, meta)
    return (None, meta)


def _generate_patch_via_model(proposal: dict) -> tuple[dict | None, dict]:
    """Call reasoning model to produce a patch. Returns (patch dict | None, meta)."""
    instruction = proposal.get("instruction", "")
    full_content = proposal.get("full_content", "")
    evidence = proposal.get("evidence", "")
    symbol = proposal.get("symbol", "")

    # Truncate file content to avoid token limits; keep enough for context
    if len(full_content) > EDIT_PROPOSAL_MAX_CONTENT:
        full_content = full_content[:EDIT_PROPOSAL_MAX_CONTENT] + "\n\n... (truncated)"
    evidence_truncated = evidence[:EDIT_PROPOSAL_EVIDENCE_MAX]

    target_file = proposal.get("file", "")
    symbol_display = symbol or "(any)"

    registry = get_registry()
    system_prompt = registry.get_instructions("edit_proposal_system")
    user_prompt = registry.get_instructions(
        "edit_proposal_user",
        variables={
            "instruction": instruction,
            "target_file": target_file,
            "symbol": symbol_display,
            "evidence": evidence_truncated,
            "full_content": full_content,
        },
    )

    try:
        response = call_reasoning_model(
            user_prompt,
            system_prompt=system_prompt,
            task_name="planner",
        )
    except Exception as e:
        logger.warning("[edit_proposal_generator] model call failed: %s", e)
        return (None, {})

    return _parse_model_patch(response)


def generate_edit_proposals(context: dict, instruction: str, project_root: str | None = None) -> list[dict]:
    """
    Generate edit proposals from edit_binding and instruction using the model.

    Returns list of change dicts: [{file, patch, patch_strategy}, ...].
    Each patch is executor-ready (text_sub or insert format).
    """
    root = (
        project_root
        or context.get("project_root")
        or SERENA_PROJECT_DIR
        or os.getcwd()
    )
    binding = context.get("edit_binding")
    if not binding or not isinstance(binding, dict):
        # Fallback: use edit target from plan_diff when no edit_binding
        chosen = (
            context.get("chosen_target_file")
            or context.get("edit_target_file")
            or ""
        )
        if not chosen:
            return []
        binding = {"file": chosen, "symbol": context.get("edit_target_symbol", ""), "evidence": []}

    proposal = _build_proposal_from_binding(binding, instruction, root)
    if not proposal:
        return []

    patch, meta = _generate_patch_via_model(proposal)
    if not patch:
        logger.info("[edit_proposal_generator] no valid patch from model for %s", proposal.get("file"))
        return []

    file_path = proposal.get("file", "")
    full_content = proposal.get("full_content", "")
    edit_generation_debug = _compute_edit_generation_debug(patch, full_content, file_path)
    if "confident" in meta:
        edit_generation_debug["confident"] = meta["confident"]
    logger.info(
        "[edit_generation] target_file=%s old_present=%s is_noop=%s grounded=%s confident=%s",
        edit_generation_debug.get("target_file"),
        edit_generation_debug.get("old_present"),
        edit_generation_debug.get("is_noop"),
        edit_generation_debug.get("grounded"),
        edit_generation_debug.get("confident"),
    )
    return [
        {
            "file": file_path,
            "patch": patch,
            "patch_strategy": "model_generated",
            "edit_generation_debug": edit_generation_debug,
        }
    ]
