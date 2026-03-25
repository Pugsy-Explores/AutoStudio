from __future__ import annotations

import json
import re
from typing import Any, Callable

from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    langfuse_generation_end_with_usage,
    langfuse_generation_input_with_prompt,
    try_langfuse_generation,
)
from agent_v2.schemas.exploration import ExplorationDecision


class UnderstandingAnalyzer:
    """Analyze snippet relevance and return ExplorationDecision."""

    def __init__(self, llm_generate: Callable[[str], str] | None = None):
        self._llm_generate = llm_generate

    def analyze(
        self,
        instruction: str,
        file_path: str,
        snippet: str,
        *,
        lf_analyze_span: Any = None,
        lf_exploration_parent: Any = None,
    ) -> ExplorationDecision:
        if self._llm_generate is None:
            return self._heuristic_decision(instruction, snippet)

        prompt = (
            "Analyze if the snippet is relevant to instruction.\n"
            "Return STRICT JSON: "
            "{\"status\":\"wrong_target|partial|sufficient\","
            "\"needs\":[],"
            "\"reason\":\"...\","
            "\"next_action\":\"expand|refine|stop\"}.\n"
            "Allowed needs: more_code, callers, callees, definition, different_symbol.\n\n"
            f"Instruction:\n{instruction}\n\nFile:\n{file_path}\n\nSnippet:\n{snippet[:6000]}"
        )
        gen = try_langfuse_generation(
            lf_analyze_span,
            lf_exploration_parent,
            name="exploration.analyze",
            input=langfuse_generation_input_with_prompt(
                prompt,
                extra={"file_path": file_path[:500]},
            ),
        )
        try:
            raw = self._llm_generate(prompt)
            parsed = self._parse_json_object(raw)
            out = ExplorationDecision.model_validate(parsed)
            if gen is not None:
                try:
                    langfuse_generation_end_with_usage(
                        gen,
                        output={"response": raw[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]},
                    )
                except Exception:
                    pass
            return out
        except Exception as e:
            if gen is not None:
                try:
                    langfuse_generation_end_with_usage(gen, output={"error": str(e)[:2000]})
                except Exception:
                    pass
            return self._heuristic_decision(instruction, snippet)

    @staticmethod
    def _heuristic_decision(instruction: str, snippet: str) -> ExplorationDecision:
        instr = instruction.lower()
        body = (snippet or "").lower()
        if not body.strip():
            return ExplorationDecision(
                status="wrong_target",
                needs=["different_symbol"],
                reason="Snippet is empty",
                next_action="refine",
            )
        keyword_hits = sum(1 for token in re.findall(r"[a-z_]{3,}", instr) if token in body)
        if keyword_hits >= 3:
            return ExplorationDecision(
                status="sufficient",
                needs=[],
                reason="Keywords match snippet",
                next_action="stop",
            )
        if keyword_hits >= 1:
            return ExplorationDecision(
                status="partial",
                needs=["callers"],
                reason="Partial keyword overlap; call context needed",
                next_action="expand",
            )
        return ExplorationDecision(
            status="wrong_target",
            needs=["different_symbol"],
            reason="Snippet does not match instruction terms",
            next_action="refine",
        )

    @staticmethod
    def _parse_json_object(text: str) -> dict:
        stripped = (text or "").strip()
        if "```" in stripped:
            match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
            if match:
                stripped = match.group(1).strip()
        data = json.loads(stripped)
        if not isinstance(data, dict):
            raise ValueError("Understanding analyzer expected JSON object")
        return data
