"""
Phase 12.6.F — LLM breadth reduction before CandidateSelector.select_batch.

Internal only; does not change ExplorationResult (Schema 4).

Orchestration (cap K, skip-below) lives in ExplorationEngineV2 — this class is a pure
index-based subset transform when called.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

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
    ) -> list[ExplorationCandidate]:
        """
        Return a subset of candidates by stable index order, or pass-through on failure/empty selection.

        Caller must not invoke when len(candidates)==0. Skip-when-trivial is handled by the engine.
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

        payload = self._wire_payload(candidates)
        prompt = self._build_prompt(instruction, payload)

        gen = None
        if lf_scope_span is not None and hasattr(lf_scope_span, "generation"):
            try:
                gen = lf_scope_span.generation(
                    "exploration.scope",
                    input={"input_count": input_n},
                )
            except Exception:
                gen = None

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
                    gen.end(output={"error": "parse_failed"})
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
                    gen.end(output={"error": "invalid_shape", "input_count": input_n})
                except Exception:
                    pass
            return list(candidates)

        n = len(candidates)
        valid: set[int] = set()
        for x in selected_raw:
            if isinstance(x, bool) or not isinstance(x, int):
                continue
            if 0 <= x < n:
                valid.add(int(x))

        if not valid:
            _LOG.debug(
                "exploration_scoper pass_through: empty_selection scoper_input_n=%s scoper_output_n=%s",
                input_n,
                input_n,
            )
            if gen is not None:
                try:
                    gen.end(output={"error": "empty_selection", "input_count": input_n})
                except Exception:
                    pass
            return list(candidates)

        sorted_indices = sorted(valid)
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
                gen.end(
                    output={
                        "input_count": input_n,
                        "output_count": output_n,
                        "response": raw[:12000] if isinstance(raw, str) else str(raw)[:12000],
                    }
                )
            except Exception:
                pass
        return out

    def _wire_payload(self, candidates: list[ExplorationCandidate]) -> list[dict]:
        return [
            {
                "index": i,
                "file_path": c.file_path,
                "source": c.source,
                "snippet": (c.snippet or "")[: self._max_snippet_chars],
            }
            for i, c in enumerate(candidates)
        ]

    @staticmethod
    def _build_prompt(instruction: str, payload: list[dict]) -> str:
        candidates_json = json.dumps(payload, ensure_ascii=False)
        return (
            "You are selecting which code locations are worth exploring for a task.\n\n"
            "You are given:\n"
            "- an instruction\n"
            "- a list of candidate code snippets from a repository\n\n"
            "Your job:\n"
            "Return a subset of candidate indices that are likely relevant to solving the instruction.\n\n"
            "Guidelines:\n"
            "- Prefer implementation logic over tests, mocks, or configs\n"
            "- Prefer files that appear to contain core logic related to the task\n"
            "- Ignore clearly unrelated files\n"
            "- Keep the selection focused (do not select everything unless necessary)\n"
            "- Selecting all candidates is usually a mistake unless all are clearly relevant\n"
            "- Do NOT rank or order — only select indices\n\n"
            "IMPORTANT:\n"
            "- Only choose from the given indices\n"
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
