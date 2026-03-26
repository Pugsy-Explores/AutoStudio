from __future__ import annotations

import json
from typing import Any, Callable

from agent_v2.config import EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K, EXPLORATION_SELECTOR_TOP_K
from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    exploration_llm_call,
)
from agent.prompt_system.registry import get_registry
from agent_v2.schemas.exploration import ExplorationCandidate
from agent_v2.utils.json_extractor import JSONExtractor

_EXPLORATION_SELECTOR_SINGLE_KEY = "exploration.selector.single"
_EXPLORATION_SELECTOR_BATCH_KEY = "exploration.selector.batch"


class CandidateSelector:
    """Select exploration candidates in a single ranking pass."""

    def __init__(
        self,
        llm_generate: Callable[[str], str] | None = None,
        llm_generate_messages: Callable[[list[dict[str, str]]], str] | None = None,
        *,
        model_name: str | None = None,
        llm_generate_single: Callable[[str], str] | None = None,
        llm_generate_batch: Callable[[str], str] | None = None,
        llm_generate_messages_single: Callable[[list[dict[str, str]]], str] | None = None,
        llm_generate_messages_batch: Callable[[list[dict[str, str]]], str] | None = None,
        model_name_single: str | None = None,
        model_name_batch: str | None = None,
    ):
        self._llm_generate = llm_generate
        self._llm_generate_messages = llm_generate_messages
        self._model_name = model_name
        # Backward-compatible defaults: if stage-specific callables are not passed,
        # reuse legacy shared llm/model wiring.
        self._llm_generate_single = llm_generate_single or llm_generate
        self._llm_generate_batch = llm_generate_batch or llm_generate
        self._llm_generate_messages_single = llm_generate_messages_single or llm_generate_messages
        self._llm_generate_messages_batch = llm_generate_messages_batch or llm_generate_messages
        self._model_name_single = model_name_single if model_name_single is not None else model_name
        self._model_name_batch = model_name_batch if model_name_batch is not None else model_name

    def select(
        self,
        instruction: str,
        candidates: list[ExplorationCandidate],
        seen_files: set[str],
    ) -> ExplorationCandidate | None:
        if not candidates:
            return None
        top = candidates[:EXPLORATION_SELECTOR_TOP_K]

        if self._llm_generate_single is None and self._llm_generate_messages_single is None:
            raise ValueError("CandidateSelector.select requires single LLM callable in strict mode.")

        payload = [
            {
                "file_path": c.file_path,
                "symbol": c.symbol,
                "source": c.source,
            }
            for c in top
        ]
        system_prompt, user_prompt = get_registry().render_prompt_parts(
            _EXPLORATION_SELECTOR_SINGLE_KEY,
            model_name=self._model_name_single,
            variables={
                "instruction": instruction,
                "candidates_json": json.dumps(payload),
            },
        )
        prompt = (
            f"[SYSTEM]\n{system_prompt}\n\n---\n\n[USER]\n{user_prompt}".strip()
            if user_prompt.strip()
            else system_prompt
        )

        def _invoke_single() -> str:
            if self._llm_generate_messages_single is not None and user_prompt.strip():
                return self._llm_generate_messages_single(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ]
                )
            if self._llm_generate_messages_single is not None:
                return self._llm_generate_messages_single(
                    [{"role": "system", "content": system_prompt}]
                )
            assert self._llm_generate_single is not None
            return self._llm_generate_single(prompt)

        holder: list[dict] = []

        def _complete_single(raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
            choice = JSONExtractor.extract_final_json(raw)
            holder.append(choice)
            return (
                {"response": raw[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]},
                {"stage": "select", "ok": True, "select_mode": "single"},
            )

        exploration_llm_call(
            lf_exploration_parent,
            name="exploration.select_single",
            prompt=prompt,
            prompt_registry_key=_EXPLORATION_SELECTOR_SINGLE_KEY,
            invoke=_invoke_single,
            stage="select",
            model_name=self._model_name_single,
            input_extra={"candidates_in": len(top), "select_mode": "single"},
            on_complete=_complete_single,
        )
        choice = holder[0]
        selected = self._match(choice, top)
        if selected is None:
            raise ValueError("CandidateSelector.select could not match parsed JSON choice to candidates.")
        return selected

    def select_batch(
        self,
        instruction: str,
        intent: str,
        candidates: list[ExplorationCandidate],
        seen_files: set[str],
        *,
        limit: int,
        explored_location_keys: set[tuple[str, str]] | None = None,
        lf_select_span: Any = None,
        lf_exploration_parent: Any = None,
    ) -> list[ExplorationCandidate] | None:
        if not candidates or limit <= 0:
            return []
        top = candidates[:EXPLORATION_SELECTOR_TOP_K]
        if self._llm_generate_batch is None and self._llm_generate_messages_batch is None:
            raise ValueError("CandidateSelector.select_batch requires batch LLM callable in strict mode.")

        payload = [
            {
                "file_path": c.file_path,
                "symbol": c.symbol,
                "source": c.source,
            }
            for c in top
        ]
        explored_block = ""
        if explored_location_keys:
            rows = [
                {"file_path": fp, "symbol": sym or ""}
                for fp, sym in sorted(explored_location_keys, key=lambda t: (t[0], t[1]))[
                    :EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K
                ]
            ]
            explored_block = (
                "\nLocations already inspected in this run (choose different file/symbol pairs "
                "unless no alternative exists):\n"
                f"{json.dumps(rows, ensure_ascii=False)}\n"
            )
        system_prompt, user_prompt = get_registry().render_prompt_parts(
            _EXPLORATION_SELECTOR_BATCH_KEY,
            model_name=self._model_name_batch,
            variables={
                "instruction": instruction,
                "intent": intent or "no intent",
                "explored_block": explored_block,
                "candidates_json": json.dumps(payload, ensure_ascii=False),
                "limit": limit,
            },
        )
        prompt = (
            f"[SYSTEM]\n{system_prompt}\n\n---\n\n[USER]\n{user_prompt}".strip()
            if user_prompt.strip()
            else system_prompt
        )

        holder: dict[str, Any] = {}

        def _invoke_batch() -> str:
            if self._llm_generate_messages_batch is not None and user_prompt.strip():
                return self._llm_generate_messages_batch(
                    [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ]
                )
            if self._llm_generate_messages_batch is not None:
                return self._llm_generate_messages_batch(
                    [{"role": "system", "content": system_prompt}]
                )
            assert self._llm_generate_batch is not None
            return self._llm_generate_batch(prompt)

        def _complete_batch(raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
            parsed = JSONExtractor.extract_final_json(raw)
            holder["parsed"] = parsed
            holder["raw"] = raw
            if bool(parsed.get("no_relevant_candidate")):
                return (
                    {
                        "no_relevant_candidate": True,
                        "response": raw[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS],
                    },
                    {"stage": "select", "ok": True, "select_mode": "batch"},
                )
            return (
                {"response": raw[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]},
                {"stage": "select", "ok": True, "select_mode": "batch"},
            )

        exploration_llm_call(
            lf_select_span,
            lf_exploration_parent,
            name="exploration.select",
            prompt=prompt,
            prompt_registry_key=_EXPLORATION_SELECTOR_BATCH_KEY,
            invoke=_invoke_batch,
            stage="select",
            model_name=self._model_name_batch,
            input_extra={
                "candidates_in": len(top),
                "limit": limit,
                "select_mode": "batch",
            },
            on_complete=_complete_batch,
        )
        parsed = holder["parsed"]
        raw = holder["raw"]
        if bool(parsed.get("no_relevant_candidate")):
            return None
        selected_indices_raw = parsed.get("selected_indices")
        if isinstance(selected_indices_raw, list):
            selected_raw = []
            for idx in selected_indices_raw:
                try:
                    i = int(idx)
                except (TypeError, ValueError):
                    continue
                if 0 <= i < len(top):
                    c = top[i]
                    selected_raw.append({"file_path": c.file_path, "symbol": c.symbol})
        else:
            selected_raw = parsed.get("selected")
        if not isinstance(selected_raw, list):
            raise ValueError(
                "CandidateSelector.select_batch expected `selected_indices` or `selected` list in parsed JSON."
            )
        ranked = self._match_many(selected_raw, top, limit=limit)
        if not ranked:
            raise ValueError(
                "CandidateSelector.select_batch parsed JSON but found no matchable selections."
            )
        return ranked

    @staticmethod
    def _match(choice: dict, candidates: list[ExplorationCandidate]) -> ExplorationCandidate | None:
        file_path = str(choice.get("file_path") or "")
        symbol = choice.get("symbol")
        for c in candidates:
            if c.file_path == file_path and (symbol is None or c.symbol == symbol):
                return c
        for c in candidates:
            if c.file_path == file_path:
                return c
        return None

    @classmethod
    def _match_many(
        cls,
        choices: list[dict],
        candidates: list[ExplorationCandidate],
        *,
        limit: int,
    ) -> list[ExplorationCandidate]:
        picked: list[ExplorationCandidate] = []
        seen: set[tuple[str, str]] = set()
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            selected = cls._match(choice, candidates)
            if selected is None:
                continue
            key = (selected.file_path, selected.symbol or "")
            if key in seen:
                continue
            seen.add(key)
            picked.append(selected)
            if len(picked) >= limit:
                break
        return picked

