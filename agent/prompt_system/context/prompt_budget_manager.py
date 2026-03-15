"""Prompt budget manager: dynamic allocation, enforce budget, emergency truncation."""

from __future__ import annotations

from dataclasses import dataclass

from agent.prompt_system.context.context_budget_manager import ContextBudget
from agent.prompt_system.context.context_pruner import prune_sections
from agent.prompt_system.context.token_counter import count_prompt_tokens
from config.agent_config import (
    MAX_PROMPT_TOKENS,
    OUTPUT_TOKEN_RESERVE,
)

_CHARS_PER_TOKEN = 4


@dataclass
class BudgetAllocation:
    """Token allocation per section (60/20/10% splits)."""

    system: int
    repo_context: int
    history: int
    skills: int
    user_input: int


@dataclass
class BudgetReport:
    """Result of enforce_budget: flags for observability."""

    pruning_triggered: bool = False
    compression_triggered: bool = False
    emergency_truncation_triggered: bool = False
    use_fallback: bool = False
    fallback_key: str = ""


class PromptBudgetManager:
    """Enforce token budget; trigger pruning, fallback, emergency truncation."""

    def allocate_budget(self, model_name: str) -> BudgetAllocation:
        """
        Dynamic allocation: available = model_window - OUTPUT_TOKEN_RESERVE.
        system=fixed, repo_context=60%, history=20%, skills=10%, user_input=remainder.
        """
        budget = ContextBudget.for_model(model_name)
        available = max(0, budget.max_tokens - OUTPUT_TOKEN_RESERVE)
        system = 2000
        remainder = available - system
        if remainder <= 0:
            return BudgetAllocation(
                system=available,
                repo_context=0,
                history=0,
                skills=0,
                user_input=0,
            )
        repo_context = int(remainder * 0.60)
        history = int(remainder * 0.20)
        skills = int(remainder * 0.10)
        user_input = remainder - repo_context - history - skills
        return BudgetAllocation(
            system=system,
            repo_context=repo_context,
            history=history,
            skills=skills,
            user_input=max(0, user_input),
        )

    def enforce_budget(
        self,
        parts: dict[str, str],
        token_counts: dict[str, int],
        model_name: str = "default",
        prompt_name: str | None = None,
    ) -> tuple[dict[str, str], BudgetReport]:
        """
        Enforce MAX_PROMPT_TOKENS. Prune sections, signal fallback if needed, emergency truncate.
        Token counts must be pre-computed by token_counter.
        """
        report = BudgetReport()
        total = token_counts.get("total", 0)
        if total <= MAX_PROMPT_TOKENS:
            return (dict(parts), report)

        report.pruning_triggered = True
        pruned = prune_sections(parts, MAX_PROMPT_TOKENS, token_counts, model_name)

        new_counts = count_prompt_tokens(
            {k: pruned.get(k, "") for k in ("system", "skills", "repo_context", "history", "user_input")},
            model_name,
        )

        if new_counts.get("total", 0) <= MAX_PROMPT_TOKENS:
            return (pruned, report)

        if prompt_name:
            report.use_fallback = True
            report.fallback_key = f"{prompt_name}_compact"

        if new_counts.get("total", 0) > MAX_PROMPT_TOKENS:
            report.emergency_truncation_triggered = True
            repo_val = pruned.get("repo_context") or ""
            other_total = new_counts.get("system", 0) + new_counts.get("skills", 0)
            other_total += new_counts.get("history", 0) + new_counts.get("user_input", 0)
            remaining_budget = max(0, MAX_PROMPT_TOKENS - other_total)
            max_repo_chars = remaining_budget * _CHARS_PER_TOKEN
            if len(repo_val) > max_repo_chars:
                pruned = dict(pruned)
                pruned["repo_context"] = repo_val[:max_repo_chars] + "\n..."

        return (pruned, report)
