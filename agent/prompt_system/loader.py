"""Central loader: loads YAML from prompt_versions or legacy prompts/, applies template variables."""

import logging
import re
from collections import defaultdict
from pathlib import Path

import yaml

from agent.prompt_system.prompt_template import PromptTemplate

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
# agent/prompt_versions/{name}/{version}.yaml (parent.parent = agent/)
_PROMPT_VERSIONS_DIR = Path(__file__).resolve().parent.parent / "prompt_versions"

# Model-specific prompts use v1.yaml, v2.yaml, ... — always load the highest N.
_V_NUM_PROMPT_RE = re.compile(r"^v(\d+)\.yaml$", re.IGNORECASE)
_LOG = logging.getLogger(__name__)


def discover_highest_v_prompt_yaml(model_dir: Path) -> tuple[str, Path]:
    """
    Pick the highest ``vN.yaml`` under ``model_dir``.

    Returns ``("vN", path)`` for the largest N. Raises ``FileNotFoundError`` if the
    directory is missing or contains no matching files.
    """
    if not model_dir.is_dir():
        raise FileNotFoundError(f"Model prompt directory does not exist: {model_dir}")
    best_n: int | None = None
    best_path: Path | None = None
    for p in model_dir.iterdir():
        if not p.is_file():
            continue
        m = _V_NUM_PROMPT_RE.match(p.name)
        if not m:
            continue
        n = int(m.group(1))
        if best_n is None or n > best_n:
            best_n = n
            best_path = p
    if best_n is None or best_path is None:
        raise FileNotFoundError(
            f"No v<number>.yaml prompts in {model_dir} (expected files like v1.yaml, v2.yaml)"
        )
    return (f"v{best_n}", best_path)


def normalize_model_name_for_path(model_name: str | None) -> str | None:
    """
    Lowercase and replace unsafe filename characters for prompt_versions/.../models/<name>/.
    Returns None if empty after normalization.
    """
    if model_name is None:
        return None
    s = str(model_name).strip()
    if not s:
        return None
    s = s.lower()
    s = re.sub(r"[^a-z0-9._-]+", "_", s)
    s = s.strip("._-")
    return s or None


def _load_yaml(path: Path) -> dict:
    """Load YAML file; return dict."""
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


def _is_flat_versioned_registry_name(name: str) -> bool:
    """True for packaged stem names like ``planner.decision.v1`` (directory + model path)."""
    if not name or "/" in name or "\\" in name:
        return False
    return bool(re.search(r"\.v\d+$", str(name)))


def load_from_flat_packaged(
    name: str,
    model_name: str | None = None,
) -> PromptTemplate | None:
    """
    Load model-specific packaged prompts (same layout as exploration).

    Resolves **highest** ``agent/prompt_versions/{name}/models/{normalized_model}/vN.yaml``.

    Example: if both ``v1.yaml`` and ``v2.yaml`` exist, ``v2.yaml`` is loaded.
    """
    if not _is_flat_versioned_registry_name(name):
        return None
    norm = normalize_model_name_for_path(model_name)
    if not norm:
        return None
    model_dir = _PROMPT_VERSIONS_DIR / name / "models" / norm
    if not model_dir.is_dir():
        return None
    ver, model_path = discover_highest_v_prompt_yaml(model_dir)
    raw = _load_yaml(model_path)
    return _raw_to_template(name, ver, raw, source_path=str(model_path.resolve()))


def _raw_to_template(
    name: str,
    version: str,
    raw: dict,
    *,
    source_path: str | None = None,
) -> PromptTemplate:
    """Convert raw YAML dict to PromptTemplate."""
    # Preferred format: separate system/user; fallback to legacy single-field instructions.
    system_prompt = raw.get("system_prompt") or ""
    user_prompt_template = raw.get("user_prompt_template") or raw.get("user_prompt") or ""
    instructions = raw.get("instructions") or raw.get("prompt") or ""
    if not instructions and system_prompt:
        instructions = system_prompt
    if isinstance(instructions, str):
        instructions = instructions.strip()
    else:
        instructions = str(instructions or "")
    if isinstance(system_prompt, str):
        system_prompt = system_prompt.strip()
    else:
        system_prompt = str(system_prompt or "")
    if isinstance(user_prompt_template, str):
        user_prompt_template = user_prompt_template.strip()
    else:
        user_prompt_template = str(user_prompt_template or "")

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
        system_prompt=system_prompt,
        user_prompt_template=user_prompt_template,
        extra=extra,
        source_path=source_path,
    )


def load_from_versioned(
    name: str,
    version: str,
    model_name: str | None = None,
) -> PromptTemplate | None:
    """
    Load from agent/prompt_versions/{name}/{version}.yaml.

    When ``models/{normalized_model}/`` exists, loads the **highest** ``vN.yaml`` there
    (ignores the ``version`` argument for that branch). Otherwise uses ``{version}.yaml``
    under ``{name}/``.
    """
    norm = normalize_model_name_for_path(model_name)
    if norm:
        model_dir = _PROMPT_VERSIONS_DIR / name / "models" / norm
        if model_dir.is_dir():
            ver, model_path = discover_highest_v_prompt_yaml(model_dir)
            raw = _load_yaml(model_path)
            return _raw_to_template(name, ver, raw, source_path=str(model_path.resolve()))
    path = _PROMPT_VERSIONS_DIR / name / f"{version}.yaml"
    if not path.exists():
        return None
    raw = _load_yaml(path)
    return _raw_to_template(name, version, raw, source_path=str(path.resolve()))


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
        system_prompt=(instructions or ""),
        user_prompt_template="",
        extra=extra,
        source_path=str(path.resolve()),
    )


def _format_map_with_defaults(s: str, variables: dict) -> str:
    """Like str.format_map, but missing keys become empty string (optional YAML fields)."""
    d: defaultdict[str, str] = defaultdict(str)
    d.update({k: ("" if v is None else str(v)) for k, v in variables.items()})
    return s.format_map(d)


def _best_effort_replace_known_fields(s: str, variables: dict) -> str:
    """
    Fallback substitution when str.format_map fails due to malformed braces in prompt text.

    This only replaces known `{key}` tokens and leaves all other braces untouched.
    """
    out = s
    for k, v in variables.items():
        out = out.replace("{" + str(k) + "}", "" if v is None else str(v))
    return out


def _apply_prompt_variables(template: PromptTemplate, variables: dict | None) -> PromptTemplate:
    if not variables:
        return template
    try:
        return PromptTemplate(
            name=template.name,
            version=template.version,
            role=template.role,
            instructions=_format_map_with_defaults(template.instructions, variables),
            constraints=template.constraints,
            output_schema=template.output_schema,
            system_prompt=_format_map_with_defaults(template.system_prompt, variables)
            if template.system_prompt
            else "",
            user_prompt_template=_format_map_with_defaults(
                template.user_prompt_template, variables
            )
            if template.user_prompt_template
            else "",
            extra=template.extra,
            source_path=template.source_path,
        )
    except (KeyError, ValueError) as exc:
        _LOG.warning(
            "Prompt formatting fallback for %s@%s due to %s: %s",
            template.name,
            template.version,
            type(exc).__name__,
            exc,
        )
        return PromptTemplate(
            name=template.name,
            version=template.version,
            role=template.role,
            instructions=_best_effort_replace_known_fields(template.instructions, variables),
            constraints=template.constraints,
            output_schema=template.output_schema,
            system_prompt=_best_effort_replace_known_fields(template.system_prompt, variables)
            if template.system_prompt
            else "",
            user_prompt_template=_best_effort_replace_known_fields(
                template.user_prompt_template, variables
            )
            if template.user_prompt_template
            else "",
            extra=template.extra,
            source_path=template.source_path,
        )


def load_prompt(
    name: str,
    version: str = "latest",
    variables: dict | None = None,
    model_name: str | None = None,
) -> PromptTemplate:
    """
    Load prompt by name and version.
    Tries prompt_versions first (optional model-specific file); falls back to legacy agent/prompts/.
    Applies template variable substitution to instructions.

    For flat registry names (``*.vN``) and for ``prompt_versions/.../models/<model>/``, the
    **highest** ``vK.yaml`` under the model directory is always used; the ``version``
    parameter does not pin the file in those cases.
    """
    template: PromptTemplate | None = None
    if _is_flat_versioned_registry_name(name):
        template = load_from_flat_packaged(name, model_name=model_name)
        if template is None:
            norm = normalize_model_name_for_path(model_name)
            base = _PROMPT_VERSIONS_DIR / name / "models" / (norm or "<model>")
            raise FileNotFoundError(
                f"Packaged prompt not found for {name!r}: need directory {base} with at least one "
                f"v<number>.yaml (e.g. v1.yaml)"
            )
    else:
        if version == "latest":
            version = "v1"
        template = load_from_versioned(name, version, model_name=model_name)
        if template is None:
            # Fall back to legacy mapping (no model-specific legacy path)
            template = _load_legacy_by_name(name, version)

    return _apply_prompt_variables(template, variables)


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
