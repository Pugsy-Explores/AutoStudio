"""
LLM-assisted bundle selector for code EXPLAIN.
Selector-only orchestration: chooses candidate IDs from retrieval_candidate_pool,
rebuilds ranked_context from selected IDs. Fail-safe fallback on any error.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING

from agent.retrieval.retrieval_intent import INTENT_ARCHITECTURE

if TYPE_CHECKING:
    from agent.memory.state import AgentState

logger = logging.getLogger(__name__)

# Query hints that suggest connection/flow/wiring (architecture-like)
_CONNECTION_HINTS = frozenset(
    "connect flow wiring entrypoint entry point flow from how does".split()
)
_CONNECTION_PATTERNS = (
    re.compile(r"how does .+ connect", re.I),
    re.compile(r"flow from .+ to", re.I),
    re.compile(r"entry point", re.I),
    re.compile(r"\bwiring\b", re.I),
    re.compile(r"how .+ work(s)?", re.I),
)
_ARCHITECTURE_KEYWORDS = frozenset(
    "connect flow how where entry init setup wiring".split()
)


def _query_has_architecture_keywords(query: str) -> bool:
    """True if query contains any architecture-like keyword (substring match)."""
    if not query or not str(query).strip():
        return False
    q = str(query).strip().lower()
    words = set(re.findall(r"[a-z0-9_]+", q))
    return bool(_ARCHITECTURE_KEYWORDS & words)


def _is_test_path(path: str) -> bool:
    p = str(path or "").replace("\\", "/").lower()
    return any(x in p for x in ("tests/", "test/", "test_", "_test.py", "conftest.py"))


def _query_suggests_connection_or_flow(query: str) -> bool:
    """True if query suggests connection/flow/wiring/entrypoint (architecture-style)."""
    if not query or not str(query).strip():
        return False
    q = str(query).strip().lower()
    if any(p.search(q) for p in _CONNECTION_PATTERNS):
        return True
    words = set(re.findall(r"[a-z0-9_]+", q))
    return bool(_CONNECTION_HINTS & words)


def _linked_row_count(pool: list) -> int:
    """Count rows with non-empty relations."""
    return sum(
        1
        for r in pool
        if isinstance(r, dict)
        and isinstance(r.get("relations"), list)
        and r.get("relations")
    )


def _dedupe_preserve_order(ids: list[str]) -> list[str]:
    """Deduplicate IDs while preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _rank_linked_candidate(r: dict) -> tuple:
    """Sort key for linked candidates: richer relations and impl body rank higher."""
    return (
        len(r.get("relations") or []),
        int(bool(r.get("implementation_body_present"))),
        r.get("final_score", 0),
    )


def _linked_row_connects_to_selected(row: dict, selected_files: set[str]) -> bool:
    """True if linked row's relations reference selected files, or row's file is in selection."""
    if not isinstance(row, dict) or not selected_files:
        return False
    rels = row.get("relations")
    if not isinstance(rels, list) or not rels:
        return False

    def _norm(p: str) -> str:
        return (p or "").strip().replace("\\", "/")

    row_file = _norm(row.get("file") or "")
    if row_file and row_file in selected_files:
        return True
    for rel in rels:
        if not isinstance(rel, dict):
            continue
        tf = _norm(rel.get("target_file") or rel.get("file") or "")
        if tf and tf in selected_files:
            return True
        if tf:
            for sf in selected_files:
                if sf and (tf in sf or sf.endswith("/" + tf) or tf.endswith("/" + sf)):
                    return True
    return False


def _validate_selection_against_constraints(
    selected_rows: list[dict], intent: str
) -> list[str]:
    """Returns list of violation/signal strings (empty = valid)."""
    violations: list[str] = []
    linked_count = sum(
        1
        for r in selected_rows
        if isinstance(r, dict)
        and isinstance(r.get("relations"), list)
        and r.get("relations")
    )
    impl_count = sum(
        1
        for r in selected_rows
        if isinstance(r, dict) and r.get("implementation_body_present")
    )
    test_count = sum(
        1
        for r in selected_rows
        if isinstance(r, dict) and _is_test_path(r.get("file") or "")
    )
    if intent == INTENT_ARCHITECTURE and linked_count == 0:
        violations.append("missing_linked_row")
    if impl_count == 0:
        violations.append("missing_impl_row")
    if test_count > impl_count:
        violations.append("test_dominated_context")
    # Soft signal for observability: single linked row + single file = insufficient multi-hop structure
    if intent == INTENT_ARCHITECTURE and linked_count == 1:
        distinct_files = len(set(r.get("file") for r in selected_rows if isinstance(r, dict) and r.get("file")))
        if distinct_files == 1:
            violations.append("insufficient_multi_hop_structure")
    return violations


def should_use_bundle_selector(
    step: dict,
    state: "AgentState",
    ranked_context_or_pool: list | None,
) -> bool:
    """
    Use selector when:
    - ENABLE_LLM_BUNDLE_SELECTOR is on (unless FORCE_SELECTOR_IN_EVAL)
    - artifact_mode == "code", action == "EXPLAIN"
    - retrieval_candidate_pool exists
    - pool_size >= 3; if > MAX, trim to top MAX and allow
    - retrieval_intent == architecture OR query has architecture keywords
      (or FORCE_SELECTOR_IN_EVAL / linked_row_count >= 2 bypasses intent)
    Sets state.context["bundle_selector_skip_reason"] in all branches.
    """
    from config.retrieval_config import (
        ENABLE_LLM_BUNDLE_SELECTOR,
        FORCE_SELECTOR_IN_EVAL,
        MAX_SELECTOR_CANDIDATE_POOL,
    )

    ctx = state.context or {}
    ctx.setdefault("bundle_selector_skip_reason", "")

    if not ENABLE_LLM_BUNDLE_SELECTOR and not FORCE_SELECTOR_IN_EVAL:
        ctx["bundle_selector_skip_reason"] = "flag_disabled"
        return False

    if (step.get("artifact_mode") or "code") != "code":
        ctx["bundle_selector_skip_reason"] = "not_explain_code"
        return False
    if (step.get("action") or "EXPLAIN").upper() != "EXPLAIN":
        ctx["bundle_selector_skip_reason"] = "not_explain_code"
        return False

    pool = ctx.get("retrieval_candidate_pool")
    if not pool or not isinstance(pool, list):
        ctx["bundle_selector_skip_reason"] = "no_pool"
        return False

    n = len(pool)
    if n < 3:
        ctx["bundle_selector_skip_reason"] = "pool_too_small"
        return False

    # Trim to top MAX if oversized; still allow selector
    if n > MAX_SELECTOR_CANDIDATE_POOL:
        trimmed = list(pool[:MAX_SELECTOR_CANDIDATE_POOL])
        ctx["retrieval_candidate_pool"] = trimmed
        pool = trimmed
        n = len(pool)

    # Phase 5: scoped guard — skip selector only for INTENT_ARCHITECTURE when pool has no linked rows
    if not FORCE_SELECTOR_IN_EVAL:
        intent_now = ctx.get("retrieval_intent") or ""
        if intent_now == INTENT_ARCHITECTURE:
            pool_has_linked = any(
                isinstance(r.get("relations"), list) and r.get("relations")
                for r in pool
                if isinstance(r, dict)
            )
            if not pool_has_linked:
                ctx["bundle_selector_skip_reason"] = "arch_pool_lacks_linked"
                return False

    linked = _linked_row_count(pool)
    intent = ctx.get("retrieval_intent") or ""
    query = (step.get("description") or step.get("query") or state.instruction or "").strip()
    intent_ok = (
        intent == INTENT_ARCHITECTURE
        or _query_has_architecture_keywords(query)
        or _query_suggests_connection_or_flow(query)
    )

    if FORCE_SELECTOR_IN_EVAL:
        ctx["bundle_selector_skip_reason"] = ""
        return True

    if linked >= 2:
        ctx["bundle_selector_skip_reason"] = ""
        return True

    if not intent_ok:
        ctx["bundle_selector_skip_reason"] = "intent_not_matched"
        return False

    ctx["bundle_selector_skip_reason"] = ""
    return True


def _format_pool_row(row: dict) -> tuple[str, str]:
    """Format single pool row for display. Returns (header_line, snippet_line)."""
    cid = row.get("candidate_id", "")
    f = (row.get("file") or "").strip()
    sym = (row.get("symbol") or "").strip()
    kind = row.get("candidate_kind") or "file"
    impl = " [impl]" if row.get("implementation_body_present") else ""
    rels = " [linked]" if row.get("relations") else ""
    test = " [test]" if _is_test_path(f) else ""
    bridge = " [bridge]" if row.get("is_bridge") else ""
    snip = (row.get("snippet") or "")[:200].replace("\n", " ")
    header = f"  {cid}: {f} {sym} ({kind}){impl}{rels}{test}{bridge}"
    snippet_line = f"    snippet: {snip}..."
    return header, snippet_line


def build_bundle_selector_payload(
    step: dict,
    state: "AgentState",
    pool: list[dict],
    *,
    top_bundles: list[dict] | None = None,
) -> str:
    """
    Build user prompt for selector. Includes question, pool summary, and output format.
    When top_bundles provided (ENABLE_BUNDLE_SELECTION), emits grouped payload with
    bundle metadata (files, structure, PRIMARY/SUPPORT).
    """
    question = (step.get("description") or step.get("query") or state.instruction or "").strip()
    lines = [
        "Question:",
        question,
        "",
        "Candidate pool (choose IDs to keep for explanation context):",
    ]

    id_to_row = {str(r.get("candidate_id", "")): r for r in pool if r.get("candidate_id")}
    bundled_ids: set[str] = set()

    if top_bundles:
        for b in top_bundles:
            bid = b.get("bundle_id", "")
            cids = b.get("candidate_ids", [])
            files = b.get("files") or set()
            linked = b.get("linked_count", 0)
            impl = b.get("impl_count", 0)
            files_str = ", ".join(sorted(files)[:5])
            if len(files) > 5:
                files_str += ", ..."
            lines.append(f"[BUNDLE {bid}]")
            lines.append(f"files: {files_str}")
            lines.append(f"structure: {linked} links, {impl} impl")
            lines.append("")
            for cid in cids:
                bundled_ids.add(cid)
                row = id_to_row.get(cid)
                if not row:
                    continue
                header, snippet = _format_pool_row(row)
                lines.append(header)
                lines.append(snippet)
            lines.append("")
        unbundled = [r for r in pool if str(r.get("candidate_id", "")) not in bundled_ids]
        if unbundled:
            lines.append("[UNBUNDLED]")
            for row in unbundled:
                header, snippet = _format_pool_row(row)
                lines.append(header)
                lines.append(snippet)
    else:
        for row in pool:
            header, snippet = _format_pool_row(row)
            lines.append(header)
            lines.append(snippet)

    lines.extend([
        "",
        "Respond with strict JSON only:",
        '{"keep_ids": ["rc_0001", "rc_0002"], "primary_ids": ["rc_0001"], "supporting_ids": ["rc_0002"], "reason": "brief reason"}',
        "- choose only IDs that exist in the pool",
        "- prefer implementation-backed rows",
        "- prefer non-test implementation files",
        "- for architecture questions prefer linked cross-file rows",
        "- keep the set small (2 to 5 max)",
    ])
    return "\n".join(lines)


def parse_bundle_selector_output(raw_text: str) -> dict | None:
    """
    Parse selector output to structured result.
    Returns dict with keep_ids, primary_ids, supporting_ids, reason or None on parse failure.
    """
    if not raw_text or not str(raw_text).strip():
        return None
    text = str(raw_text).strip()
    # Extract JSON block: find { ... } containing "keep_ids"
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    end = -1
    for i, c in enumerate(text[start:], start):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        return None
    text = text[start : end + 1]
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
        keep = data.get("keep_ids")
        if not isinstance(keep, list):
            return None
        return {
            "keep_ids": [str(x) for x in keep],
            "primary_ids": [str(x) for x in (data.get("primary_ids") or [])],
            "supporting_ids": [str(x) for x in (data.get("supporting_ids") or [])],
            "reason": str(data.get("reason", "")).strip(),
        }
    except json.JSONDecodeError as e:
        logger.debug("[bundle_selector] parse failed: %s", e)
        return None


def rebuild_ranked_context_from_selected_ids(
    pool: list[dict],
    selected_ids: list[str],
) -> list[dict]:
    """
    Rebuild ranked_context from pool rows matching selected_ids.
    Preserves typed metadata. Order follows selected_ids.
    """
    id_to_row = {str(r.get("candidate_id", "")): r for r in pool if r.get("candidate_id")}
    result = []
    for cid in selected_ids:
        row = id_to_row.get(cid)
        if row is None:
            continue
        # Strip candidate_id from output (ranked_context does not use it) but keep all other fields
        out = dict(row)
        result.append(out)
    return result


def build_bundle_selector_observability_summary(state_or_context: "AgentState | dict") -> dict:
    """
    Summarize selector state from AgentState or context dict for observability/rollback.
    Safe to call when selector was not used (returns used=False, skip_reason, etc.).
    """
    ctx = (
        getattr(state_or_context, "context", None)
        if not isinstance(state_or_context, dict)
        else state_or_context
    )
    ctx = ctx or {}
    used = bool(ctx.get("bundle_selector_used"))
    keep_ids = list(ctx.get("bundle_selector_keep_ids") or [])
    dropped = list(ctx.get("bundle_selector_dropped_ids") or [])
    selected_pool = ctx.get("bundle_selector_selected_pool") or []
    selected_rows = [r for r in selected_pool if isinstance(r, dict)]
    selected_impl = int(
        ctx.get("bundle_selector_selected_impl_body_count")
        or sum(1 for r in selected_rows if r.get("implementation_body_present"))
    )
    selected_linked = int(
        ctx.get("bundle_selector_selected_linked_row_count")
        or sum(
            1
            for r in selected_rows
            if isinstance(r.get("relations"), list) and r.get("relations")
        )
    )
    selected_test = int(
        ctx.get("bundle_selector_selected_test_row_count")
        or sum(1 for r in selected_rows if _is_test_path(str(r.get("file") or "")))
    )
    out = {
        "used": used,
        "skip_reason": ctx.get("bundle_selector_skip_reason") or "",
        "keep_ids": keep_ids,
        "dropped_ids_count": len(dropped),
        "selected_id_count": len(keep_ids),
        "selected_impl_body_count": selected_impl,
        "selected_linked_row_count": selected_linked,
        "selected_test_row_count": selected_test,
        "final_answer_context_from_selected_rows_only": bool(
            ctx.get("final_answer_context_from_selected_rows_only")
        ),
    }
    if ctx.get("bundle_selector_selected_bundle_ids") is not None:
        out["selected_bundle_ids"] = ctx.get("bundle_selector_selected_bundle_ids")
        out["bundle_count"] = ctx.get("bundle_selector_bundle_count", 0)
        out["cross_bundle"] = ctx.get("bundle_selector_cross_bundle", False)
        out["bridge_selected"] = ctx.get("bundle_selector_bridge_selected", False)
    return out


def run_bundle_selector(step: dict, state: "AgentState") -> bool:
    """
    Run selector pass: call LLM, parse output, validate, rebuild ranked_context.
    Returns True if selector succeeded and ranked_context was rebuilt; False on any failure.
    On success, sets state.context["bundle_selector_used"] = True, etc.
    On failure, logs and leaves ranked_context unchanged (fail-soft).
    """
    from config.retrieval_config import BUNDLE_SELECTOR_MAX_KEEP

    pool = (state.context or {}).get("retrieval_candidate_pool")
    if not pool or not isinstance(pool, list):
        return False

    valid_ids = {str(r.get("candidate_id", "")) for r in pool if r.get("candidate_id")}
    if not valid_ids:
        return False

    try:
        from agent.models.model_client import call_small_model
        from agent.prompt_system import get_registry
        from config.retrieval_config import ENABLE_BUNDLE_SELECTION

        from agent.retrieval.bundle_builder import (
            build_candidate_bundles,
            detect_bridge_candidates,
            top_bundles_by_score,
        )

        top_bundles = None
        all_bundles: list[dict] = []
        if ENABLE_BUNDLE_SELECTION:
            all_bundles = build_candidate_bundles(pool)
            detect_bridge_candidates(pool, all_bundles)
            top_bundles = top_bundles_by_score(all_bundles, top_n=3)
            ctx = state.context or {}
            ctx["bundle_selector_bundles"] = top_bundles
            ctx["bundle_selector_all_bundles"] = all_bundles

        system = get_registry().get_instructions("bundle_selector")
        user = build_bundle_selector_payload(step, state, pool, top_bundles=top_bundles)
        raw = call_small_model(
            user,
            task_name="bundle_selector",
            system_prompt=system,
        )
        parsed = parse_bundle_selector_output(raw or "")
        if not parsed:
            logger.warning("[bundle_selector] failed to parse output")
            return False
        keep = parsed.get("keep_ids") or []
        if not keep:
            logger.warning("[bundle_selector] empty keep_ids")
            return False
        # Validate all IDs exist in pool
        invalid = [x for x in keep if x not in valid_ids]
        if invalid:
            logger.warning("[bundle_selector] invalid ids (invented): %s", invalid[:5])
            return False
        # Cap to max_keep
        keep = keep[:BUNDLE_SELECTOR_MAX_KEEP]
        rebuilt = rebuild_ranked_context_from_selected_ids(pool, keep)
        if not rebuilt:
            logger.warning("[bundle_selector] rebuilt context empty")
            return False

        intent = (state.context or {}).get("retrieval_intent") or ""
        forced_link_injection = False
        repair_applied = False

        selected_impl_count = sum(
            1 for r in rebuilt if isinstance(r, dict) and r.get("implementation_body_present")
        )
        selected_linked_count = sum(
            1
            for r in rebuilt
            if isinstance(r, dict)
            and isinstance(r.get("relations"), list)
            and r.get("relations")
        )
        selected_test_count = sum(
            1 for r in rebuilt if isinstance(r, dict) and _is_test_path(r.get("file") or "")
        )

        if intent == INTENT_ARCHITECTURE:
            keep_set = set(keep)

            if selected_linked_count == 0:
                selected_files = {
                    (r.get("file") or "").strip().replace("\\", "/")
                    for r in rebuilt
                    if isinstance(r, dict) and (r.get("file") or "").strip()
                }
                linked_candidates = [
                    r
                    for r in pool
                    if isinstance(r.get("relations"), list)
                    and r.get("relations")
                    and str(r.get("candidate_id", "")) not in keep_set
                ]
                linked_candidates.sort(
                    key=lambda r: (
                        int(_linked_row_connects_to_selected(r, selected_files)),
                        _rank_linked_candidate(r),
                    ),
                    reverse=True,
                )
                if linked_candidates:
                    inject_id = str(linked_candidates[0].get("candidate_id", ""))
                    if inject_id in valid_ids:
                        keep = _dedupe_preserve_order(
                            [inject_id] + [k for k in keep if k != inject_id]
                        )
                        keep_set = set(keep)
                        forced_link_injection = True
                        repair_applied = True

            if selected_impl_count == 0:
                impl_candidates = [
                    r
                    for r in pool
                    if r.get("implementation_body_present")
                    and str(r.get("candidate_id", "")) not in set(keep)
                ]
                if impl_candidates:
                    inject_id = str(impl_candidates[0].get("candidate_id", ""))
                    if inject_id in valid_ids:
                        keep = _dedupe_preserve_order(list(keep) + [inject_id])
                        repair_applied = True

        # Single final rebuild after all injections
        rebuilt = rebuild_ranked_context_from_selected_ids(pool, keep)
        if not rebuilt:
            state.context["bundle_selector_used"] = False
            state.context["bundle_selector_fallback_reason"] = "empty_after_injection"
            return False

        # Recompute counts once from final rebuilt
        selected_impl_count = sum(
            1 for r in rebuilt if isinstance(r, dict) and r.get("implementation_body_present")
        )
        selected_linked_count = sum(
            1
            for r in rebuilt
            if isinstance(r, dict)
            and isinstance(r.get("relations"), list)
            and r.get("relations")
        )
        selected_test_count = sum(
            1 for r in rebuilt if isinstance(r, dict) and _is_test_path(r.get("file") or "")
        )

        # Fallback check after injection attempt
        if intent == INTENT_ARCHITECTURE and selected_linked_count == 0:
            state.context["bundle_selector_used"] = False
            state.context["bundle_selector_forced_link_injection"] = False
            state.context["bundle_selector_fallback_reason"] = "no_linked_rows"
            logger.warning(
                "[bundle_selector] architecture: no linked rows after injection attempt, fallback"
            )
            return False

        # Multi-link preference: prefer 2+ linked rows for architecture (multi-hop structure)
        linked_rows = [
            r for r in rebuilt
            if isinstance(r, dict)
            and isinstance(r.get("relations"), list)
            and r.get("relations")
        ]
        keep_set = set(keep)
        forced_multi_link_injection = False

        if intent == INTENT_ARCHITECTURE and len(linked_rows) == 1:
            selected_files = {
                (r.get("file") or "").strip().replace("\\", "/")
                for r in rebuilt
                if isinstance(r, dict) and (r.get("file") or "").strip()
            }
            remaining_linked = [
                r for r in pool
                if isinstance(r.get("relations"), list)
                and r.get("relations")
                and str(r.get("candidate_id", "")) not in keep_set
            ]
            if remaining_linked:
                remaining_linked.sort(
                    key=lambda r: (
                        int(_linked_row_connects_to_selected(r, selected_files)),
                        _rank_linked_candidate(r),
                    ),
                    reverse=True,
                )
                inject_id = str(remaining_linked[0].get("candidate_id", ""))
                if inject_id in valid_ids:
                    keep = [inject_id] + list(keep)
                    keep = _dedupe_preserve_order(keep)
                    forced_multi_link_injection = True
                    rebuilt = rebuild_ranked_context_from_selected_ids(pool, keep)
                    selected_impl_count = sum(
                        1 for r in rebuilt if isinstance(r, dict) and r.get("implementation_body_present")
                    )
                    selected_linked_count = sum(
                        1
                        for r in rebuilt
                        if isinstance(r, dict)
                        and isinstance(r.get("relations"), list)
                        and r.get("relations")
                    )
                    selected_test_count = sum(
                        1 for r in rebuilt if isinstance(r, dict) and _is_test_path(r.get("file") or "")
                    )

        state.context["bundle_selector_forced_multi_link_injection"] = forced_multi_link_injection

        # Bundle-aware: when ARCH and selection spans 2+ bundles but no bridge, inject bridge
        pool_id_to_row = {str(r.get("candidate_id", "")): r for r in pool if r.get("candidate_id")}
        if (
            ENABLE_BUNDLE_SELECTION
            and intent == INTENT_ARCHITECTURE
            and all_bundles
        ):
            cid_to_bundle: dict[str, str] = {}
            for b in all_bundles:
                for cid in b.get("candidate_ids", []):
                    cid_to_bundle[cid] = b.get("bundle_id", "")
            selected_bundles = {cid_to_bundle.get(c, "") for c in keep if cid_to_bundle.get(c)}
            selected_bundles.discard("")
            bridge_in_selection = any(
                (pool_id_to_row.get(c) or {}).get("is_bridge")
                for c in keep
            )
            if len(selected_bundles) >= 2 and not bridge_in_selection:
                bridge_candidates = [
                    r for r in pool
                    if r.get("is_bridge")
                    and str(r.get("candidate_id", "")) not in set(keep)
                    and str(r.get("candidate_id", "")) in valid_ids
                ]
                if bridge_candidates:
                    bridge_candidates.sort(key=_rank_linked_candidate, reverse=True)
                    inject_id = str(bridge_candidates[0].get("candidate_id", ""))
                    keep = _dedupe_preserve_order([inject_id] + [k for k in keep if k != inject_id])
                    rebuilt = rebuild_ranked_context_from_selected_ids(pool, keep)
                    selected_impl_count = sum(
                        1 for r in rebuilt if isinstance(r, dict) and r.get("implementation_body_present")
                    )
                    selected_linked_count = sum(
                        1
                        for r in rebuilt
                        if isinstance(r, dict)
                        and isinstance(r.get("relations"), list)
                        and r.get("relations")
                    )
                    selected_test_count = sum(
                        1 for r in rebuilt if isinstance(r, dict) and _is_test_path(r.get("file") or "")
                    )

        # Bundle observability
        if ENABLE_BUNDLE_SELECTION and all_bundles:
            cid_to_bundle = {}
            for b in all_bundles:
                for cid in b.get("candidate_ids", []):
                    cid_to_bundle[cid] = b.get("bundle_id", "")
            selected_bundle_ids = list(dict.fromkeys(
                bid for c in keep for bid in (cid_to_bundle.get(c),) if bid
            ))
            bridge_selected = any(
                (pool_id_to_row.get(c) or {}).get("is_bridge") for c in keep
            )
            state.context["bundle_selector_selected_bundle_ids"] = selected_bundle_ids
            state.context["bundle_selector_bundle_count"] = len(selected_bundle_ids)
            state.context["bundle_selector_cross_bundle"] = len(selected_bundle_ids) >= 2
            state.context["bundle_selector_bridge_selected"] = bridge_selected
        else:
            state.context["bundle_selector_selected_bundle_ids"] = []
            state.context["bundle_selector_bundle_count"] = 0
            state.context["bundle_selector_cross_bundle"] = False
            state.context["bundle_selector_bridge_selected"] = False

        violations = _validate_selection_against_constraints(rebuilt, intent)
        state.context["bundle_selector_constraint_violations"] = violations
        state.context["bundle_selector_forced_link_injection"] = forced_link_injection
        state.context["bundle_selector_repair_applied"] = repair_applied

        all_ids = [str(r.get("candidate_id")) for r in pool if r.get("candidate_id")]
        dropped_ids = [cid for cid in all_ids if cid not in set(keep)]

        state.context["bundle_selector_used"] = True
        state.context["bundle_selector_keep_ids"] = list(keep)
        state.context["bundle_selector_primary_ids"] = parsed.get("primary_ids") or []
        state.context["bundle_selector_supporting_ids"] = parsed.get("supporting_ids") or []
        state.context["bundle_selector_reason"] = parsed.get("reason", "")
        state.context["bundle_selector_selected_pool"] = rebuilt
        state.context["bundle_selector_dropped_ids"] = dropped_ids
        state.context["bundle_selector_selected_has_impl"] = selected_impl_count > 0
        state.context["bundle_selector_selected_has_linked"] = selected_linked_count > 0
        state.context["bundle_selector_selected_has_test_files"] = selected_test_count > 0
        state.context["bundle_selector_selected_impl_body_count"] = selected_impl_count
        state.context["bundle_selector_selected_linked_row_count"] = selected_linked_count
        state.context["bundle_selector_selected_test_row_count"] = selected_test_count
        state.context["final_answer_context_from_selected_rows_only"] = True
        state.context["ranked_context"] = rebuilt
        logger.info("[bundle_selector] selected %d ids, rebuilt ranked_context", len(rebuilt))
        return True
    except Exception as e:
        logger.warning("[bundle_selector] selector failed (fail-soft): %s", e)
        return False
