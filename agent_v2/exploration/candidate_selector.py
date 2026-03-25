from __future__ import annotations

import json
import re
from typing import Any, Callable

from agent_v2.schemas.exploration import ExplorationCandidate


class CandidateSelector:
    """Select exploration candidates in a single ranking pass."""

    def __init__(self, llm_generate: Callable[[str], str] | None = None):
        self._llm_generate = llm_generate

    def select(
        self,
        instruction: str,
        candidates: list[ExplorationCandidate],
        seen_files: set[str],
    ) -> ExplorationCandidate | None:
        if not candidates:
            return None
        top = candidates[:10]

        if self._llm_generate is not None:
            try:
                payload = [
                    {
                        "file_path": c.file_path,
                        "symbol": c.symbol,
                        "source": c.source,
                    }
                    for c in top
                ]
                prompt = (
                    "You are selecting the most relevant code location.\n"
                    "Return STRICT JSON only: {\"file_path\":\"...\",\"symbol\":\"...\"}.\n"
                    "Prefer implementation files over tests and already explored files.\n\n"
                    f"Instruction:\n{instruction}\n\nCandidates:\n{json.dumps(payload)}"
                )
                raw = self._llm_generate(prompt)
                choice = self._parse_json_object(raw)
                selected = self._match(choice, top)
                if selected is not None:
                    return selected
            except Exception:
                pass

        unvisited = [c for c in top if c.file_path not in seen_files]
        pool = unvisited if unvisited else top
        pool = sorted(pool, key=self._heuristic_score, reverse=True)
        return pool[0] if pool else None

    def select_batch(
        self,
        instruction: str,
        candidates: list[ExplorationCandidate],
        seen_files: set[str],
        *,
        limit: int,
        lf_select_span: Any = None,
    ) -> list[ExplorationCandidate] | None:
        if not candidates or limit <= 0:
            return []
        top = candidates[:10]
        unvisited_top = [c for c in top if c.file_path not in seen_files]
        fallback_pool = unvisited_top if unvisited_top else top
        fallback_ranked = sorted(fallback_pool, key=self._heuristic_score, reverse=True)[:limit]

        if self._llm_generate is None:
            return fallback_ranked

        gen = None
        if lf_select_span is not None and hasattr(lf_select_span, "generation"):
            try:
                gen = lf_select_span.generation(
                    "exploration.select",
                    input={"candidates_in": len(top), "limit": limit},
                )
            except Exception:
                gen = None

        try:
            payload = [
                {
                    "file_path": c.file_path,
                    "symbol": c.symbol,
                    "source": c.source,
                }
                for c in top
            ]
            prompt = (
                "You are ranking code locations for exploration.\n"
                "Return STRICT JSON only in one of these shapes:\n"
                '1) {"selected":[{"file_path":"...","symbol":"..."}, ...]}\n'
                '2) {"selected":[],"no_relevant_candidate":true}\n'
                "Rules:\n"
                "- Rank best-first.\n"
                "- Prefer implementation files over tests and already explored files.\n"
                "- If no candidates are relevant, set no_relevant_candidate=true.\n\n"
                f"Instruction:\n{instruction}\n\n"
                f"Limit: {limit}\n\n"
                f"Candidates:\n{json.dumps(payload)}"
            )
            raw = self._llm_generate(prompt)
            parsed = self._parse_json_object(raw)
            if bool(parsed.get("no_relevant_candidate")):
                if gen is not None:
                    try:
                        gen.end(output={"no_relevant_candidate": True, "response": raw[:12000]})
                    except Exception:
                        pass
                return None
            selected_raw = parsed.get("selected")
            if isinstance(selected_raw, list):
                ranked = self._match_many(selected_raw, top, limit=limit)
                if ranked:
                    if gen is not None:
                        try:
                            gen.end(output={"response": raw[:12000]})
                        except Exception:
                            pass
                    return ranked
        except Exception:
            if gen is not None:
                try:
                    gen.end(output={"error": "select_batch_failed"})
                except Exception:
                    pass
            pass

        if gen is not None:
            try:
                gen.end(output={"fallback": True})
            except Exception:
                pass
        return fallback_ranked

    @staticmethod
    def _heuristic_score(candidate: ExplorationCandidate) -> tuple[int, int]:
        file_score = 0 if "/test" in candidate.file_path or "test_" in candidate.file_path else 1
        symbol_score = 1 if candidate.symbol else 0
        return (file_score, symbol_score)

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

    @staticmethod
    def _parse_json_object(text: str) -> dict:
        stripped = (text or "").strip()
        if "```" in stripped:
            match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL)
            if match:
                stripped = match.group(1).strip()
        data = json.loads(stripped)
        if not isinstance(data, dict):
            raise ValueError("Candidate selector expected JSON object")
        return data
