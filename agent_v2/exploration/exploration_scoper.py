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

import json
import logging
import re
from typing import Any, Callable

from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    langfuse_generation_end_with_usage,
    langfuse_generation_input_with_prompt,
    try_langfuse_generation,
)
from agent_v2.schemas.exploration import ExplorationCandidate

_LOG = logging.getLogger(__name__)


class ExplorationScoper:
    """Select a subset of discovery candidates by index (single LLM call when invoked)."""

    def __init__(
        self,
        llm_generate: Callable[[str], str] | None = None,
        *,
        max_snippet_chars: int = 600,
    ):
        self._llm_generate = llm_generate
        self._max_snippet_chars = max_snippet_chars

    def scope(
        self,
        instruction: str,
        candidates: list[ExplorationCandidate],
        *,
        lf_scope_span: Any = None,
        lf_exploration_parent: Any = None,
    ) -> list[ExplorationCandidate]:
        """
        Return a subset of candidates by stable index order, or pass-through on failure/empty selection.

        Caller must not invoke when len(candidates)==0. Skip-when-trivial is handled by the engine.

        ``lf_exploration_parent`` is the ``exploration`` span: used as a fallback parent for the
        Langfuse generation when the ``exploration.scope`` span is missing or ``.generation`` fails.
        """
        input_n = len(candidates)
        if input_n == 0:
            return []

        if self._llm_generate is None:
            _LOG.debug(
                "exploration_scoper pass_through: no_llm scoper_input_n=%s scoper_output_n=%s",
                input_n,
                input_n,
            )
            return list(candidates)

        payload, dedupe_orig_indices = self._aggregate_payload_by_file_path(candidates)
        dedupe_n = len(payload)
        prompt = self._build_prompt(instruction, payload)

        gen = try_langfuse_generation(
            lf_scope_span,
            lf_exploration_parent,
            name="exploration.scope",
            input=langfuse_generation_input_with_prompt(
                prompt,
                extra={"input_count": input_n, "dedupe_count": dedupe_n},
            ),
        )

        try:
            raw = self._llm_generate(prompt)
            parsed = _parse_json_object(raw)
        except Exception:
            _LOG.debug(
                "exploration_scoper pass_through: parse_error scoper_input_n=%s scoper_output_n=%s",
                input_n,
                input_n,
                exc_info=True,
            )
            if gen is not None:
                try:
                    langfuse_generation_end_with_usage(gen, output={"error": "parse_failed"})
                except Exception:
                    pass
            return list(candidates)

        selected_raw = parsed.get("selected_indices")
        if not isinstance(selected_raw, list):
            _LOG.debug(
                "exploration_scoper pass_through: invalid_shape scoper_input_n=%s scoper_output_n=%s",
                input_n,
                input_n,
            )
            if gen is not None:
                try:
                    langfuse_generation_end_with_usage(
                        gen, output={"error": "invalid_shape", "input_count": input_n}
                    )
                except Exception:
                    pass
            return list(candidates)

        valid_dedupe: set[int] = set()
        for x in selected_raw:
            if isinstance(x, bool) or not isinstance(x, int):
                continue
            if 0 <= int(x) < dedupe_n:
                valid_dedupe.add(int(x))

        if not valid_dedupe:
            _LOG.debug(
                "exploration_scoper pass_through: empty_selection scoper_input_n=%s scoper_output_n=%s",
                input_n,
                input_n,
            )
            if gen is not None:
                try:
                    langfuse_generation_end_with_usage(
                        gen, output={"error": "empty_selection", "input_count": input_n}
                    )
                except Exception:
                    pass
            return list(candidates)

        valid_orig: set[int] = set()
        for j in valid_dedupe:
            valid_orig.update(dedupe_orig_indices[j])
        sorted_indices = sorted(valid_orig)
        out = [candidates[i] for i in sorted_indices]
        output_n = len(out)
        ratio = output_n / input_n if input_n else 0.0
        _LOG.debug(
            "exploration_scoper ok: scoper_skipped=false scoper_input_n=%s scoper_output_n=%s "
            "scoper_selected_ratio=%.4f",
            input_n,
            output_n,
            ratio,
        )
        if gen is not None:
            try:
                langfuse_generation_end_with_usage(
                    gen,
                    output={
                        "input_count": input_n,
                        "output_count": output_n,
                        "response": (
                            raw[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]
                            if isinstance(raw, str)
                            else str(raw)[:LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS]
                        ),
                    },
                )
            except Exception:
                pass
        return out

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

    @staticmethod
    def _build_prompt(instruction: str, payload: list[dict]) -> str:
        candidates_json = json.dumps(payload, ensure_ascii=False)
        return (
            "You are selecting which code locations are worth exploring for a task.\n\n"
            "You are given:\n"
            "- an instruction\n"
            "- a list of candidates from a repository (one entry per **unique file path**)\n"
            "- each entry may aggregate multiple discovery hits on the same file "
            "(snippets, sources, symbols as parallel lists)\n\n"
            "Your job:\n"
            "Return a subset of candidate **indices** that are likely relevant to solving the instruction.\n\n"
            "Guidelines:\n"
            "- Prefer implementation logic over tests, mocks, or configs\n"
            "- Prefer files that appear to contain core logic related to the task\n"
            "- Ignore clearly unrelated files\n"
            "- Keep the selection focused (do not select everything unless necessary)\n"
            "- Selecting all candidates is usually a mistake unless all are clearly relevant\n"
            "- Do NOT rank or order — only select indices\n\n"
            "IMPORTANT:\n"
            "- Indices refer to the numbered list below (0 .. N-1 for N unique file paths)\n"
            "- Do not invent new files or indices\n"
            "- If none are relevant, return an empty list\n\n"
            "---\n\n"
            f"Instruction:\n{instruction}\n\n"
            f"Candidates:\n{candidates_json}\n\n"
            "---\n\n"
            "Return JSON ONLY in this format:\n\n"
            '{\n  "selected_indices": [ ... ]\n}\n'
        )


def _parse_json_object(text: str) -> dict:
    stripped = (text or "").strip()
    if "```" in stripped:
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
        if match:
            stripped = match.group(1).strip()
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise ValueError("exploration_scoper expected JSON object")
    return data
