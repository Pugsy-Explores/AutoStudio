"""Centralized prompts: compatibility shim redirecting to PromptRegistry."""

# Legacy name -> registry name mapping
_LEGACY_TO_REGISTRY = {
    "planner_system": "planner",
    "model_router": "router",
    "critic_system": "critic",
    "retry_planner_system": "retry_planner",
    "replanner_system": "replanner",
    "query_rewrite": "query_rewrite",
    "query_rewrite_with_context": "query_rewrite_with_context",
    "validate_step": "validate_step",
    "router_logit_system": "router_logit",
}


def get_prompt(name: str, key: str | None = None) -> str | dict:
    """
    Compatibility shim: redirect to PromptRegistry.
    name: legacy file stem (e.g. 'planner_system', 'query_rewrite_with_context').
    key: optional key; if given, return that key's value (str); else return full dict.
    """
    from agent.prompt_system import get_registry

    reg_name = _LEGACY_TO_REGISTRY.get(name, name)
    template = get_registry().get(reg_name)
    if key is not None:
        if key in ("system_prompt", "prompt"):
            return template.instructions
        if template.extra and key in template.extra:
            return template.extra[key]
        return ""
    if template.extra:
        return dict(template.extra)
    return {"system_prompt": template.instructions}
