"""
Phase 12.6.F — LLM breadth reduction before CandidateSelector.select_batch.

Internal only; does not change ExplorationResult (Schema 4).

Before the scoping LLM call, discovery candidates are **deduplicated by ``file_path``**
(first-seen order): snippets, sources, and symbols are aggregated into parallel lists per
file. The model returns indices into that deduplicated list; the implementation expands
each chosen slot back to **all** original ``ExplorationCandidate`` rows for that path.

Orchestration (cap K, skip-below) lives in ExplorationEngineV2 — this class is a pure
subset transform when called.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from agent_v2.exploration.llm_input_normalize import normalize_scoper
from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    exploration_llm_call,
)
from agent.prompt_system.registry import get_registry
from agent_v2.schemas.exploration import ExplorationCandidate
from agent_v2.utils.json_extractor import JSONExtractor

_LOG = logging.getLogger(__name__)

_EXPLORATION_SCOPER_KEY = "exploration.scoper"


class ExplorationScoper:
    """Select a subset of discovery candidates by index (single LLM call when invoked)."""

    def __init__(
        self,
        llm_generate: Callable[[str], str] | None = None,
        *,
        max_snippet_chars: int = 600,
        model_name: str | None = None,
    ):
        self._llm_generate = llm_generate
        self._max_snippet_chars = max_snippet_chars
        self._model_name = model_name

    def scope(
        self,
        instruction: str,
        candidates: list[ExplorationCandidate],
        *,
        lf_scope_span: Any = None,
        lf_exploration_parent: Any = None,
    ) -> list[ExplorationCandidate]:
        """
        Return a subset of candidates by stable index order.

        Caller must not invoke when len(candidates)==0. Skip-when-trivial is handled by the engine.

        ``lf_exploration_parent`` is the ``exploration`` span: used as a fallback parent for the
        Langfuse generation when the ``exploration.scope`` span is missing or ``.generation`` fails.
        """
        _LOG.debug("[ExplorationScoper.scope]")
        input_n = len(candidates)
        if input_n == 0:
            return []

        if self._llm_generate is None:
            raise ValueError("ExplorationScoper requires llm_generate in strict mode.")

        payload, dedupe_orig_indices = self._aggregate_payload_by_file_path(candidates)
        dedupe_n = len(payload)
        prompt = self._build_prompt(instruction, payload)

        holder: list[list[ExplorationCandidate]] = []

        def _invoke() -> str:
            return self._llm_generate(prompt)

        def _complete(raw: str) -> tuple[dict[str, Any], dict[str, Any]]:
            selected_raw: Any = None
            parse_error: Exception | None = None
            try:
                selected_raw = self._parse_scoper_selected_indices(raw)
            except Exception as exc:
                parse_error = exc
            if not isinstance(selected_raw, list):
                if parse_error is not None:
                    _LOG.warning(
                        "exploration_scoper parse failure: could not parse selected_indices as JSON/list; "
                        "error=%s raw_preview=%r",
                        parse_error,
                        str(raw or "")[:500],
                    )
                    raise parse_error
                _LOG.warning(
                    "exploration_scoper parse failure: selected_indices missing or non-list; raw_preview=%r",
                    str(raw or "")[:500],
                )
                raise ValueError("ExplorationScoper expected `selected_indices` list in parsed JSON.")
            valid_dedupe: set[int] = set()
            for x in selected_raw:
                if isinstance(x, bool) or not isinstance(x, int):
                    continue
                if 0 <= int(x) < dedupe_n:
                    valid_dedupe.add(int(x))
            if not valid_dedupe:
                raise ValueError("ExplorationScoper selected no valid indices in strict mode.")
            valid_orig: set[int] = set()
            for j in valid_dedupe:
                valid_orig.update(dedupe_orig_indices[j])
            sorted_indices = sorted(valid_orig)
            out = [candidates[i] for i in sorted_indices]
            holder.append(out)
            output_n = len(out)
            ratio = output_n / input_n if input_n else 0.0
            _LOG.debug(
                "exploration_scoper ok: scoper_skipped=false scoper_input_n=%s scoper_output_n=%s "
                "scoper_selected_ratio=%.4f",
                input_n,
                output_n,
                ratio,
            )
            return (
                {
                    "input_count": input_n,
                    "output_count": output_n,
                    "response": (
                        raw[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]
                        if isinstance(raw, str)
                        else str(raw)[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]
                    ),
                },
                {"stage": "scope", "ok": True},
            )

        exploration_llm_call(
            lf_scope_span,
            lf_exploration_parent,
            name="exploration.scope",
            prompt=prompt,
            prompt_registry_key=_EXPLORATION_SCOPER_KEY,
            invoke=_invoke,
            stage="scope",
            model_name=self._model_name,
            input_extra={"input_count": input_n, "dedupe_count": dedupe_n},
            on_complete=_complete,
        )
        return holder[0]

    @classmethod
    def _parse_scoper_selected_indices(cls, raw: Any) -> list[int] | None:
        text = str(raw or "")
        try:
            parsed = JSONExtractor.extract_final_json(text)
            selected_raw = parsed.get("selected_indices")
            if isinstance(selected_raw, str):
                nums = re.findall(r"-?\d+", selected_raw)
                if nums:
                    return [int(n) for n in nums]
            if isinstance(selected_raw, list):
                return selected_raw
        except Exception as exc:
            fallback = cls._extract_selected_indices_lenient(text)
            if isinstance(fallback, list):
                return fallback
            raise exc
        return cls._extract_selected_indices_lenient(text)

    @staticmethod
    def _extract_selected_indices_lenient(raw: Any) -> list[int] | None:
        """
        Robust fallback for model outputs that are near-JSON but malformed:
        fenced text, extra whitespace, escaped newlines/tabs, or bare key/value style.
        """
        text = str(raw or "")
        if not text.strip():
            return None
        compact = text.replace("\\n", "\n").replace("\\t", "\t")
        # selected_indices: [0, 4] / "selected_indices": [0,4] / selected_indices=[0,4]
        m = re.search(
            r"(?:\"|')?selected_indices(?:\"|')?\s*[:=]\s*\[([^\]]*)\]",
            compact,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            nums = re.findall(r"-?\d+", m.group(1) or "")
            return [int(n) for n in nums]
        # selected_indices: 0, 4 (no brackets)
        m2 = re.search(
            r"(?:\"|')?selected_indices(?:\"|')?\s*[:=]\s*([0-9,\s-]+)",
            compact,
            flags=re.IGNORECASE,
        )
        if m2:
            nums = re.findall(r"-?\d+", m2.group(1) or "")
            return [int(n) for n in nums]
        return None

    def _aggregate_payload_by_file_path(
        self, candidates: list[ExplorationCandidate]
    ) -> tuple[list[dict], list[list[int]]]:
        """
        One LLM row per unique ``file_path`` (first-seen order). Aggregate snippets,
        sources, and symbols from all discovery rows sharing that path.

        Returns:
            payload rows for the prompt, and ``dedupe_orig_indices[j]`` = original
            candidate indices for dedupe slot ``j`` (for expanding the LLM selection).
        """
        path_order: list[str] = []
        path_to_orig_indices: dict[str, list[int]] = {}
        for i, c in enumerate(candidates):
            fp = c.file_path
            if fp not in path_to_orig_indices:
                path_to_orig_indices[fp] = []
                path_order.append(fp)
            path_to_orig_indices[fp].append(i)

        cap = self._max_snippet_chars
        payload: list[dict] = []
        dedupe_orig_indices: list[list[int]] = []
        for j, fp in enumerate(path_order):
            orig_ixs = path_to_orig_indices[fp]
            dedupe_orig_indices.append(orig_ixs)
            rows = [candidates[k] for k in orig_ixs]
            sources: list[str] = []
            snippets: list[str] = []
            symbols: list[str | None] = []
            for c in rows:
                if c.source not in sources:
                    sources.append(c.source)
                snippets.append((c.snippet or "")[:cap])
                symbols.append(c.symbol)
            payload.append(
                {
                    "index": j,
                    "file_path": fp,
                    "sources": sources,
                    "snippets": snippets,
                    "symbols": symbols,
                }
            )
        return payload, dedupe_orig_indices

    def _build_prompt(self, instruction: str, payload: list[dict]) -> str:
        candidates_json = normalize_scoper(instruction=instruction, rows=payload)
        tmpl = get_registry().get_instructions(_EXPLORATION_SCOPER_KEY, model_name=self._model_name)
        return tmpl.format(candidates_json=candidates_json) + "\n"
