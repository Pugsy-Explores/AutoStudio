from __future__ import annotations

import json
from typing import Any, Callable

from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    langfuse_generation_end_with_usage,
    langfuse_generation_input_with_prompt,
    try_langfuse_generation,
)
from agent.prompt_system.registry import get_registry
from agent_v2.schemas.exploration import ContextBlock, UnderstandingResult
from agent_v2.utils.json_extractor import JSONExtractor

_EXPLORATION_ANALYZER_KEY = "exploration.analyzer"


class UnderstandingAnalyzer:
    """Interpret context and return understanding without control directives."""

    def __init__(
        self,
        llm_generate: Callable[[str], str] | None = None,
        llm_generate_messages: Callable[[list[dict[str, str]]], str] | None = None,
        *,
        model_name: str | None = None,
    ):
        self._llm_generate = llm_generate
        self._llm_generate_messages = llm_generate_messages
        self._model_name = model_name

    def analyze(
        self,
        instruction: str,
        *,
        intent: str,
        context_blocks: list[ContextBlock],
        lf_analyze_span: Any = None,
        lf_exploration_parent: Any = None,
    ) -> UnderstandingResult:
        if self._llm_generate is None and self._llm_generate_messages is None:
            raise ValueError("UnderstandingAnalyzer requires llm_generate/llm_generate_messages in strict mode.")

        snippet = self._context_blocks_to_snippet(context_blocks)
        file_path = context_blocks[0].file_path if context_blocks else ""
        system_prompt, user_prompt = get_registry().render_prompt_parts(
            _EXPLORATION_ANALYZER_KEY,
            model_name=self._model_name,
            variables={
                "instruction": instruction,
                "file_path": file_path,
                "snippet": snippet[:6000],
                "intent": intent,
                "context_blocks": self._context_blocks_json(context_blocks),
            },
        )
        prompt = (
            f"[SYSTEM]\n{system_prompt}\n\n---\n\n[USER]\n{user_prompt}".strip()
            if user_prompt.strip()
            else system_prompt
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
            if self._llm_generate_messages is not None and user_prompt.strip():
                raw = self._llm_generate_messages(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ]
                )
            elif self._llm_generate_messages is not None:
                raw = self._llm_generate_messages([{"role": "system", "content": system_prompt}])
            else:
                raw = self._llm_generate(prompt)
            parsed = JSONExtractor.extract_final_json(raw)
            out = self._coerce_understanding(parsed)
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
                except Exception as e2:
                    raise Exception(f"Fatal error: Failed to end langfuse generation: {e2}") from e2
            raise

    @staticmethod
    def _context_blocks_json(context_blocks: list[ContextBlock]) -> str:
        try:
            payload = [b.model_dump() for b in context_blocks][:6]
            return json.dumps(payload, ensure_ascii=False)
        except Exception:
            return "[]"

    @staticmethod
    def _context_blocks_to_snippet(context_blocks: list[ContextBlock]) -> str:
        if not context_blocks:
            return ""
        chunks: list[str] = []
        for b in context_blocks[:6]:
            chunks.append(
                f"{b.file_path}:{b.start}-{b.end}\n{b.content}".strip()
            )
        return "\n\n".join(chunks)

    @staticmethod
    def _coerce_understanding(parsed: dict) -> UnderstandingResult:
        if not isinstance(parsed, dict):
            return UnderstandingResult(
                relevance="medium",
                confidence=0.3,
                sufficient=False,
                evidence_sufficiency="insufficient",
                knowledge_gaps=["Analyzer returned non-object JSON"],
                summary="Analyzer output was invalid JSON object.",
            )
        if any(k in parsed for k in ("understanding", "key_findings", "gaps")):
            relevance = str(parsed.get("relevance") or "medium").strip().lower()
            if relevance not in ("high", "medium", "low"):
                relevance = "medium"
            confidence_raw = str(parsed.get("confidence") or "medium").strip().lower()
            confidence_map = {"high": 0.85, "medium": 0.6, "low": 0.35}
            confidence = confidence_map.get(confidence_raw, 0.6)
            understanding = str(parsed.get("understanding") or "partial").strip().lower()
            if understanding == "sufficient":
                sufficient = True
                evidence_sufficiency = "sufficient"
            elif understanding == "insufficient":
                sufficient = False
                evidence_sufficiency = "insufficient"
            else:
                sufficient = False
                evidence_sufficiency = "partial"
            key_findings = parsed.get("key_findings")
            if not isinstance(key_findings, list):
                key_findings = []
            key_findings = [str(x).strip() for x in key_findings if str(x).strip()]
            gaps = parsed.get("gaps")
            if not isinstance(gaps, list):
                gaps = []
            gaps = [str(x).strip() for x in gaps if str(x).strip()]
            summary = " | ".join(key_findings[:2]) if key_findings else "Context analyzed."
            return UnderstandingResult(
                relevance=relevance,
                confidence=confidence,
                sufficient=sufficient,
                evidence_sufficiency=evidence_sufficiency,
                knowledge_gaps=gaps,
                summary=summary,
            )
        if any(k in parsed for k in ("status", "needs", "next_action")):
            status = str(parsed.get("status") or "partial")
            reason = str(parsed.get("reason") or "").strip()
            if status == "sufficient":
                return UnderstandingResult(
                    relevance="high",
                    confidence=0.8,
                    sufficient=True,
                    evidence_sufficiency="sufficient",
                    knowledge_gaps=[],
                    summary=reason or "Sufficient evidence identified.",
                )
            if status == "wrong_target":
                return UnderstandingResult(
                    relevance="low",
                    confidence=0.7,
                    sufficient=False,
                    evidence_sufficiency="insufficient",
                    knowledge_gaps=["Current target appears irrelevant."],
                    summary=reason or "Low relevance for current target.",
                )
            return UnderstandingResult(
                relevance="medium",
                confidence=0.6,
                sufficient=False,
                evidence_sufficiency="partial",
                knowledge_gaps=["Need additional evidence."],
                summary=reason or "Partial understanding from current evidence.",
            )
        clean = dict(parsed)
        clean.pop("next_action", None)
        clean.pop("needs", None)
        clean.pop("status", None)
        return UnderstandingResult.model_validate(clean)
