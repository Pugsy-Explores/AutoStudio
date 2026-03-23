"""Central loader: loads YAML from prompt_versions or legacy prompts/, applies template variables."""

from pathlib import Path

import yaml

from agent.prompt_system.prompt_template import PromptTemplate

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
# agent/prompt_versions/{name}/{version}.yaml (parent.parent = agent/)
_PROMPT_VERSIONS_DIR = Path(__file__).resolve().parent.parent / "prompt_versions"


def _load_yaml(path: Path) -> dict:
    """Load YAML file; return dict."""
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _raw_to_template(name: str, version: str, raw: dict) -> PromptTemplate:
    """Convert raw YAML dict to PromptTemplate."""
    # Legacy format: system_prompt, prompt (single key)
    instructions = (
        raw.get("instructions")
        or raw.get("system_prompt")
        or raw.get("prompt")
        or ""
    )
    if isinstance(instructions, str):
        instructions = instructions.strip()
    else:
        instructions = str(instructions or "")

    # Multi-part prompts: collect string values as extra
    extra: dict[str, str] | None = None
    if "main" in raw or "end" in raw:
        extra = {}
        for k in ("main", "end"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                extra[k] = v.strip()
        if not instructions and extra.get("main"):
            instructions = extra["main"]

    return PromptTemplate(
        name=name,
        version=version,
        role=raw.get("role", "system"),
        instructions=instructions,
        constraints=raw.get("constraints") or [],
        output_schema=raw.get("output_schema"),
        extra=extra,
    )


def load_from_versioned(name: str, version: str) -> PromptTemplate | None:
    """Load from agent/prompt_versions/{name}/{version}.yaml. Returns None if not found."""
    path = _PROMPT_VERSIONS_DIR / name / f"{version}.yaml"
    if not path.exists():
        return None
    raw = _load_yaml(path)
    return _raw_to_template(name, version, raw)


def load_from_legacy(file_stem: str, name: str, version: str = "v1") -> PromptTemplate:
    """Load from agent/prompts/{file_stem}.yaml (legacy format)."""
    path = _PROMPTS_DIR / f"{file_stem}.yaml"
    raw = _load_yaml(path)

    # Legacy: extract instructions from system_prompt or prompt key
    instructions = raw.get("system_prompt") or raw.get("prompt") or ""
    if isinstance(instructions, str):
        instructions = instructions.strip()

    # Multi-part (main/end): use main as instructions, store both in extra
    extra: dict[str, str] | None = None
    if "main" in raw or "end" in raw:
        extra = {}
        for k in ("main", "end"):
            v = raw.get(k)
            if isinstance(v, str) and v.strip():
                extra[k] = v.strip()
        if not instructions and extra:
            instructions = extra.get("main", "")

    # Legacy files may have multiple keys - use first string value for single-prompt
    if not instructions and isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(v, str) and v.strip():
                instructions = v.strip()
                break

    return PromptTemplate(
        name=name,
        version=version,
        role="system",
        instructions=instructions,
        constraints=[],
        output_schema=None,
        extra=extra,
    )


def load_prompt(
    name: str,
    version: str = "latest",
    variables: dict | None = None,
) -> PromptTemplate:
    """
    Load prompt by name and version.
    Tries prompt_versions first; falls back to legacy agent/prompts/.
    Applies template variable substitution to instructions.
    """
    if version == "latest":
        version = "v1"

    template = load_from_versioned(name, version)
    if template is None:
        # Fall back to legacy mapping
        template = _load_legacy_by_name(name, version)

    if variables:
        try:
            template = PromptTemplate(
                name=template.name,
                version=template.version,
                role=template.role,
                instructions=template.instructions.format_map(
                    {k: (v if v is not None else "") for k, v in variables.items()}
                ),
                constraints=template.constraints,
                output_schema=template.output_schema,
            )
        except KeyError:
            pass  # Leave unformatted if variable missing

    return template


# Mapping: registry name -> legacy file stem (for fallback when versioned file missing)
_LEGACY_MAP: dict[str, str] = {
    "planner": "planner_system",
    "router": "model_router",
    "critic": "critic_system",
    "retry_planner": "retry_planner_system",
    "replanner": "replanner_system",
    "query_rewrite": "query_rewrite",
    "query_rewrite_with_context": "query_rewrite_with_context",
    "query_rewrite_system": "query_rewrite_system",
    "validate_step": "validate_step",
    "router_logit": "router_logit_system",
    # Phase 13: no legacy fallback (versioned only)
    # explain_system, instruction_router, action_selector, context_ranker_single,
    # context_ranker_batch, replanner_user
    # Phase 15: new prompt modules
    "query_expansion": "query_expansion",
    "context_interpreter": "context_interpreter",
    "patch_generator": "patch_generator",
    "bundle_selector": "bundle_selector",
    "edit_proposal_system": "edit_proposal_system",
    "edit_proposal_user": "edit_proposal_user",
    "retry_planner_user": "retry_planner_user",
    "react_action": "react_action",
}


def _load_legacy_by_name(name: str, version: str) -> PromptTemplate:
    """Load from legacy agent/prompts/ using name -> file_stem map."""
    file_stem = _LEGACY_MAP.get(name)
    if not file_stem:
        raise FileNotFoundError(f"Unknown prompt name: {name}")
    return load_from_legacy(file_stem, name, version)
