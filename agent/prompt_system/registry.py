"""Registry mapping prompt_name -> prompt_file, version, model_type."""

import hashlib
import json
from pathlib import Path

import yaml

from agent.models.model_types import ModelType
from agent.prompt_system.guardrails import check_constraints, check_prompt_injection
from agent.prompt_system.loader import load_prompt
from agent.prompt_system.prompt_call_context import PromptResolution, bind_prompt_resolution
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
    # Agent V2 exploration (registry-backed prompts)
    "exploration.query_intent_parser": (ModelType.REASONING,),
    "exploration.scoper": (ModelType.REASONING,),
    "exploration.selector.single": (ModelType.REASONING,),
    "exploration.selector.batch": (ModelType.REASONING,),
    "exploration.analyzer": (ModelType.REASONING,),
    # Post-exploration answer synthesis (Agent V2)
    "answer_synthesis": (ModelType.REASONING,),
    # Planner v2 packaged (flat file + optional model override)
    "planner.decision.v1": (ModelType.REASONING,),
    "planner.replan.v1": (ModelType.REASONING,),
}


class PromptRegistry:
    """Central registry for prompts. Maps prompt_name -> prompt_file, version, model_type."""

    _instance: "PromptRegistry | None" = None

    def __init__(self) -> None:
        self._model_types: dict[str, ModelType] = {
            k: v[0] for k, v in _DEFAULT_REGISTRY.items()
        }
        self._custom: dict[str, tuple[str, str, ModelType]] = {}
        self._compiled_prompt_cache: dict[
            tuple[str, str, str | None, str],
            tuple[str, str, str, str | None],
        ] = {}
        self._compiled_prompt_cache_max: int = 256

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
        model_name: str | None = None,
    ) -> PromptTemplate:
        """Get prompt by name. Returns structured PromptTemplate."""
        return load_prompt(
            name, version=version, variables=variables or {}, model_name=model_name
        )

    def get_instructions(
        self,
        name: str,
        version: str = "latest",
        variables: dict | None = None,
        model_name: str | None = None,
    ) -> str:
        """Convenience: return instructions string only (for drop-in replacement of get_prompt)."""
        return self.get(
            name, version=version, variables=variables, model_name=model_name
        ).instructions

    @staticmethod
    def _vars_hash(variables: dict | None) -> str:
        if not variables:
            return "novars"
        try:
            payload = json.dumps(variables, sort_keys=True, default=str)
        except Exception:
            payload = str(variables)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def render_prompt_parts(
        self,
        name: str,
        *,
        version: str = "latest",
        variables: dict | None = None,
        model_name: str | None = None,
    ) -> tuple[str, str]:
        """
        Render and cache prompt parts as (system_prompt, user_prompt).
        Falls back to legacy single-body instructions as system prompt.
        """
        key = (name, version, model_name, self._vars_hash(variables))
        bind_prompt_resolution(None)
        cached = self._compiled_prompt_cache.get(key)
        if cached is not None:
            sys_p, usr_p, fv, spath = cached
            bind_prompt_resolution(PromptResolution(name, fv, spath, model_name))
            return (sys_p, usr_p)
        tmpl = self.get(name, version=version, variables=variables, model_name=model_name)
        system_prompt = (tmpl.system_prompt or tmpl.instructions or "").strip()
        user_prompt = (tmpl.user_prompt_template or "").strip()
        fv = tmpl.version
        spath = tmpl.source_path
        rendered_row = (system_prompt, user_prompt, fv, spath)
        if len(self._compiled_prompt_cache) >= self._compiled_prompt_cache_max:
            try:
                self._compiled_prompt_cache.pop(next(iter(self._compiled_prompt_cache)))
            except Exception:
                self._compiled_prompt_cache.clear()
        self._compiled_prompt_cache[key] = rendered_row
        bind_prompt_resolution(PromptResolution(name, fv, spath, model_name))
        return (system_prompt, user_prompt)

    def get_guarded(
        self,
        name: str,
        user_input: str | None = None,
        version: str = "latest",
        variables: dict | None = None,
        model_name: str | None = None,
    ) -> PromptTemplate:
        """
        Load prompt with pre-load injection guard on user_input.
        Raises PromptInjectionError if user_input contains injection patterns.
        """
        if user_input:
            check_prompt_injection(user_input)
        return self.get(
            name, version=version, variables=variables or {}, model_name=model_name
        )

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
        model_name: str | None = None,
    ) -> PromptTemplate:
        """Compose prompt + optional skill + optional repo context. Returns PromptTemplate."""
        template = self.get(
            prompt_name, version=version, variables=variables or {}, model_name=model_name
        )
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
            system_prompt=template.system_prompt,
            user_prompt_template=template.user_prompt_template,
            extra=template.extra,
            source_path=template.source_path,
        )


# Singleton access
def get_registry() -> PromptRegistry:
    return PromptRegistry.get_instance()
