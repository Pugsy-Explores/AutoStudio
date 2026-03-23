"""Registry mapping prompt_name -> prompt_file, version, model_type."""

from pathlib import Path

import yaml

from agent.models.model_types import ModelType
from agent.prompt_system.guardrails import check_constraints, check_prompt_injection
from agent.prompt_system.loader import load_prompt
from agent.prompt_system.prompt_context_builder import build_context
from agent.prompt_system.prompt_template import PromptTemplate

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# Default mapping: prompt_name -> (model_type,)
_DEFAULT_REGISTRY: dict[str, tuple[ModelType, ...]] = {
    "planner": (ModelType.REASONING,),
    "router": (ModelType.SMALL,),
    "critic": (ModelType.SMALL,),
    "retry_planner": (ModelType.REASONING,),
    "replanner": (ModelType.REASONING,),
    "query_rewrite": (ModelType.SMALL,),
    "query_rewrite_with_context": (ModelType.REASONING,),
    "query_rewrite_system": (ModelType.SMALL,),
    "validate_step": (ModelType.SMALL,),
    "router_logit": (ModelType.SMALL,),
    # Phase 13 extracted prompts
    "explain_system": (ModelType.REASONING,),
    "instruction_router": (ModelType.SMALL,),
    "action_selector": (ModelType.SMALL,),
    "context_ranker_single": (ModelType.REASONING,),
    "context_ranker_batch": (ModelType.REASONING,),
    "replanner_user": (ModelType.REASONING,),
    # Phase 15: query expansion, context interpreter, patch generator
    "query_expansion": (ModelType.SMALL,),
    "context_interpreter": (ModelType.REASONING,),
    "patch_generator": (ModelType.REASONING,),
    "bundle_selector": (ModelType.SMALL,),
    "edit_proposal_system": (ModelType.REASONING,),
    "edit_proposal_user": (ModelType.REASONING,),
    "retry_planner_user": (ModelType.REASONING,),
    "react_action": (ModelType.REASONING,),
}


class PromptRegistry:
    """Central registry for prompts. Maps prompt_name -> prompt_file, version, model_type."""

    _instance: "PromptRegistry | None" = None

    def __init__(self) -> None:
        self._model_types: dict[str, ModelType] = {
            k: v[0] for k, v in _DEFAULT_REGISTRY.items()
        }
        self._custom: dict[str, tuple[str, str, ModelType]] = {}

    @classmethod
    def get_instance(cls) -> "PromptRegistry":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(
        self,
        name: str,
        version: str = "latest",
        variables: dict | None = None,
    ) -> PromptTemplate:
        """Get prompt by name. Returns structured PromptTemplate."""
        return load_prompt(name, version=version, variables=variables or {})

    def get_instructions(
        self,
        name: str,
        version: str = "latest",
        variables: dict | None = None,
    ) -> str:
        """Convenience: return instructions string only (for drop-in replacement of get_prompt)."""
        return self.get(name, version=version, variables=variables).instructions

    def get_guarded(
        self,
        name: str,
        user_input: str | None = None,
        version: str = "latest",
        variables: dict | None = None,
    ) -> PromptTemplate:
        """
        Load prompt with pre-load injection guard on user_input.
        Raises PromptInjectionError if user_input contains injection patterns.
        """
        if user_input:
            check_prompt_injection(user_input)
        return self.get(name, version=version, variables=variables or {})

    def validate_response(
        self,
        name: str,
        response: str,
        user_input: str | None = None,
        *,
        relax_actions: bool = False,
    ) -> tuple[bool, str]:
        """
        Validate LLM response against template constraints (injection, output_schema, safety).
        Returns (is_valid, error_message).
        relax_actions: When True (planner-only recovery), skip action validation in safety check.
        """
        template = self.get(name)
        return check_constraints(user_input, response, template, relax_actions=relax_actions)

    def get_model_type(self, name: str) -> ModelType:
        """Return which model type this prompt expects."""
        return self._model_types.get(name, ModelType.REASONING)

    def register(
        self,
        name: str,
        file_stem: str,
        version: str = "v1",
        model_type: ModelType = ModelType.REASONING,
    ) -> None:
        """Register a custom prompt for dynamic use."""
        self._custom[name] = (file_stem, version, model_type)
        self._model_types[name] = model_type

    def get_skill(self, skill_name: str) -> dict:
        """Load skill YAML by name. Returns dict with goal, tools_allowed, output_format, constraints."""
        path = _SKILLS_DIR / f"{skill_name}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Skill not found: {path}")
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data

    def compose(
        self,
        prompt_name: str,
        skill_name: str | None = None,
        repo_context: str | None = None,
        version: str = "latest",
        variables: dict | None = None,
    ) -> PromptTemplate:
        """Compose prompt + optional skill + optional repo context. Returns PromptTemplate."""
        template = self.get(prompt_name, version=version, variables=variables or {})
        skill_block: str | None = None
        if skill_name:
            skill = self.get_skill(skill_name)
            parts = [f"Skill: {skill.get('goal', '')}"]
            if skill.get("tools_allowed"):
                parts.append(f"Tools allowed: {', '.join(skill['tools_allowed'])}")
            if skill.get("output_format"):
                parts.append(f"Output format: {skill['output_format']}")
            if skill.get("constraints"):
                parts.append("Constraints: " + "; ".join(skill["constraints"]))
            skill_block = "\n".join(parts)
        composed = build_context(
            template.instructions,
            skill_block=skill_block,
            repo_context=repo_context,
        )
        return PromptTemplate(
            name=template.name,
            version=template.version,
            role=template.role,
            instructions=composed,
            constraints=template.constraints,
            output_schema=template.output_schema,
            extra=template.extra,
        )


# Singleton access
def get_registry() -> PromptRegistry:
    return PromptRegistry.get_instance()
