from __future__ import annotations

import logging
from typing import Any, Callable

from agent_v2.exploration.llm_input_normalize import normalize_analyzer
from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    exploration_llm_call,
)
from agent.prompt_system.registry import get_registry
from agent_v2.schemas.exploration import ContextBlock, UnderstandingResult
from agent_v2.utils.json_extractor import JSONExtractor

_LOG = logging.getLogger(__name__)

_EXPLORATION_ANALYZER_KEY = "exploration.analyzer"


class UnderstandingAnalyzer:
    """Semantic interpretation only — no control directives."""

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
        task_intent_summary: str = "",
        symbol_relationships_block: str = "",
        upstream_selection_confidence: str | None = None,
        lf_analyze_span: Any = None,
        lf_exploration_parent: Any = None,
    ) -> UnderstandingResult:
        _LOG.debug("[UnderstandingAnalyzer.analyze]")
        if self._llm_generate is None and self._llm_generate_messages is None:
            raise ValueError("UnderstandingAnalyzer requires llm_generate/llm_generate_messages in strict mode.")

        snippet = self._context_blocks_to_snippet(context_blocks)
        file_path = context_blocks[0].file_path if context_blocks else ""
        tis = (task_intent_summary or "").strip() or "(not provided)"
        srb = (symbol_relationships_block or "").strip()
        if not srb:
            srb = "(not provided)"
        blocks_payload = [b.model_dump() for b in context_blocks[:6]]
        exploration_llm_input = normalize_analyzer(
            instruction=instruction,
            intent=intent,
            task_intent_summary=tis,
            file_path=file_path,
            snippet=snippet,
            symbol_relationships_block=srb,
            context_blocks=blocks_payload,
            upstream_selection_confidence=upstream_selection_confidence,
        )
        system_prompt, user_prompt = get_registry().render_prompt_parts(
            _EXPLORATION_ANALYZER_KEY,
            model_name=self._model_name,
            variables={
                "exploration_llm_input": exploration_llm_input,
            },
        )
        prompt = (
            f"[SYSTEM]\n{system_prompt}\n\n---\n\n[USER]\n{user_prompt}".strip()
            if user_prompt.strip()
            else system_prompt
        )

        def _invoke() -> str:
            if self._llm_generate_messages is not None and user_prompt.strip():
                return self._llm_generate_messages(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ]
                )
            if self._llm_generate_messages is not None:
                return self._llm_generate_messages([{"role": "system", "content": system_prompt}])
            assert self._llm_generate is not None
            return self._llm_generate(prompt)

        parsed_out: list[UnderstandingResult] = []

        def _complete(raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
            parsed = JSONExtractor.extract_final_json(raw)
            out = self._coerce_understanding(parsed)
            parsed_out.append(out)
            return (
                {"response": raw[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]},
                {"stage": "analyze", "ok": True},
            )

        exploration_llm_call(
            lf_analyze_span,
            lf_exploration_parent,
            name="exploration.analyze",
            prompt=prompt,
            prompt_registry_key=_EXPLORATION_ANALYZER_KEY,
            invoke=_invoke,
            stage="analyze",
            model_name=self._model_name,
            input_extra={"file_path": file_path[:500]},
            on_complete=_complete,
        )
        return parsed_out[0]

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
    def _confidence_label_to_float(label: str) -> float:
        m = {"high": 0.85, "medium": 0.6, "low": 0.35}
        return m.get(label, 0.6)

    @staticmethod
    def _gaps_relevant_to_intent_from_parsed(parsed: dict) -> list[str]:
        gr = parsed.get("gaps_relevant_to_intent")
        if not isinstance(gr, list):
            return []
        return [str(x).strip() for x in gr if str(x).strip()]

    @classmethod
    def _coerce_understanding(cls, parsed: dict) -> UnderstandingResult:
        if not isinstance(parsed, dict):
            return UnderstandingResult(
                relevance="medium",
                confidence=0.3,
                sufficient=False,
                evidence_sufficiency="insufficient",
                knowledge_gaps=["Analyzer returned non-object JSON"],
                summary="Analyzer output was invalid JSON object.",
                semantic_understanding="",
                is_sufficient=False,
                confidence_label="low",
                gaps_relevant_to_intent=[],
            )

        # New contract: understanding, relevant_files, relationships, confidence, gaps, is_sufficient
        if "is_sufficient" in parsed or "semantic_understanding" in parsed or (
            "understanding" in parsed and isinstance(parsed.get("understanding"), str)
            and "relevant_files" in parsed
        ):
            return cls._coerce_modern(parsed)

        if any(k in parsed for k in ("understanding", "key_findings", "gaps")):
            relevance = str(parsed.get("relevance") or "medium").strip().lower()
            if relevance not in ("high", "medium", "low"):
                relevance = "medium"
            confidence_raw = str(parsed.get("confidence") or "medium").strip().lower()
            confidence_map = {"high": 0.85, "medium": 0.6, "low": 0.35}
            confidence = confidence_map.get(confidence_raw, 0.6)
            cl = confidence_raw if confidence_raw in ("high", "medium", "low") else "medium"
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
            is_suf = bool(sufficient or evidence_sufficiency == "sufficient")
            return UnderstandingResult(
                relevance=relevance,
                confidence=confidence,
                sufficient=sufficient,
                evidence_sufficiency=evidence_sufficiency,
                knowledge_gaps=gaps,
                summary=summary,
                semantic_understanding=summary,
                confidence_label=cl,  # type: ignore[arg-type]
                is_sufficient=is_suf,
                gaps_relevant_to_intent=cls._gaps_relevant_to_intent_from_parsed(parsed),
            )

        # Legacy status-shaped JSON: map to semantic fields only (no control leakage to engine).
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
                    semantic_understanding=reason or "Sufficient evidence identified.",
                    confidence_label="high",
                    is_sufficient=True,
                    gaps_relevant_to_intent=[],
                )
            if status == "wrong_target":
                return UnderstandingResult(
                    relevance="low",
                    confidence=0.7,
                    sufficient=False,
                    evidence_sufficiency="insufficient",
                    knowledge_gaps=["Current target appears irrelevant."],
                    summary=reason or "Low relevance for current target.",
                    semantic_understanding=reason or "Low relevance for current target.",
                    confidence_label="medium",
                    is_sufficient=False,
                    gaps_relevant_to_intent=[],
                )
            return UnderstandingResult(
                relevance="medium",
                confidence=0.6,
                sufficient=False,
                evidence_sufficiency="partial",
                knowledge_gaps=["Need additional evidence."],
                summary=reason or "Partial understanding from current evidence.",
                semantic_understanding=reason or "Partial understanding from current evidence.",
                confidence_label="medium",
                is_sufficient=False,
                gaps_relevant_to_intent=[],
            )

        clean = dict(parsed)
        clean.pop("next_action", None)
        clean.pop("needs", None)
        clean.pop("status", None)
        return UnderstandingResult.model_validate(clean)

    @classmethod
    def _coerce_modern(cls, parsed: dict) -> UnderstandingResult:
        raw_under = parsed.get("semantic_understanding")
        if raw_under is None:
            raw_under = parsed.get("understanding")
        semantic_understanding = str(raw_under or "").strip()
        rel_files = parsed.get("relevant_files")
        if not isinstance(rel_files, list):
            rel_files = []
        rel_files = [str(x).strip() for x in rel_files if str(x).strip()]
        rels = parsed.get("relationships")
        if not isinstance(rels, list):
            rels = []
        rels = [str(x).strip() for x in rels if str(x).strip()]
        conf_l = str(parsed.get("confidence") or "medium").strip().lower()
        if conf_l not in ("high", "medium", "low"):
            conf_l = "medium"
        gaps = parsed.get("gaps")
        if not isinstance(gaps, list):
            gaps = []
        gaps = [str(x).strip() for x in gaps if str(x).strip()]
        is_sufficient = bool(parsed.get("is_sufficient", False))
        summary = semantic_understanding or (" | ".join(gaps[:2]) if gaps else "Context analyzed.")
        cf = cls._confidence_label_to_float(conf_l)
        rel = "high" if conf_l == "high" else ("low" if conf_l == "low" else "medium")
        ev = "sufficient" if is_sufficient else ("insufficient" if not gaps else "partial")
        return UnderstandingResult(
            relevance=rel,
            confidence=cf,
            sufficient=is_sufficient,
            evidence_sufficiency=ev,  # type: ignore[arg-type]
            knowledge_gaps=gaps,
            summary=summary,
            semantic_understanding=semantic_understanding or summary,
            relevant_files=rel_files,
            relationship_strings=rels,
            confidence_label=conf_l,  # type: ignore[arg-type]
            is_sufficient=is_sufficient,
            gaps_relevant_to_intent=cls._gaps_relevant_to_intent_from_parsed(parsed),
        )
