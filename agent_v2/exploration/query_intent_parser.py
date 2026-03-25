from __future__ import annotations

import json
import re
from typing import Callable

from agent_v2.schemas.exploration import QueryIntent


class QueryIntentParser:
    """Parse instruction into a minimal QueryIntent."""

    def __init__(self, llm_generate: Callable[[str], str] | None = None):
        self._llm_generate = llm_generate

    def parse(self, instruction: str) -> QueryIntent:
        if self._llm_generate is None:
            return self._heuristic_parse(instruction)

        prompt = (
            "You are extracting search intent from a coding task.\n"
            "Return STRICT JSON only with keys symbols, keywords, intents.\n"
            "Allowed intents: find_definition, find_usage, debug, understand_flow, locate_logic.\n"
            "Do not include extra keys.\n\n"
            f"Instruction:\n{instruction}"
        )
        try:
            raw = self._llm_generate(prompt)
            data = self._parse_json_object(raw)
            return QueryIntent.model_validate(data)
        except Exception:
            return self._heuristic_parse(instruction)

    def _heuristic_parse(self, instruction: str) -> QueryIntent:
        symbol_candidates = re.findall(r"\b[A-Z][A-Za-z0-9_]{2,}\b", instruction)
        symbols = list(dict.fromkeys(symbol_candidates[:5]))

        keyword_candidates = re.findall(r"\b[a-z][a-z0-9_]{2,}\b", instruction.lower())
        stop = {
            "the",
            "and",
            "for",
            "with",
            "where",
            "what",
            "when",
            "into",
            "from",
            "this",
            "that",
            "find",
            "show",
        }
        keywords = [k for k in keyword_candidates if k not in stop][:8]

        intents: list[str] = []
        lowered = instruction.lower()
        if "where" in lowered or "definition" in lowered:
            intents.append("find_definition")
        if "usage" in lowered or "used" in lowered:
            intents.append("find_usage")
        if "debug" in lowered or "error" in lowered or "fail" in lowered:
            intents.append("debug")
        if "flow" in lowered or "how" in lowered:
            intents.append("understand_flow")
        if not intents:
            intents.append("locate_logic")

        return QueryIntent(symbols=symbols, keywords=keywords, intents=intents)

    @staticmethod
    def _parse_json_object(text: str) -> dict:
        stripped = (text or "").strip()
        if "```" in stripped:
            match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
            if match:
                stripped = match.group(1).strip()
        data = json.loads(stripped)
        if not isinstance(data, dict):
            raise ValueError("Intent parser expected a JSON object")
        return data
