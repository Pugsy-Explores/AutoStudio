from __future__ import annotations

import logging
import re
from typing import Any, Callable, Sequence

_LOG = logging.getLogger(__name__)

from agent_v2.exploration.llm_input_normalize import (
    format_explored_locations_for_prompt,
    normalize_selector_batch,
    normalize_selector_single,
)
from agent_v2.config import (
    EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K,
    EXPLORATION_SELECTOR_TOP_K,
    EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS,
    MAX_SELECTOR_CODE_CHARS,
)
from agent_v2.exploration.selector_outline_injection import (
    build_outline_signatures_only,
    prepare_outline_for_selector_prompt,
)
from agent_v2.observability.langfuse_helpers import (
    LANGFUSE_GENERATION_PROMPT_INPUT_MAX_CHARS,
    exploration_llm_call,
)
from agent.prompt_system.registry import get_registry
from agent_v2.schemas.exploration import (
    CoverageSignal,
    ExplorationCandidate,
    SelectionConfidence,
    SelectorBatchResult,
)
from agent_v2.utils.json_extractor import JSONExtractor

_EXPLORATION_SELECTOR_SINGLE_KEY = "exploration.selector.single"
_EXPLORATION_SELECTOR_BATCH_KEY = "exploration.selector.batch"


def _selector_candidate_payload(
    c: ExplorationCandidate,
    *,
    snippet_compact: str = "",
    outline_for_prompt: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "file_path": c.file_path,
        "symbol": c.symbol,
        "source": c.source,
        "snippet_compact": snippet_compact,
    }
    if outline_for_prompt:
        out["outline_for_prompt"] = list(outline_for_prompt)
    return out


def _build_snippet_compact(raw: str, *, head_lines: int = 8, max_chars: int = 600) -> str:
    text = str(raw or "")
    if not text.strip():
        return ""
    lines = text.splitlines()
    keep: list[str] = []
    seen: set[str] = set()
    for line in lines[: max(1, head_lines)]:
        s = line.rstrip()
        if s not in seen:
            seen.add(s)
            keep.append(s)
    pat = re.compile(r"\b(def|class|error|exception)\b", flags=re.IGNORECASE)
    for line in lines:
        if not pat.search(line):
            continue
        s = line.rstrip()
        if s not in seen:
            seen.add(s)
            keep.append(s)
    out = "\n".join(keep).strip()
    if len(out) > max_chars:
        return out[:max_chars]
    return out


def build_selector_prompt_with_budget(
    *,
    instruction: str,
    intent: str,
    limit: int,
    explored_block: str,
    top: list[ExplorationCandidate],
    outline_rows: list[list[dict[str, str]]] | None,
    max_selector_code_chars: int,
    prompt_token_budget: int = 12000,
) -> tuple[str, list[dict[str, Any]], int]:
    """
    Build minimal selector batch payload under a global budget.
    Priority:
    1) identity (always)
    2) outline signatures (always)
    3) outline bodies (if space)
    4) snippet_compact (if space)
    """
    char_budget = max(200, prompt_token_budget * 4)
    # Keep full top list stable; reduce field richness under budget pressure.
    items: list[dict[str, Any]] = []
    for i, c in enumerate(top):
        ol_raw = outline_rows[i] if outline_rows and i < len(outline_rows) else []
        sigs = build_outline_signatures_only(ol_raw)
        fallback_snippet = str(c.symbol or "").strip() or "(no-symbol)"
        item = _selector_candidate_payload(c, snippet_compact=fallback_snippet, outline_for_prompt=sigs)
        items.append(item)

    for idx, c in enumerate(top[: len(items)]):
        ol_raw = outline_rows[idx] if outline_rows and idx < len(outline_rows) else []
        rich = prepare_outline_for_selector_prompt(ol_raw, max_selector_code_chars) if ol_raw else []
        if not rich:
            continue
        cand = dict(items[idx])
        cand["outline_for_prompt"] = rich
        trial_items = items[:idx] + [cand] + items[idx + 1 :]
        trial = normalize_selector_batch(
            instruction=instruction,
            intent=intent or "no intent",
            limit=limit,
            explored_block=explored_block,
            items=trial_items,
        )
        if len(trial) <= char_budget:
            items[idx] = cand

    for idx in range(len(items)):
        sc = _build_snippet_compact(top[idx].snippet_summary or top[idx].snippet or "")
        if not sc:
            continue
        cand = dict(items[idx])
        cand["snippet_compact"] = sc
        trial_items = items[:idx] + [cand] + items[idx + 1 :]
        trial = normalize_selector_batch(
            instruction=instruction,
            intent=intent or "no intent",
            limit=limit,
            explored_block=explored_block,
            items=trial_items,
        )
        if len(trial) <= char_budget:
            items[idx] = cand

    for it in items:
        if not str(it.get("file_path") or "").strip():
            raise ValueError("Selector payload contract violation: missing file_path")
        if "snippet_summary" in it or "source_channels" in it or "symbols" in it:
            raise ValueError("Selector payload contract violation: redundant fields present")
        has_outline = bool(it.get("outline_for_prompt"))
        has_snip = bool(str(it.get("snippet_compact") or "").strip())
        if not (has_outline or has_snip):
            raise ValueError("Selector payload contract violation: each candidate needs outline or snippet")

    candidates_json = normalize_selector_batch(
        instruction=instruction,
        intent=intent or "no intent",
        limit=limit,
        explored_block=explored_block,
        items=items,
    )
    # Do not shrink candidate count here; index space must stay aligned with top list.
    return candidates_json, items, len(top)


def _parse_selected_symbols_raw(raw: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        key = str(k).strip()
        if not key:
            continue
        names: list[str] = []
        if isinstance(v, list):
            for x in v[:3]:
                n = str(x).strip()
                if n:
                    names.append(n)
        else:
            n = str(v).strip()
            if n:
                names = [n]
        out[key] = names
    return out


def _validate_selected_symbols_against_outlines(
    selected_symbols: dict[str, list[str]],
    outline_rows: Sequence[Sequence[dict[str, str]]] | None,
    top_len: int,
    chosen_top_indices: set[int],
) -> dict[str, list[str]]:
    if not outline_rows or len(outline_rows) != top_len:
        ks = {str(i) for i in chosen_top_indices}
        return {k: v[:3] for k, v in selected_symbols.items() if k in ks}
    allowed: dict[int, set[str]] = {}
    for j, row in enumerate(outline_rows):
        allowed[j] = {str(d.get("name") or "").strip() for d in row if (d.get("name") or "").strip()}
    cleaned: dict[str, list[str]] = {}
    for key, names in selected_symbols.items():
        try:
            ji = int(key)
        except (TypeError, ValueError):
            continue
        if ji not in chosen_top_indices:
            continue
        ok = allowed.get(ji, set())
        filt = [n for n in names if n in ok][:3]
        if filt:
            cleaned[str(ji)] = filt
    return cleaned


def _flatten_expanded_symbols(
    selected_symbols: dict[str, list[str]],
    ordered_indices: list[int],
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for j in ordered_indices:
        for name in selected_symbols.get(str(j), []) or []:
            n = str(name).strip()
            if not n or n in seen:
                continue
            seen.add(n)
            out.append(n)
    return out


def _expand_js_indices(parsed_idx: list[int], top_n: int) -> list[int]:
    """Map model indices to 0-based top row indices (matches legacy one-based lone-1 rule)."""
    if not parsed_idx or top_n <= 0:
        return []
    if len(parsed_idx) == 1 and parsed_idx[0] == 1:
        return [0]
    out: list[int] = []
    for raw in parsed_idx:
        try:
            j = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= j < top_n:
            out.append(j)
    if not out and parsed_idx and 0 not in parsed_idx:
        for raw in parsed_idx:
            try:
                j = int(raw) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= j < top_n:
                out.append(j)
    return out


def _pick_candidates_by_top_indices(
    ordered_js: list[int],
    top: list[ExplorationCandidate],
    *,
    limit: int,
) -> tuple[list[ExplorationCandidate], list[int]]:
    picked: list[ExplorationCandidate] = []
    indices: list[int] = []
    seen: set[tuple[str, str]] = set()
    for j in ordered_js:
        if 0 > j or j >= len(top):
            continue
        c = top[j]
        key = (c.file_path, c.symbol or "")
        if key in seen:
            continue
        seen.add(key)
        picked.append(c)
        indices.append(j)
        if len(picked) >= limit:
            break
    return picked, indices


def _extract_selector_batch_lenient(raw: str) -> dict[str, Any]:
    """
    Selector-specific fallback parser for near-JSON outputs.
    Keeps scope intentionally small: only core control fields.
    """
    text = str(raw or "")
    if not text.strip():
        return {}
    compact = text.replace("\\n", "\n").replace("\\t", "\t")
    out: dict[str, Any] = {}
    m_idx = re.search(
        r"(?:\"|')?selected_indices(?:\"|')?\s*[:=]\s*\[([^\]]*)\]",
        compact,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if m_idx:
        nums = re.findall(r"-?\d+", m_idx.group(1) or "")
        out["selected_indices"] = [int(n) for n in nums]
    m_nrc = re.search(
        r"(?:\"|')?no_relevant_candidate(?:\"|')?\s*[:=]\s*(true|false)",
        compact,
        flags=re.IGNORECASE,
    )
    if m_nrc:
        out["no_relevant_candidate"] = m_nrc.group(1).lower() == "true"
    m_conf = re.search(
        r"(?:\"|')?selection_confidence(?:\"|')?\s*[:=]\s*(?:\"|')?(high|medium|low)(?:\"|')?",
        compact,
        flags=re.IGNORECASE,
    )
    if m_conf:
        out["selection_confidence"] = m_conf.group(1).lower()
    m_cov = re.search(
        r"(?:\"|')?coverage_signal(?:\"|')?\s*[:=]\s*(?:\"|')?(good|weak|fragmented|empty|unknown)(?:\"|')?",
        compact,
        flags=re.IGNORECASE,
    )
    if m_cov:
        out["coverage_signal"] = m_cov.group(1).lower()
    return out


def _parse_selector_batch_response(raw: str) -> dict[str, Any]:
    try:
        parsed = JSONExtractor.extract_final_json(raw)
        if isinstance(parsed.get("selected_indices"), str):
            nums = re.findall(r"-?\d+", str(parsed.get("selected_indices") or ""))
            if nums:
                parsed["selected_indices"] = [int(n) for n in nums]
        if isinstance(parsed.get("selected_indices"), list) or isinstance(parsed.get("selected"), list):
            return parsed
        fallback = _extract_selector_batch_lenient(raw)
        if fallback:
            merged = dict(parsed)
            merged.update(fallback)
            return merged
        return parsed
    except Exception as exc:
        fallback = _extract_selector_batch_lenient(raw)
        if fallback:
            return fallback
        raise exc


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
        *,
        lf_exploration_parent: Any = None,
    ) -> ExplorationCandidate | None:
        _LOG.debug("[CandidateSelector.select]")
        if not candidates:
            return None
        top = candidates[:EXPLORATION_SELECTOR_TOP_K]

        if self._llm_generate_single is None and self._llm_generate_messages_single is None:
            raise ValueError("CandidateSelector.select requires single LLM callable in strict mode.")

        payload = [_selector_candidate_payload(c) for c in top]
        candidates_json = normalize_selector_single(items=payload)
        system_prompt, user_prompt = get_registry().render_prompt_parts(
            _EXPLORATION_SELECTOR_SINGLE_KEY,
            model_name=self._model_name_single,
            variables={
                "instruction": instruction,
                "candidates_json": candidates_json,
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

    @staticmethod
    def _normalize_coverage(raw: Any) -> CoverageSignal:
        s = str(raw or "").strip().lower()
        if s in ("good", "weak", "fragmented", "empty", "unknown"):
            return s  # type: ignore[return-value]
        return "unknown"

    @staticmethod
    def _normalize_selection_confidence(raw: Any) -> SelectionConfidence:
        s = str(raw or "").strip().lower()
        if s in ("high", "medium", "low"):
            return s  # type: ignore[return-value]
        return "medium"

    def select_batch(
        self,
        instruction: str,
        intent: str,
        candidates: list[ExplorationCandidate],
        *,
        limit: int,
        explored_location_keys: set[tuple[str, str]] | None = None,
        lf_select_span: Any = None,
        lf_exploration_parent: Any = None,
        outline_rows: list[list[dict[str, str]]] | None = None,
    ) -> SelectorBatchResult:
        _LOG.debug("[CandidateSelector.select_batch]")
        if not candidates or limit <= 0:
            return SelectorBatchResult(
                selected_candidates=[],
                selection_confidence="low",
                coverage_signal="empty",
                selected_symbols={},
                selected_top_indices=[],
            )
        top = candidates[:EXPLORATION_SELECTOR_TOP_K]
        selector_input_count = len(top)
        if self._llm_generate_batch is None and self._llm_generate_messages_batch is None:
            raise ValueError("CandidateSelector.select_batch requires batch LLM callable in strict mode.")

        explored_block = format_explored_locations_for_prompt(
            explored_location_keys,
            max_rows=EXPLORATION_SELECTOR_EXPLORED_BLOCK_TOP_K,
        )
        candidates_json, payload, effective_n = build_selector_prompt_with_budget(
            instruction=instruction,
            intent=intent or "no intent",
            limit=limit,
            explored_block=explored_block,
            top=top,
            outline_rows=outline_rows,
            max_selector_code_chars=MAX_SELECTOR_CODE_CHARS,
        )
        top = top[:effective_n]
        _LOG.info(
            "exploration.selector_batch_counts scoper_output_count=%s selector_input_count=%s selector_effective_n=%s limit=%s",
            len(candidates),
            selector_input_count,
            effective_n,
            limit,
        )
        system_prompt, user_prompt = get_registry().render_prompt_parts(
            _EXPLORATION_SELECTOR_BATCH_KEY,
            model_name=self._model_name_batch,
            variables={
                "candidates_json": candidates_json,
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
            parsed = _parse_selector_batch_response(raw)
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

        _sym_aware_batch = outline_rows is not None
        if _sym_aware_batch and EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
            _LOG.info(
                "exploration.symbol_aware select_batch_llm start top_k=%s limit=%s outline_rows=%s",
                len(top),
                limit,
                len(outline_rows),
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
        if isinstance(parsed, dict) and len(parsed) == 0:
            parsed = {
                "selected_indices": [],
                "selected_symbols": {},
                "selection_confidence": "low",
            }
            holder["parsed"] = parsed
        cov_default: CoverageSignal = self._normalize_coverage(parsed.get("coverage_signal"))
        conf_default: SelectionConfidence = self._normalize_selection_confidence(
            parsed.get("selection_confidence")
        )
        if bool(parsed.get("no_relevant_candidate")):
            if _sym_aware_batch and EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
                _LOG.info("exploration.symbol_aware select_batch_llm result=no_relevant_candidate")
            return SelectorBatchResult(
                selected_candidates=[],
                selection_confidence="low",
                coverage_signal="weak",
                selected_symbols={},
                expanded_symbols=[],
                selected_top_indices=[],
            )

        selected_indices_raw_pre = parsed.get("selected_indices")
        sraw_pre = parsed.get("selected")
        if (
            isinstance(selected_indices_raw_pre, list)
            and len(selected_indices_raw_pre) == 0
            and not (isinstance(sraw_pre, list) and len(sraw_pre) > 0)
        ):
            sym_raw_empty = _parse_selected_symbols_raw(parsed.get("selected_symbols"))
            sym_clean_empty = _validate_selected_symbols_against_outlines(
                sym_raw_empty,
                outline_rows,
                len(top),
                set(),
            )
            return SelectorBatchResult(
                selected_candidates=[],
                selection_confidence=conf_default,
                coverage_signal=cov_default,
                selected_symbols=sym_clean_empty,
                expanded_symbols=[],
                selected_top_indices=[],
            )

        ordered_js: list[int] = []
        selected_indices_raw = parsed.get("selected_indices")
        if isinstance(selected_indices_raw, list) and len(selected_indices_raw) > 0:
            parsed_idx: list[int] = []
            for idx in selected_indices_raw:
                try:
                    parsed_idx.append(int(idx))
                except (TypeError, ValueError):
                    continue
            ordered_js = _expand_js_indices(parsed_idx, len(top))

        picked: list[ExplorationCandidate] = []
        indices: list[int] = []
        if ordered_js:
            picked, indices = _pick_candidates_by_top_indices(ordered_js, top, limit=limit)

        has_explicit_indices = isinstance(selected_indices_raw, list) and len(selected_indices_raw) > 0
        if not picked and not has_explicit_indices:
            selected_list = parsed.get("selected")
            if isinstance(selected_list, list):
                picked, indices = self._match_many_with_top_indices(
                    selected_list, top, limit=limit
                )

        if not picked:
            sir = parsed.get("selected_indices")
            sraw = parsed.get("selected")
            if not (isinstance(sir, list) and len(sir) > 0) and not isinstance(sraw, list):
                raise ValueError(
                    "CandidateSelector.select_batch expected `selected_indices` or `selected` list "
                    "in parsed JSON."
                )
            _LOG.warning(
                "exploration.select_batch: no matchable selections from model "
                "(indices=%r); emitting fragmented signal (no silent fallback)",
                selected_indices_raw,
            )
            if _sym_aware_batch and EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
                _LOG.info("exploration.symbol_aware select_batch_llm result=fragmented_no_match")
            return SelectorBatchResult(
                selected_candidates=[],
                selection_confidence="low",
                coverage_signal="fragmented",
                selected_symbols={},
                expanded_symbols=[],
                selected_top_indices=[],
            )

        sym_raw = _parse_selected_symbols_raw(parsed.get("selected_symbols"))
        chosen_set = set(indices)
        sym_clean = _validate_selected_symbols_against_outlines(
            sym_raw,
            outline_rows,
            len(top),
            chosen_set,
        )

        if _sym_aware_batch and EXPLORATION_SYMBOL_AWARE_LOG_PROGRESS:
            _LOG.info(
                "exploration.symbol_aware select_batch_llm result ok picked=%s top_idx=%s "
                "validated_symbol_keys=%s raw_symbol_keys=%s",
                len(picked),
                indices,
                sorted(sym_clean.keys()),
                sorted(sym_raw.keys()) if sym_raw else [],
            )

        return SelectorBatchResult(
            selected_candidates=picked,
            selection_confidence=conf_default,
            coverage_signal=cov_default,
            selected_symbols=sym_clean,
            expanded_symbols=_flatten_expanded_symbols(sym_clean, indices),
            selected_top_indices=indices,
        )

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
    def _match_many_with_top_indices(
        cls,
        choices: list[dict],
        top: list[ExplorationCandidate],
        *,
        limit: int,
    ) -> tuple[list[ExplorationCandidate], list[int]]:
        picked: list[ExplorationCandidate] = []
        indices: list[int] = []
        seen: set[tuple[str, str]] = set()
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            selected = cls._match(choice, top)
            if selected is None:
                continue
            key = (selected.file_path, selected.symbol or "")
            if key in seen:
                continue
            j = -1
            fp = selected.file_path
            for i, c in enumerate(top):
                if c.file_path == fp:
                    j = i
                    break
            if j < 0:
                continue
            seen.add(key)
            picked.append(selected)
            indices.append(j)
            if len(picked) >= limit:
                break
        return picked, indices

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

