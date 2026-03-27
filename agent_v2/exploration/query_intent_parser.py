from __future__ import annotations

import json
from typing import Any, Callable

from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    exploration_llm_call,
)
from agent.prompt_system.registry import get_registry
from agent_v2.schemas.exploration import FailureReason, QueryIntent
from agent_v2.utils.json_extractor import JSONExtractor

_EXPLORATION_QUERY_INTENT_KEY = "exploration.query_intent_parser"


def _coerce_for_query_intent(data: dict) -> dict:
    """
    Map common LLM key aliases onto QueryIntent field names before validation.

    Prompt may emit symbol_queries / text_queries / intent; schema expects
    symbols / keywords / intents.
    """
    out = dict(data)
    if not (out.get("symbols") or []):
        sq = out.pop("symbol_queries", None)
        if isinstance(sq, list):
            out["symbols"] = [str(x).strip() for x in sq if str(x).strip()]
    if not (out.get("keywords") or []):
        tq = out.pop("text_queries", None)
        if isinstance(tq, list):
            out["keywords"] = [str(x).strip() for x in tq if str(x).strip()]
    if not (out.get("intents") or []):
        one = out.pop("intent", None)
        if isinstance(one, str) and one.strip():
            out["intents"] = [one.strip()]
    # Drop alias keys if still present so model_validate does not see unknowns
    out.pop("symbol_queries", None)
    out.pop("text_queries", None)
    out.pop("intent", None)
    rp = out.get("regex_patterns")
    if rp is not None and not isinstance(rp, list):
        out["regex_patterns"] = []
    return out


class QueryIntentParser:
    """Parse instruction into a minimal QueryIntent."""

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

    def parse(
        self,
        instruction: str,
        *,
        previous_queries: QueryIntent | dict[str, Any] | None = None,
        failure_reason: FailureReason | str | None = None,
        context_feedback: dict[str, Any] | None = None,
        lf_exploration_parent: Any = None,
        lf_intent_span: Any = None,
    ) -> QueryIntent:
        """
        Parse instruction into ``QueryIntent``.

        When Langfuse is available, pass ``lf_intent_span`` (preferred) and/or
        ``lf_exploration_parent`` so the LLM call is recorded as generation
        ``exploration.query_intent`` with full prompt input and structured output.
        """
        if self._llm_generate is None and self._llm_generate_messages is None:
            raise ValueError("QueryIntentParser requires llm_generate/llm_generate_messages in strict mode.")

        previous_payload: dict[str, Any] | None = None
        if previous_queries is not None:
            if isinstance(previous_queries, QueryIntent):
                previous_payload = previous_queries.model_dump()
            elif isinstance(previous_queries, dict):
                previous_payload = dict(previous_queries)
        previous_json = (
            json.dumps(previous_payload, ensure_ascii=False, sort_keys=True)
            if previous_payload
            else "no queries"
        )
        fr = (str(failure_reason).strip() if failure_reason else "no failure")
        context_feedback_json = (
            json.dumps(context_feedback, ensure_ascii=False, sort_keys=True)
            if context_feedback
            else "none"
        )
        partial_findings_count = 0
        known_symbols_count = 0
        known_files_count = 0
        knowledge_gaps_count = 0
        relationships_count = 0
        if isinstance(context_feedback, dict):
            pf = context_feedback.get("partial_findings")
            if isinstance(pf, list):
                partial_findings_count = len(pf)
            ke = context_feedback.get("known_entities")
            if isinstance(ke, dict):
                ks = ke.get("symbols")
                if isinstance(ks, list):
                    known_symbols_count = len(ks)
                kf = ke.get("files")
                if isinstance(kf, list):
                    known_files_count = len(kf)
            kg = context_feedback.get("knowledge_gaps")
            if isinstance(kg, list):
                knowledge_gaps_count = len(kg)
            rel = context_feedback.get("relationships")
            if isinstance(rel, list):
                relationships_count = len(rel)
        system_prompt, user_prompt = get_registry().render_prompt_parts(
            _EXPLORATION_QUERY_INTENT_KEY,
            model_name=self._model_name,
            variables={
                "instruction": instruction,
                "previous_queries": previous_json,
                "failure_reason": fr,
                "context_feedback": context_feedback_json,
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

        parsed: list[QueryIntent] = []

        def _parse_complete(raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
            try:
                data = JSONExtractor.extract_final_json(raw)
                data = _coerce_for_query_intent(data)
                qi = QueryIntent.model_validate(data)
            except Exception as exc:
                raise Exception("Fatal error: Failed to parse intent JSON output.") from exc
            if previous_payload:
                qi = self._remove_repeated_queries(qi, previous_payload)
            parsed.append(qi)
            return (
                {
                    "query_intent": qi.model_dump(),
                    "raw_response_preview": (raw or "")[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS],
                },
                {"stage": "query_intent", "ok": True},
            )

        exploration_llm_call(
            lf_intent_span,
            lf_exploration_parent,
            name="exploration.query_intent",
            prompt=prompt,
            prompt_registry_key=_EXPLORATION_QUERY_INTENT_KEY,
            invoke=_invoke,
            stage="query_intent",
            model_name=self._model_name,
            input_extra={
                "instruction_preview": instruction[:2000],
                "instruction_chars": len(instruction),
                "context_feedback_present": bool(context_feedback),
                "partial_findings_count": partial_findings_count,
                "known_symbols_count": known_symbols_count,
                "known_files_count": known_files_count,
                "knowledge_gaps_count": knowledge_gaps_count,
                "relationships_count": relationships_count,
                "context_feedback_preview": context_feedback_json[:2000],
            },
            on_complete=_parse_complete,
        )
        return parsed[0]

    @staticmethod
    def _remove_repeated_queries(
        parsed: QueryIntent, previous_payload: dict[str, Any]
    ) -> QueryIntent:
        prev = _coerce_for_query_intent(previous_payload or {})
        seen_symbols = {str(x).strip() for x in (prev.get("symbols") or []) if str(x).strip()}
        seen_keywords = {str(x).strip() for x in (prev.get("keywords") or []) if str(x).strip()}
        seen_regex = {str(x).strip() for x in (prev.get("regex_patterns") or []) if str(x).strip()}
        seen_intents = {str(x).strip() for x in (prev.get("intents") or []) if str(x).strip()}
        return QueryIntent(
            symbols=[x for x in parsed.symbols if x.strip() and x.strip() not in seen_symbols],
            keywords=[x for x in parsed.keywords if x.strip() and x.strip() not in seen_keywords],
            regex_patterns=[
                x for x in parsed.regex_patterns if x.strip() and x.strip() not in seen_regex
            ],
            intents=[x for x in parsed.intents if x.strip() and x.strip() not in seen_intents],
        )
