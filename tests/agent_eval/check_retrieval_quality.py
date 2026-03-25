"""
Offline retrieval quality metrics for ranked_context (Phase E).

Run: pytest tests/agent_eval/check_retrieval_quality.py -q

Aggregate + A/B helpers for ENABLE_KIND_AWARE_EXPANSION evals:
  python3 -m tests.agent_eval.run_kind_expansion_ab --output-dir artifacts/kind_expansion_ab
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from agent.retrieval.result_contract import (
    RETRIEVAL_RESULT_TYPE_FILE_HEADER,
    RETRIEVAL_RESULT_TYPE_REGION_BODY,
    RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
)

FAILURE_WEIGHTS = {
    "SUCCESS": 0.0,
    "RETRIEVAL_FAILURE": 0.3,
    "NO_SIGNAL_FAILURE": 0.4,
    "SELECTION_FAILURE": 0.6,
    "EXPLORATION_FAILURE": 0.5,
    "GROUNDING_FAILURE": 0.8,
    "PLANNING_FAILURE": 0.5,
    "PLANNING_LOOP": 1.0,
}

_TEST_PATH_RE = re.compile(r"(/tests/|/test/|(^|/)test_[^/]+\.py$)", re.I)
_log = logging.getLogger(__name__)


def _context_tokens_from_rows(rows: list[dict]) -> list[str]:
    """Local token extraction to avoid private observability imports."""
    tokens: list[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        text = " ".join(
            str(v)
            for v in (
                row.get("file"),
                row.get("symbol"),
                row.get("snippet"),
                row.get("code"),
                row.get("content"),
            )
            if v
        )
        if text:
            tokens.extend(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text.lower()))
    return tokens


def _check_bundle_selector_integrity(ctx: dict, rows: list[dict], selected_id_count: int) -> tuple[bool, list[str]]:
    """
    When bundle_selector_used=True, verify invariants. Returns (ok, failure_reasons).
    """
    failures: list[str] = []
    if not ctx.get("bundle_selector_used"):
        return (True, [])

    keep_ids = list(ctx.get("bundle_selector_keep_ids") or [])
    selected_pool = ctx.get("bundle_selector_selected_pool") or []
    pool = ctx.get("retrieval_candidate_pool") or []
    valid_ids = {str(r.get("candidate_id", "")) for r in pool if r.get("candidate_id")}

    if selected_id_count != len(keep_ids):
        failures.append(f"selected_id_count={selected_id_count} != len(keep_ids)={len(keep_ids)}")

    invalid_keep = [x for x in keep_ids if x not in valid_ids]
    if invalid_keep:
        failures.append(f"keep_ids not in pool: {invalid_keep[:5]}")

    row_ids = {str(r.get("candidate_id", "")) for r in rows if r.get("candidate_id")}
    keep_set = set(keep_ids)
    if row_ids != keep_set or len(rows) != len(keep_ids):
        failures.append(
            f"ranked_context rows do not match selected pool: "
            f"len(rows)={len(rows)} keep_ids={len(keep_ids)} row_ids={len(row_ids)}"
        )

    if failures:
        _log.warning("[bundle_selector] integrity check failed: %s", "; ".join(failures))
    return (len(failures) == 0, failures)


def _compute_ranked_context_breakdown(rows: list[dict]) -> dict[str, Any]:
    """Derive granular counts from final ranked_context for A/B diagnostics."""
    from collections import Counter

    if not rows:
        return {
            "candidate_kind_counts": {},
            "retrieval_result_type_counts": {},
            "implementation_body_present_count": 0,
            "linked_row_count": 0,
            "useful_link_count": 0,
            "isolated_link_count": 0,
            "linked_connects_to_impl": False,
            "symbol_body_count": 0,
            "file_header_count": 0,
            "region_body_count": 0,
            "test_file_row_count": 0,
            "impl_file_row_count": 0,
            "distinct_impl_file_count": 0,
            "top_files": [],
        }

    ck_counts: Counter = Counter()
    rrt_counts: Counter = Counter()
    impl_body = 0
    linked = 0
    symbol_body = 0
    file_header = 0
    region_body = 0
    test_rows = 0
    impl_rows = 0
    file_counter: Counter = Counter()

    for r in rows:
        if not isinstance(r, dict):
            continue
        ck = (r.get("candidate_kind") or "").strip() or "unknown"
        ck_counts[ck] += 1
        rrt = (r.get("retrieval_result_type") or "").strip()
        if rrt:
            rrt_counts[rrt] += 1
        if r.get("implementation_body_present"):
            impl_body += 1
        rels = r.get("relations")
        if isinstance(rels, list) and rels:
            linked += 1
        if rrt == RETRIEVAL_RESULT_TYPE_SYMBOL_BODY:
            symbol_body += 1
        elif rrt == RETRIEVAL_RESULT_TYPE_FILE_HEADER:
            file_header += 1
        elif rrt == RETRIEVAL_RESULT_TYPE_REGION_BODY:
            region_body += 1
        f = (r.get("file") or "").strip()
        if f:
            file_counter[f] += 1
            if _is_test_path(f):
                test_rows += 1
            else:
                impl_rows += 1

    top_files = [p for p, _ in file_counter.most_common(3)]
    distinct_impl_files = sum(1 for f in file_counter if f and not _is_test_path(f))

    # Link usefulness: selected_files = all files in selection
    selected_files = {(r.get("file") or "").strip().replace("\\", "/") for r in rows if isinstance(r, dict) and (r.get("file") or "").strip()}

    def _normalize_path(p: str) -> str:
        return (p or "").strip().replace("\\", "/")

    def _linked_row_connects_to_selected(row: dict) -> bool:
        """True if linked row's relations reference selected files, or row's file is in selection."""
        if not isinstance(row, dict):
            return False
        rels = row.get("relations")
        if not isinstance(rels, list) or not rels:
            return False
        row_file = _normalize_path(row.get("file") or "")
        if row_file and row_file in selected_files:
            return True
        for rel in rels:
            if not isinstance(rel, dict):
                continue
            tf = _normalize_path(rel.get("target_file") or rel.get("file") or "")
            if tf and tf in selected_files:
                return True
            # Substring match for path variations (e.g. "a.py" vs "src/a.py")
            if tf:
                for sf in selected_files:
                    if sf and (tf in sf or sf.endswith("/" + tf) or tf.endswith("/" + sf)):
                        return True
        return False

    useful_link_count = 0
    linked_connects_to_impl = False
    for r in rows:
        if not isinstance(r, dict):
            continue
        rels = r.get("relations")
        if not isinstance(rels, list) or not rels:
            continue
        if _linked_row_connects_to_selected(r):
            useful_link_count += 1
            linked_connects_to_impl = True
    isolated_links = linked - useful_link_count

    return {
        "candidate_kind_counts": dict(ck_counts),
        "retrieval_result_type_counts": dict(rrt_counts),
        "implementation_body_present_count": impl_body,
        "linked_row_count": linked,
        "useful_link_count": useful_link_count,
        "isolated_link_count": isolated_links,
        "linked_connects_to_impl": linked_connects_to_impl,
        "symbol_body_count": symbol_body,
        "file_header_count": file_header,
        "region_body_count": region_body,
        "test_file_row_count": test_rows,
        "impl_file_row_count": impl_rows,
        "distinct_impl_file_count": distinct_impl_files,
        "top_files": top_files,
    }


def _assert_architecture_quality(
    breakdown: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    """
    Architecture-specific quality signals for explain/architecture retrieval tasks.
    Returns dict with: linked_ok, substantive_impl_ok, not_test_dominated, multi_file_ok, architecture_ok.
    """
    linked = int(breakdown.get("linked_row_count") or 0)
    impl_body = int(breakdown.get("implementation_body_present_count") or 0)
    impl_rows = int(breakdown.get("impl_file_row_count") or 0)
    test_rows = int(breakdown.get("test_file_row_count") or 0)
    distinct_impl = int(breakdown.get("distinct_impl_file_count") or 0)

    linked_ok = linked >= 2
    substantive_impl_ok = impl_body >= 1 or impl_rows >= 1
    not_test_dominated = test_rows <= impl_rows if (impl_rows > 0 or test_rows > 0) else True
    multi_hop_tasks = frozenset(("sq_2hop_arch", "sq_entrypoint_arch"))
    multi_file_ok = distinct_impl >= 2 if task_id in multi_hop_tasks else True
    architecture_ok = linked_ok and substantive_impl_ok and not_test_dominated and multi_file_ok

    return {
        "architecture_linked_ok": linked_ok,
        "architecture_substantive_impl_ok": substantive_impl_ok,
        "architecture_not_test_dominated": not_test_dominated,
        "architecture_multi_file_ok": multi_file_ok,
        "architecture_ok": architecture_ok,
    }


def _is_test_path(path: str) -> bool:
    if not path:
        return False
    p = path.replace("\\", "/").lower()
    return bool(_TEST_PATH_RE.search(p)) or p.endswith("conftest.py")


def assert_retrieval_quality(
    final_context: list[dict],
    query: str,
    state_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Compute offline metrics for retrieval quality (deterministic).
    """
    rows = [r for r in (final_context or []) if isinstance(r, dict)]
    state_context = state_context or {}

    has_symbol_or_region_body = any(
        r.get("retrieval_result_type") in (RETRIEVAL_RESULT_TYPE_SYMBOL_BODY, RETRIEVAL_RESULT_TYPE_REGION_BODY)
        for r in rows
    )

    has_non_test_implementation = any(
        r.get("file") and not _is_test_path(str(r.get("file")))
        for r in rows
    )

    relation_count = sum(len(r.get("relations") or []) for r in rows if isinstance(r.get("relations"), list))

    all_kinds_typed = bool(rows) and all(r.get("candidate_kind") for r in rows)

    retrieval_intent = str(state_context.get("retrieval_intent") or "")

    replanner_fired = bool(state_context.get("replanner_fired") or state_context.get("replan_triggered"))

    return {
        "has_symbol_or_region_body": has_symbol_or_region_body,
        "has_non_test_implementation": has_non_test_implementation,
        "relation_count": relation_count,
        "all_kinds_typed": all_kinds_typed,
        "retrieval_intent": retrieval_intent,
        "replanner_fired": replanner_fired,
        "query": query,
    }


def classify_edit_failure(record: dict[str, Any]) -> str | None:
    """
    Classify edit failure stage for root-cause diagnosis (observability only).
    Returns: RETRIEVAL_FAILURE | SELECTION_FAILURE | EDIT_GROUNDING_FAILURE | UNKNOWN | None
    """
    if not record:
        return None
    has_impl_in_pool = record.get("has_impl_in_pool")
    final_has_signal = record.get("final_has_signal")
    answer_supported = record.get("answer_supported")

    if has_impl_in_pool is False:
        return "RETRIEVAL_FAILURE"
    if has_impl_in_pool and final_has_signal is False:
        return "SELECTION_FAILURE"
    if final_has_signal and answer_supported is False:
        return "EDIT_GROUNDING_FAILURE"
    return "UNKNOWN"


def build_retrieval_quality_record(spec: Any, state: Any, loop_out: dict[str, Any] | None) -> dict[str, Any]:
    """
    Compact per-task retrieval metrics for outcome.json (no full ranked_context payload).
    """
    tid = getattr(spec, "task_id", None)
    if state is None:
        return {"task_id": tid, "error": "missing_state"}

    ctx = getattr(state, "context", None) or {}
    if not isinstance(ctx, dict):
        ctx = {}

    rc = ctx.get("ranked_context") or []
    rows = [r for r in rc if isinstance(r, dict)]

    step_results = getattr(state, "step_results", None) or []
    explain_steps = [sr for sr in step_results if getattr(sr, "action", None) == "EXPLAIN"]
    if explain_steps:
        explain_ok = all(bool(getattr(s, "success", False)) for s in explain_steps)
    else:
        explain_ok = bool(ctx.get("explain_success"))

    exec_counts = ctx.get("execution_counts")
    replan_count = 0
    if isinstance(exec_counts, dict):
        try:
            replan_count = int(exec_counts.get("replan_count") or 0)
        except (TypeError, ValueError):
            replan_count = 0

    sc: dict[str, Any] = {
        "retrieval_intent": ctx.get("retrieval_intent"),
        "explain_success": ctx.get("explain_success"),
        "replanner_fired": replan_count > 0,
        "replan_triggered": replan_count > 0,
    }
    base = assert_retrieval_quality(rows, getattr(spec, "instruction", "") or "", sc)
    tags = list(getattr(spec, "tags", ()) or ())
    is_arch = "architecture" in tags
    relation_ok = bool(base["relation_count"] > 0) if is_arch else None

    breakdown = _compute_ranked_context_breakdown(rows)
    rm = ctx.get("retrieval_metrics") or {}
    prune_loss_proxy: int | None = None
    if isinstance(rm.get("candidates_in"), (int, float)) and isinstance(rm.get("candidates_out"), (int, float)):
        prune_loss_proxy = int(rm["candidates_in"]) - int(rm["candidates_out"])

    selector_used = bool(ctx.get("bundle_selector_used"))
    skip_reason = ctx.get("bundle_selector_skip_reason")
    selected_pool = ctx.get("bundle_selector_selected_pool") or []
    selected_rows = [r for r in selected_pool if isinstance(r, dict)]
    selected_ids = list(ctx.get("bundle_selector_keep_ids") or [])
    dropped_ids = list(ctx.get("bundle_selector_dropped_ids") or [])
    selected_impl_count = int(
        ctx.get("bundle_selector_selected_impl_body_count")
        or sum(1 for r in selected_rows if r.get("implementation_body_present"))
    )
    selected_linked_count = int(
        ctx.get("bundle_selector_selected_linked_row_count")
        or sum(1 for r in selected_rows if isinstance(r.get("relations"), list) and r.get("relations"))
    )
    selected_test_count = int(
        ctx.get("bundle_selector_selected_test_row_count")
        or sum(1 for r in selected_rows if _is_test_path(str(r.get("file") or "")))
    )
    selected_top_files = [str(r.get("file") or "") for r in selected_rows[:3] if r.get("file")]

    selector_integrity_ok = True
    if selector_used:
        integ_ok, _ = _check_bundle_selector_integrity(ctx, rows, len(selected_ids))
        selector_integrity_ok = integ_ok

    out: dict[str, Any] = {
        "task_id": tid,
        "tags": tags,
        **base,
        "explain_step_ok": explain_ok,
        "symbol_body_present": base["has_symbol_or_region_body"],
        "impl_bias_ok": base["has_non_test_implementation"],
        "relation_ok": relation_ok,
        "final_context_count": len(rows),
        "final_context_chars": sum(len(str(r)) for r in rows),
        "replanner_triggered": replan_count > 0,
        "replan_count": replan_count,
        **breakdown,
        "prune_loss_proxy": prune_loss_proxy,
        "bundle_selector_used": selector_used,
        "bundle_selector_skip_reason": skip_reason if skip_reason is not None else "",
        "selected_id_count": len(selected_ids),
        "selected_impl_body_count": selected_impl_count,
        "selected_linked_row_count": selected_linked_count,
        "selected_test_row_count": selected_test_count,
        "selected_top_files": selected_top_files,
        "bundle_selector_dropped_id_count": len(dropped_ids),
        "final_answer_context_from_selected_rows_only": bool(
            ctx.get("final_answer_context_from_selected_rows_only")
        ),
        "selector_integrity_ok": selector_integrity_ok,
    }
    # Exploration metrics
    if ctx.get("exploration_used"):
        out["exploration_used"] = True
        out["exploration_added_count"] = int(ctx.get("exploration_added_count") or 0)
        out["exploration_structure_gain"] = int(ctx.get("exploration_structure_gain") or 0)
        out["exploration_steps_used"] = int(ctx.get("exploration_steps_used") or 0)
        out["exploration_helped"] = bool(ctx.get("exploration_helped"))
        out["exploration_improved_structure"] = bool(ctx.get("exploration_improved_structure"))
        out["exploration_linked_gain"] = int(ctx.get("exploration_linked_gain") or 0)
        expl_debug = ctx.get("exploration_debug") or {}
        out["exploration_used_new_token_count"] = int(expl_debug.get("used_new_token_count") or 0)
    else:
        out["exploration_used"] = False
        out["exploration_added_count"] = 0
        out["exploration_structure_gain"] = 0
        out["exploration_steps_used"] = 0
        out["exploration_helped"] = False
        out["exploration_improved_structure"] = False
        out["exploration_linked_gain"] = 0
        out["exploration_used_new_token_count"] = 0
    if selector_used or (skip_reason or "").strip():
        from agent.retrieval.bundle_selector import build_bundle_selector_observability_summary

        out["bundle_selector_observability"] = build_bundle_selector_observability_summary(ctx)
    if is_arch and tid:
        out.update(_assert_architecture_quality(breakdown, str(tid)))

    # Selector-quality: architecture_answer_ready
    if is_arch:
        linked = int(breakdown.get("linked_row_count") or 0)
        impl = int(breakdown.get("implementation_body_present_count") or 0)
        impl_rows = int(breakdown.get("impl_file_row_count") or 0)
        test_rows = int(breakdown.get("test_file_row_count") or 0)
        out["architecture_answer_ready"] = (
            linked >= 1 and impl >= 1 and (test_rows <= impl_rows if (impl_rows > 0 or test_rows > 0) else True)
        )
    else:
        out["architecture_answer_ready"] = None

    # architecture_safe_selection: arch tasks with selector must have linked>=1 and impl>=1
    if is_arch and selector_used:
        out["architecture_safe_selection"] = (
            selected_linked_count >= 1 and selected_impl_count >= 1
        )
    else:
        out["architecture_safe_selection"] = None

    # structure_score: linked + distinct_impl_files + (1 if linked rows connect to impl else 0)
    if selector_used:
        distinct_impl_files = int(breakdown.get("distinct_impl_file_count") or 0)
        linked_connects = bool(breakdown.get("linked_connects_to_impl"))
        out["structure_score"] = selected_linked_count + distinct_impl_files + (1 if linked_connects else 0)
        out["useful_link_count"] = int(breakdown.get("useful_link_count") or 0)
        out["isolated_link_count"] = int(breakdown.get("isolated_link_count") or 0)
    else:
        out["structure_score"] = None
        out["useful_link_count"] = None
        out["isolated_link_count"] = None

    # Bundle metrics (when ENABLE_BUNDLE_SELECTION / bundle context available)
    selected_bundle_ids = ctx.get("bundle_selector_selected_bundle_ids")
    if selected_bundle_ids is not None:
        bundle_count = len(selected_bundle_ids)
        all_bundles = ctx.get("bundle_selector_all_bundles") or []
        cid_to_bundle: dict[str, str] = {}
        for b in all_bundles:
            for cid in b.get("candidate_ids", []):
                cid_to_bundle[cid] = b.get("bundle_id", "")
        selected_in_bundles: dict[str, int] = {}
        for cid in selected_ids:
            bid = cid_to_bundle.get(cid)
            if bid:
                selected_in_bundles[bid] = selected_in_bundles.get(bid, 0) + 1
        out["bundle_count_selected"] = bundle_count
        out["largest_bundle_size"] = (
            max(selected_in_bundles.values()) if selected_in_bundles else 0
        )
        dominant_count = max(selected_in_bundles.values()) if selected_in_bundles else 0
        out["bundle_coherence_score"] = (
            dominant_count / len(selected_ids) if selected_ids else 1.0
        )
        out["bridge_usage_rate"] = bool(ctx.get("bundle_selector_bridge_selected"))
    else:
        out["bundle_count_selected"] = None
        out["largest_bundle_size"] = None
        out["bundle_coherence_score"] = None
        out["bridge_usage_rate"] = None

    # multi_hop_satisfied: linked>=2 AND distinct_files>=2 (architecture)
    distinct_impl_for_mh = int(breakdown.get("distinct_impl_file_count") or 0)
    out["multi_hop_satisfied"] = (
        selected_linked_count >= 2 and distinct_impl_for_mh >= 2
        if (selector_used and is_arch)
        else None
    )

    # Phase 5: Patch validation debug for RCA (stale context vs patch quality)
    out["patch_debug"] = ctx.get("patch_validation_debug")

    # Search debug records (stage-wise SEARCH audit)
    search_debug_records = ctx.get("search_debug_records")
    if search_debug_records is not None:
        out["search_debug_records"] = search_debug_records
        # For failure attribution: retrieval_empty, pool_has_signal from last record
        sdr = [r for r in search_debug_records if isinstance(r, dict)]
        if sdr:
            last_rec = sdr[-1]
            out["retrieval_empty"] = last_rec.get("retrieval_empty")
            out["pool_has_signal"] = last_rec.get("pool_has_signal")
        else:
            out["retrieval_empty"] = None
            out["pool_has_signal"] = None
    else:
        out["retrieval_empty"] = None
        out["pool_has_signal"] = None

    # Attribution signals from loop_out and state
    if loop_out is not None and isinstance(loop_out, dict):
        errors = loop_out.get("errors_encountered") or []
        out["errors"] = list(errors) if isinstance(errors, list) else [str(errors)]
        out["task_success"] = len(out["errors"]) == 0
        out["terminal"] = loop_out.get("terminal")
        et = loop_out.get("edit_telemetry") or {}
        out["edit_failure_reason"] = et.get("edit_failure_reason")
    else:
        out["errors"] = []
        out["task_success"] = True
        out["terminal"] = None
        out["edit_failure_reason"] = None
    out["termination_reason"] = ctx.get("termination_reason") or out.get("terminal")

    # Failure attribution (always present, runner-independent)
    from agent.meta.failure_attribution import ensure_failure_reason

    ensure_failure_reason(out, task_id=str(tid) if tid else None)

    # NOTE:
    # overlap_score is a lexical diagnostic signal only.
    # It must NOT be used for correctness decisions.
    # Use answer_supported for grounding truth.

    grounding = ctx.get("grounding_debug") or {}
    exploration = ctx.get("exploration_debug") or {}
    final_context_tokens = len(_context_tokens_from_rows(rows))
    out["final_has_signal"] = (
        (breakdown.get("implementation_body_present_count") or 0) >= 1
        or (breakdown.get("linked_row_count") or 0) >= 1
    )
    out.update({
        "overlap_score": grounding.get("overlap_score"),
        "overlap_count": grounding.get("overlap_count"),
        "exploration_new_token_count": exploration.get("new_token_count"),
        "exploration_used_new_token_count": exploration.get("used_new_token_count"),
        "exploration_effective": exploration.get("exploration_effective"),
        "final_context_tokens": final_context_tokens,
    })

    eval_res = ctx.get("answer_grounding_eval") or {}
    out.update({
        "answer_supported": eval_res.get("supported"),
        "support_strength": eval_res.get("support_strength"),
    })
    supported = eval_res.get("supported")
    if supported is True:
        out["grounding_status"] = "supported"
    elif supported is False:
        out["grounding_status"] = "unsupported"
    else:
        out["grounding_status"] = "unknown"

    # Phase 1: per-task diagnostics for edit failure root-cause analysis
    pool = ctx.get("retrieval_candidate_pool") or []
    pool_has_signal = out.get("pool_has_signal")
    if pool:
        has_impl_in_pool = any(r.get("implementation_body_present") for r in pool if isinstance(r, dict))
        has_linked_in_pool = any(
            isinstance(r.get("relations"), list) and r.get("relations")
            for r in pool
            if isinstance(r, dict)
        )
    else:
        has_impl_in_pool = pool_has_signal if pool_has_signal is not None else out.get("final_has_signal")
        has_linked_in_pool = None

    sdr = [r for r in (ctx.get("search_debug_records") or []) if isinstance(r, dict)]
    selection_loss = sdr[-1].get("selection_loss") if sdr else None
    if selection_loss is None:
        selection_loss = bool(has_impl_in_pool and not out.get("final_has_signal"))
    exploration_used = bool(out.get("exploration_used"))
    exploration_effective = out.get("exploration_effective") if exploration_used else False

    out["diagnostics"] = {
        "has_impl_in_pool": has_impl_in_pool,
        "has_linked_in_pool": has_linked_in_pool,
        "final_has_signal": out.get("final_has_signal"),
        "selection_loss": selection_loss,
        "exploration_used": exploration_used,
        "exploration_effective": exploration_effective,
    }
    out["has_impl_in_pool"] = has_impl_in_pool
    out["selection_loss"] = selection_loss

    # Phase 2: failure classification (diagnostic only)
    out["edit_failure_stage"] = classify_edit_failure(out)

    # Phase 4: per-task diagnostics logging
    stage = out.get("edit_failure_stage")
    _log.info(
        "[diagnostics] task=%s stage=%s impl=%s final_signal=%s selection_loss=%s",
        tid,
        stage,
        has_impl_in_pool,
        out.get("final_has_signal"),
        selection_loss,
    )

    return out


def _mean_bool(rows: list[dict[str, Any]], key: str, pred: Any) -> float | None:
    sub = [r for r in rows if pred(r)]
    if not sub:
        return None
    return sum(1 for r in sub if r.get(key)) / len(sub)


def _merge_count_dicts(rows: list[dict], key: str) -> dict[str, int]:
    """Merge per-task count dicts into one aggregated dict."""
    from collections import Counter

    merged: Counter = Counter()
    for r in rows:
        val = r.get(key)
        if isinstance(val, dict):
            for k, v in val.items():
                if isinstance(v, (int, float)):
                    merged[str(k)] += int(v)
    return dict(merged)


def aggregate_search_failure_modes(records: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate search_debug_records for stage-wise failure diagnosis.
    Flattens all search_debug records across tasks.
    Optional: correlation with query_score when records have it (from search_query_audit).
    """
    flat: list[dict] = []
    for r in records:
        sdr = r.get("search_debug_records")
        if isinstance(sdr, list):
            flat.extend(s for s in sdr if isinstance(s, dict))
    if not flat:
        return {
            "search_debug_record_count": 0,
            "retrieval_empty_rate": None,
            "pool_weak_rate": None,
            "selection_loss_rate": None,
            "avg_query_score": None,
            "avg_query_score_when_retrieval_empty": None,
            "avg_query_score_when_selection_loss": None,
        }
    n = len(flat)
    retrieval_empty = sum(1 for s in flat if s.get("retrieval_empty"))
    pool_weak = sum(1 for s in flat if s.get("pool_has_signal") is False)
    selection_loss = sum(1 for s in flat if s.get("selection_loss"))
    out: dict[str, Any] = {
        "search_debug_record_count": n,
        "retrieval_empty_rate": retrieval_empty / n if n else None,
        "pool_weak_rate": pool_weak / n if n else None,
        "selection_loss_rate": selection_loss / n if n else None,
    }
    scores = [s.get("query_score") for s in flat if s.get("query_score") is not None]
    if scores:
        out["avg_query_score"] = sum(scores) / len(scores)
        empty_scores = [s.get("query_score") for s in flat if s.get("retrieval_empty") and s.get("query_score") is not None]
        loss_scores = [s.get("query_score") for s in flat if s.get("selection_loss") and s.get("query_score") is not None]
        out["avg_query_score_when_retrieval_empty"] = sum(empty_scores) / len(empty_scores) if empty_scores else None
        out["avg_query_score_when_selection_loss"] = sum(loss_scores) / len(loss_scores) if loss_scores else None
    else:
        out["avg_query_score"] = None
        out["avg_query_score_when_retrieval_empty"] = None
        out["avg_query_score_when_selection_loss"] = None
    return out


def aggregate_retrieval_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize per-task retrieval_quality records (e.g. from one agent_eval run).
    Excludes timeout tasks from averages; counts them separately."""
    all_rows = [r for r in records if isinstance(r, dict) and not r.get("error")]
    timeout_count = sum(1 for r in all_rows if r.get("timeout"))
    rows = [r for r in all_rows if not r.get("timeout")]
    if not rows:
        return {
            "task_count": 0,
            "timeout_count": timeout_count,
            "explain_success_rate": None,
            "symbol_body_present_rate": None,
            "impl_bias_ok_rate": None,
            "relation_ok_rate": None,
            "all_kinds_typed_rate": None,
            "average_final_context_count": None,
            "average_final_context_chars": None,
            "replanner_trigger_rate": None,
            "average_implementation_body_present_count": None,
            "average_linked_row_count": None,
            "average_symbol_body_count": None,
            "average_file_header_count": None,
            "average_region_body_count": None,
            "average_test_file_row_count": None,
            "average_impl_file_row_count": None,
            "average_distinct_impl_file_count": None,
            "average_prune_loss_proxy": None,
            "architecture_task_count": 0,
            "architecture_ok_rate": None,
            "aggregate_candidate_kind_counts": {},
            "aggregate_retrieval_result_type_counts": {},
            "bundle_selector_usage_rate": None,
            "average_selected_id_count": None,
            "average_selected_impl_body_count": None,
            "average_selected_linked_row_count": None,
            "average_selected_test_row_count": None,
            "selected_rows_only_rate": None,
            "architecture_answer_ready_rate": None,
            "selector_integrity_rate": None,
            "architecture_safe_selection_rate": None,
            "average_structure_score": None,
            "average_bundle_coherence_score": None,
            "average_bridge_usage_rate": None,
            "multi_hop_satisfied_rate": None,
            "exploration_used_rate": None,
            "average_exploration_structure_gain": None,
            "average_exploration_linked_gain": None,
            "exploration_helped_rate": None,
            "avg_overlap_score": None,
            "exploration_effective_rate": None,
            "avg_used_new_token_count": None,
            "supported_answer_rate": None,
            "average_support_strength": None,
            "evaluator_coverage_rate": None,
            "unsupported_with_signal_rate": None,
            "unsupported_without_signal_rate": None,
            "weak_support_rate": None,
            "timeout_count": timeout_count,
            "agent_health": {
                "overall_score": None,
                "weighted_failure": None,
                "subscores": {
                    "exploration": None,
                    "grounding": None,
                    "planning": None,
                    "retrieval": None,
                    "selection": None,
                },
                "unknown_failure_reasons": [],
                "attribution_coverage": None,
            },
        }

    n = len(rows)
    avg_count = sum(int(r.get("final_context_count") or 0) for r in rows) / n
    avg_chars = sum(int(r.get("final_context_chars") or 0) for r in rows) / n

    def _avg(k: str) -> float:
        return sum(int(r.get(k) or 0) for r in rows) / n

    prune_vals = [r["prune_loss_proxy"] for r in rows if r.get("prune_loss_proxy") is not None]
    avg_prune = sum(prune_vals) / len(prune_vals) if prune_vals else None

    # Agent health score: attribution-driven, no fallbacks, no corrections
    from collections import Counter

    total = len(rows)
    valid_rows = [
        r for r in rows
        if r.get("failure_reason") in FAILURE_WEIGHTS
    ]
    valid_total = len(valid_rows)
    counts = Counter(r.get("failure_reason") for r in valid_rows)

    unknown_reasons = sorted(
        set(r.get("failure_reason") for r in rows if r.get("failure_reason"))
        - set(FAILURE_WEIGHTS)
    )
    missing_count = sum(1 for r in rows if not r.get("failure_reason"))
    coverage = (total - missing_count) / total if total else None

    weighted_failure = (
        sum(counts[k] * FAILURE_WEIGHTS[k] for k in counts) / valid_total
    ) if valid_total else None
    overall_score = (
        1.0 - weighted_failure
    ) if weighted_failure is not None else None

    retrieval_failures = ["RETRIEVAL_FAILURE", "NO_SIGNAL_FAILURE"]
    selection_failures = ["SELECTION_FAILURE"]
    exploration_failures = ["EXPLORATION_FAILURE"]
    grounding_failures = ["GROUNDING_FAILURE"]
    planning_failures = ["PLANNING_FAILURE", "PLANNING_LOOP"]

    def _rate(keys):
        return sum(counts.get(k, 0) for k in keys) / valid_total if valid_total else None

    subscores = {
        "retrieval": 1 - _rate(retrieval_failures) if valid_total else None,
        "selection": 1 - _rate(selection_failures) if valid_total else None,
        "exploration": 1 - _rate(exploration_failures) if valid_total else None,
        "grounding": 1 - _rate(grounding_failures) if valid_total else None,
        "planning": 1 - _rate(planning_failures) if valid_total else None,
    }
    subscores = dict(sorted(subscores.items()))

    agent_health = {
        "overall_score": overall_score,
        "weighted_failure": weighted_failure,
        "subscores": subscores,
        "unknown_failure_reasons": unknown_reasons,
        "attribution_coverage": coverage,
    }

    result: dict[str, Any] = {
        "task_count": n,
        "timeout_count": timeout_count,
        "explain_success_rate": _mean_bool(rows, "explain_step_ok", lambda _: True),
        "symbol_body_present_rate": _mean_bool(rows, "symbol_body_present", lambda _: True),
        "impl_bias_ok_rate": _mean_bool(rows, "impl_bias_ok", lambda r: "implementation" in (r.get("tags") or [])),
        "relation_ok_rate": _mean_bool(rows, "relation_ok", lambda r: "architecture" in (r.get("tags") or [])),
        "all_kinds_typed_rate": _mean_bool(rows, "all_kinds_typed", lambda _: True),
        "average_final_context_count": avg_count,
        "average_final_context_chars": avg_chars,
        "replanner_trigger_rate": _mean_bool(rows, "replanner_triggered", lambda _: True),
        "average_implementation_body_present_count": _avg("implementation_body_present_count"),
        "average_linked_row_count": _avg("linked_row_count"),
        "average_symbol_body_count": _avg("symbol_body_count"),
        "average_file_header_count": _avg("file_header_count"),
        "average_region_body_count": _avg("region_body_count"),
        "average_test_file_row_count": _avg("test_file_row_count"),
        "average_impl_file_row_count": _avg("impl_file_row_count"),
        "average_distinct_impl_file_count": _avg("distinct_impl_file_count"),
        "average_prune_loss_proxy": avg_prune,
        "aggregate_candidate_kind_counts": _merge_count_dicts(rows, "candidate_kind_counts"),
        "aggregate_retrieval_result_type_counts": _merge_count_dicts(rows, "retrieval_result_type_counts"),
        "architecture_task_count": sum(1 for r in rows if "architecture" in (r.get("tags") or [])),
        "architecture_ok_rate": _mean_bool(rows, "architecture_ok", lambda r: "architecture" in (r.get("tags") or [])),
        "bundle_selector_usage_rate": _mean_bool(rows, "bundle_selector_used", lambda _: True),
        "average_selected_id_count": _avg("selected_id_count"),
        "average_selected_impl_body_count": _avg("selected_impl_body_count"),
        "average_selected_linked_row_count": _avg("selected_linked_row_count"),
        "average_selected_test_row_count": _avg("selected_test_row_count"),
        "selected_rows_only_rate": _mean_bool(rows, "final_answer_context_from_selected_rows_only", lambda _: True),
        "architecture_answer_ready_rate": _mean_bool(
            rows, "architecture_answer_ready", lambda r: "architecture" in (r.get("tags") or [])
        ),
        "selector_integrity_rate": _mean_bool(
            rows, "selector_integrity_ok", lambda r: bool(r.get("bundle_selector_used"))
        ),
        "architecture_safe_selection_rate": _mean_bool(
            rows, "architecture_safe_selection",
            lambda r: "architecture" in (r.get("tags") or []) and bool(r.get("bundle_selector_used")),
        ),
        "average_structure_score": (
            sum(r["structure_score"] for r in rows if r.get("structure_score") is not None)
            / len([r for r in rows if r.get("structure_score") is not None])
            if [r for r in rows if r.get("structure_score") is not None]
            else None
        ),
        "average_bundle_coherence_score": (
            sum(r["bundle_coherence_score"] for r in rows if r.get("bundle_coherence_score") is not None)
            / len([r for r in rows if r.get("bundle_coherence_score") is not None])
            if [r for r in rows if r.get("bundle_coherence_score") is not None]
            else None
        ),
        "average_bridge_usage_rate": _mean_bool(
            rows, "bridge_usage_rate",
            lambda r: r.get("bridge_usage_rate") is not None,
        ),
        "multi_hop_satisfied_rate": _mean_bool(
            rows, "multi_hop_satisfied",
            lambda r: r.get("multi_hop_satisfied") is not None and "architecture" in (r.get("tags") or []),
        ),
        "exploration_used_rate": _mean_bool(rows, "exploration_used", lambda _: True),
        "average_exploration_structure_gain": (
            sum(int(r.get("exploration_structure_gain") or 0) for r in rows) / n
            if n else 0
        ),
        "average_exploration_linked_gain": (
            sum(int(r.get("exploration_linked_gain") or 0) for r in rows) / n
            if n else 0
        ),
        "exploration_helped_rate": (
            _mean_bool(rows, "exploration_helped", lambda r: bool(r.get("exploration_used")))
            if any(r.get("exploration_used") for r in rows)
            else None
        ),
        "avg_overlap_score": (
            sum(v for r in rows if (v := r.get("overlap_score")) is not None) / len([r for r in rows if r.get("overlap_score") is not None])
            if [r for r in rows if r.get("overlap_score") is not None]
            else None
        ),
        "exploration_effective_rate": (
            _mean_bool(rows, "exploration_effective", lambda r: bool(r.get("exploration_used")))
            if any(r.get("exploration_used") for r in rows)
            else None
        ),
        "avg_used_new_token_count": (
            sum(int(r.get("exploration_used_new_token_count") or 0) for r in rows if r.get("exploration_used"))
            / len([r for r in rows if r.get("exploration_used")])
            if [r for r in rows if r.get("exploration_used")]
            else None
        ),
        "supported_answer_rate": (
            sum(1 for r in rows if r.get("answer_supported") is True)
            / len(supported_rows)
            if (supported_rows := [r for r in rows if r.get("answer_supported") is not None])
            else None
        ),
        "average_support_strength": (
            sum(strength_vals) / len(strength_vals)
            if (strength_vals := [r.get("support_strength") for r in rows if isinstance(r.get("support_strength"), int)])
            else None
        ),
        "evaluator_coverage_rate": (
            len([r for r in rows if r.get("answer_supported") is not None]) / len(rows)
            if rows else None
        ),
        "unsupported_with_signal_rate": (
            len([r for r in rows if r.get("answer_supported") is False and r.get("final_has_signal") is True])
            / len(rows)
            if rows else None
        ),
        "unsupported_without_signal_rate": (
            len([r for r in rows if r.get("answer_supported") is False and not r.get("final_has_signal")])
            / len(rows)
            if rows else None
        ),
        "weak_support_rate": (
            len([r for r in rows if isinstance(r.get("support_strength"), int) and r.get("support_strength") <= 2])
            / len(rows)
            if rows else None
        ),
        "agent_health": agent_health,
    }
    sfm = aggregate_search_failure_modes(rows)
    if sfm.get("search_debug_record_count", 0) > 0:
        result["search_failure_modes"] = {
            "retrieval_empty_rate": sfm["retrieval_empty_rate"],
            "pool_weak_rate": sfm["pool_weak_rate"],
            "selection_loss_rate": sfm["selection_loss_rate"],
            "avg_query_score": sfm["avg_query_score"],
            "avg_query_score_when_retrieval_empty": sfm["avg_query_score_when_retrieval_empty"],
            "avg_query_score_when_selection_loss": sfm["avg_query_score_when_selection_loss"],
        }

    # Phase 3: edit failure stage histogram (for root-cause diagnosis)
    stages = [r.get("edit_failure_stage") for r in rows if r.get("edit_failure_stage")]
    result["edit_failure_stage_histogram"] = dict(Counter(stages))

    return result


_DELTA_KEYS = (
    "explain_success_rate",
    "symbol_body_present_rate",
    "impl_bias_ok_rate",
    "relation_ok_rate",
    "all_kinds_typed_rate",
    "average_final_context_count",
    "average_final_context_chars",
    "replanner_trigger_rate",
    "average_implementation_body_present_count",
    "average_linked_row_count",
    "average_symbol_body_count",
    "average_file_header_count",
    "average_region_body_count",
    "average_test_file_row_count",
    "average_impl_file_row_count",
    "average_distinct_impl_file_count",
    "average_prune_loss_proxy",
    "architecture_ok_rate",
    "bundle_selector_usage_rate",
    "average_selected_id_count",
    "average_selected_impl_body_count",
    "average_selected_linked_row_count",
    "average_selected_test_row_count",
    "selected_rows_only_rate",
    "architecture_answer_ready_rate",
    "selected_impl_retention_rate",
    "selected_linked_retention_rate",
    "selected_test_drift_rate",
    "average_selector_vs_baseline_context_delta",
    "architecture_safe_selection_rate",
    "average_structure_score",
    "average_bundle_coherence_score",
    "average_bridge_usage_rate",
    "multi_hop_satisfied_rate",
    "exploration_used_rate",
    "average_exploration_structure_gain",
    "average_exploration_linked_gain",
    "exploration_helped_rate",
    "avg_overlap_score",
    "exploration_effective_rate",
    "avg_used_new_token_count",
    "supported_answer_rate",
    "average_support_strength",
    "evaluator_coverage_rate",
    "unsupported_with_signal_rate",
    "unsupported_without_signal_rate",
    "weak_support_rate",
)


def compare_ab_runs(
    off_agg: dict[str, Any],
    on_agg: dict[str, Any],
    *,
    label_off: str = "ENABLE_KIND_AWARE_EXPANSION=0",
    label_on: str = "ENABLE_KIND_AWARE_EXPANSION=1",
    rec_off: list[dict[str, Any]] | None = None,
    rec_on: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pairwise deltas (on - off) for numeric aggregate fields. When rec_off/rec_on provided, adds selector-quality aggregates."""
    deltas: dict[str, Any] = {}
    agg_off = dict(off_agg)
    agg_on = dict(on_agg)
    if rec_off is not None and rec_on is not None:
        sq = compute_selector_quality_aggregates(rec_off, rec_on)
        for k, v in sq.items():
            agg_on[k] = v
    for k in _DELTA_KEYS:
        a, b = agg_off.get(k), agg_on.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            deltas[k] = b - a
        elif a is None and isinstance(b, (int, float)):
            deltas[k] = b
        else:
            deltas[k] = None
    return {
        "label_off": label_off,
        "label_on": label_on,
        "aggregate_off": agg_off,
        "aggregate_on": agg_on,
        "deltas_on_minus_off": deltas,
    }


def selector_quality_verdict(
    off_agg: dict[str, Any],
    on_agg: dict[str, Any],
) -> str:
    """
    Selector quality verdict: promote_candidate | neutral | regress_linking | regress_impl | regress_test_drift.
    """
    if (off_agg.get("task_count") or 0) < 1 or (on_agg.get("task_count") or 0) < 1:
        return "neutral"
    arch_off = off_agg.get("architecture_ok_rate")
    arch_on = on_agg.get("architecture_ok_rate")
    impl_ret = on_agg.get("selected_impl_retention_rate")
    linked_ret = on_agg.get("selected_linked_retention_rate")
    test_drift = on_agg.get("selected_test_drift_rate")
    eps = 1e-6
    # Check regressions first
    if linked_ret is not None and linked_ret < 0.3:
        return "regress_linking"
    if impl_ret is not None and impl_ret < 0.3:
        return "regress_impl"
    if test_drift is not None and test_drift > 0.5:
        return "regress_test_drift"
    safe_on = on_agg.get("architecture_safe_selection_rate")
    safe_off = off_agg.get("architecture_safe_selection_rate")
    arch_safe_ok = safe_on is None or safe_off is None or safe_on >= safe_off - eps
    # Promote: ON keeps >= baseline arch, arch_safe_selection ok, both retentions >= 0.5, no test drift
    if arch_on is not None and arch_off is not None and arch_on >= arch_off - eps and arch_safe_ok:
        if (impl_ret is None or impl_ret >= 0.5 - eps) and (linked_ret is None or linked_ret >= 0.5 - eps):
            if test_drift is None or test_drift <= 0 + eps:
                return "promote_candidate"
    return "neutral"


def verdict_kind_expansion_step1(off_agg: dict[str, Any], on_agg: dict[str, Any]) -> str:
    """
    Conservative Step-1 gate for production rollout notes (offline metrics only).
    Returns: stay_off | canary_worthy | inconclusive
    """
    if (off_agg.get("task_count") or 0) < 1 or (on_agg.get("task_count") or 0) < 1:
        return "inconclusive"

    primary = (
        "explain_success_rate",
        "symbol_body_present_rate",
        "impl_bias_ok_rate",
        "relation_ok_rate",
    )
    improved = 0
    worsened = 0
    for k in primary:
        a, b = off_agg.get(k), on_agg.get(k)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            continue
        if b > a + 1e-9:
            improved += 1
        elif b < a - 1e-9:
            worsened += 1

    ac0 = off_agg.get("average_final_context_chars")
    ac1 = on_agg.get("average_final_context_chars")
    context_surge = False
    if isinstance(ac0, (int, float)) and isinstance(ac1, (int, float)) and ac0 > 0:
        context_surge = (ac1 - ac0) / ac0 > 0.25

    if worsened >= 2 or context_surge:
        return "stay_off"
    if improved >= 1 and worsened == 0 and not context_surge:
        return "canary_worthy"
    return "inconclusive"


def diagnostic_verdict_kind_expansion(
    off_agg: dict[str, Any],
    on_agg: dict[str, Any],
) -> dict[str, Any]:
    """
    Diagnostic verdict explaining WHY ON differs from OFF (not just pass/fail).
    Returns dict with: substantive_context_improved, linked_arch_improved,
    test_only_drift, prune_loss_direction, summary.
    """
    eps = 1e-9
    out: dict[str, Any] = {
        "substantive_context_improved": None,
        "linked_arch_improved": None,
        "test_only_drift": None,
        "prune_loss_direction": None,
        "summary": "",
    }
    if (off_agg.get("task_count") or 0) < 1 or (on_agg.get("task_count") or 0) < 1:
        out["summary"] = "insufficient_tasks"
        return out

    impl0 = off_agg.get("average_implementation_body_present_count")
    impl1 = on_agg.get("average_implementation_body_present_count")
    if isinstance(impl0, (int, float)) and isinstance(impl1, (int, float)):
        out["substantive_context_improved"] = impl1 > impl0 + eps

    linked0 = off_agg.get("average_linked_row_count")
    linked1 = on_agg.get("average_linked_row_count")
    if isinstance(linked0, (int, float)) and isinstance(linked1, (int, float)):
        out["linked_arch_improved"] = linked1 > linked0 + eps

    test0 = off_agg.get("average_test_file_row_count")
    test1 = on_agg.get("average_test_file_row_count")
    impl_rows0 = off_agg.get("average_impl_file_row_count")
    impl_rows1 = on_agg.get("average_impl_file_row_count")
    if (
        isinstance(test0, (int, float))
        and isinstance(test1, (int, float))
        and isinstance(impl_rows0, (int, float))
        and isinstance(impl_rows1, (int, float))
    ):
        test_delta = test1 - test0
        impl_delta = impl_rows1 - impl_rows0
        out["test_only_drift"] = test_delta > impl_delta + eps if (test_delta > 0 or impl_delta < 0) else False

    p0 = off_agg.get("average_prune_loss_proxy")
    p1 = on_agg.get("average_prune_loss_proxy")
    if isinstance(p0, (int, float)) and isinstance(p1, (int, float)):
        if p1 > p0 + eps:
            out["prune_loss_direction"] = "increased"
        elif p1 < p0 - eps:
            out["prune_loss_direction"] = "decreased"
        else:
            out["prune_loss_direction"] = "unchanged"
    else:
        out["prune_loss_direction"] = "unknown"

    parts = []
    if out["substantive_context_improved"] is True:
        parts.append("substantive+")
    elif out["substantive_context_improved"] is False and impl0 is not None and impl1 is not None and impl1 < impl0 - eps:
        parts.append("substantive-")
    if out["linked_arch_improved"] is True:
        parts.append("linked_arch+")
    elif out["linked_arch_improved"] is False:
        parts.append("linked_arch=")
    if out["test_only_drift"]:
        parts.append("test_drift+")
    if out["prune_loss_direction"] == "increased":
        parts.append("prune_loss+")
    elif out["prune_loss_direction"] == "decreased":
        parts.append("prune_loss-")
    out["summary"] = "; ".join(parts) if parts else "neutral"
    return out


def build_per_task_diffs(
    rec_off: list[dict[str, Any]],
    rec_on: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build per-task OFF vs ON diff for retrieval_quality tasks (matched by task_id)."""
    from tests.agent_eval.suites.search_stack import architecture_task_ids

    off_by_id = {r.get("task_id"): r for r in rec_off if r.get("task_id")}
    out: list[dict[str, Any]] = []
    for r_on in rec_on:
        tid = r_on.get("task_id")
        r_off = off_by_id.get(tid) if tid else None
        if not r_off:
            continue
        diff: dict[str, Any] = {"task_id": tid}
        for k in (
            "final_context_count",
            "relation_count",
            "implementation_body_present_count",
            "linked_row_count",
            "symbol_body_count",
            "file_header_count",
            "region_body_count",
            "test_file_row_count",
            "impl_file_row_count",
            "distinct_impl_file_count",
            "architecture_ok",
        ):
            v0 = r_off.get(k)
            v1 = r_on.get(k)
            if isinstance(v0, (int, float)) and isinstance(v1, (int, float)):
                diff[f"{k}_off"] = v0
                diff[f"{k}_on"] = v1
                diff[f"{k}_delta"] = v1 - v0
        # Selector-quality per-task metrics
        ctx_off = r_off.get("final_context_count") or 0
        sel_on = r_on.get("selected_id_count") or 0
        diff["selector_vs_baseline_context_delta"] = sel_on - ctx_off if sel_on > 0 else None
        impl_off = r_off.get("implementation_body_present_count") or 0
        linked_off = r_off.get("linked_row_count") or 0
        test_off = r_off.get("test_file_row_count") or 0
        sel_impl = r_on.get("selected_impl_body_count") or 0
        sel_linked = r_on.get("selected_linked_row_count") or 0
        sel_test = r_on.get("selected_test_row_count") or 0
        if sel_on > 0 and impl_off >= 0:
            diff["selected_impl_retention_rate"] = sel_impl / max(impl_off, 1)
        else:
            diff["selected_impl_retention_rate"] = None
        if sel_on > 0 and linked_off >= 0:
            diff["selected_linked_retention_rate"] = sel_linked / max(linked_off, 1)
        else:
            diff["selected_linked_retention_rate"] = None
        if sel_on > 0:
            diff["selected_test_drift"] = sel_test - test_off
        else:
            diff["selected_test_drift"] = None
        # Per-task linked-row preservation
        diff["had_links_off"] = linked_off > 0
        diff["kept_any_links_on"] = sel_linked > 0 if sel_on > 0 else (r_on.get("linked_row_count") or 0) > 0
        diff["lost_all_links_on"] = diff["had_links_off"] and not diff["kept_any_links_on"]
        diff["architecture_answer_ready_on"] = r_on.get("architecture_answer_ready")
        # Useful compaction: fewer rows than baseline AND kept ≥1 impl AND (arch → ≥1 linked)
        is_arch = tid in architecture_task_ids()
        if sel_on > 0 and ctx_off > 0:
            fewer_rows = sel_on < ctx_off
            kept_impl = sel_impl >= 1
            kept_linked = sel_linked >= 1 if is_arch else True
            diff["useful_compaction"] = fewer_rows and kept_impl and kept_linked
        else:
            diff["useful_compaction"] = None
        out.append(diff)
    return out


def compute_selector_quality_aggregates(
    rec_off: list[dict[str, Any]],
    rec_on: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Compute selector-quality aggregates from paired OFF/ON records.
    Returns: selected_impl_retention_rate, selected_linked_retention_rate,
    selected_test_drift_rate, architecture_answer_ready_rate (ON arm).
    """
    diffs = build_per_task_diffs(rec_off, rec_on)
    if not diffs:
        return {
            "selected_impl_retention_rate": None,
            "selected_linked_retention_rate": None,
            "selected_test_drift_rate": None,
            "architecture_answer_ready_rate": None,
            "average_selector_vs_baseline_context_delta": None,
            "architecture_tasks_lost_all_links_rate": None,
            "useful_compaction_rate": None,
        }
    from tests.agent_eval.suites.search_stack import architecture_task_ids, selector_hard_task_ids

    arch_ids = architecture_task_ids() | selector_hard_task_ids()
    impl_rates = [d["selected_impl_retention_rate"] for d in diffs if d.get("selected_impl_retention_rate") is not None]
    linked_rates = [d["selected_linked_retention_rate"] for d in diffs if d.get("selected_linked_retention_rate") is not None]
    test_drifts = [d["selected_test_drift"] for d in diffs if d.get("selected_test_drift") is not None]
    ctx_deltas = [d["selector_vs_baseline_context_delta"] for d in diffs if d.get("selector_vs_baseline_context_delta") is not None]
    arch_recs_on = [r for r in rec_on if "architecture" in (r.get("tags") or [])]
    arch_ready_on = [r.get("architecture_answer_ready") for r in arch_recs_on if r.get("architecture_answer_ready") is not None]

    arch_diffs_with_links = [d for d in diffs if d.get("task_id") in arch_ids and d.get("had_links_off")]
    lost_all = sum(1 for d in arch_diffs_with_links if d.get("lost_all_links_on"))
    architecture_tasks_lost_all_links_rate = lost_all / len(arch_diffs_with_links) if arch_diffs_with_links else None

    rec_on_by_tid = {r.get("task_id"): r for r in rec_on if r.get("task_id")}
    selector_used_count = sum(1 for d in diffs if (rec_on_by_tid.get(d.get("task_id")) or {}).get("selected_id_count", 0) > 0)
    useful_compaction_count = sum(1 for d in diffs if d.get("useful_compaction") is True)
    useful_compaction_rate = useful_compaction_count / selector_used_count if selector_used_count else None

    arch_selector_recs = [r for r in rec_on if "architecture" in (r.get("tags") or []) and r.get("bundle_selector_used")]
    arch_safe_vals = [r.get("architecture_safe_selection") for r in arch_selector_recs if r.get("architecture_safe_selection") is not None]
    architecture_safe_selection_rate = sum(arch_safe_vals) / len(arch_safe_vals) if arch_safe_vals else None

    return {
        "selected_impl_retention_rate": sum(impl_rates) / len(impl_rates) if impl_rates else None,
        "selected_linked_retention_rate": sum(linked_rates) / len(linked_rates) if linked_rates else None,
        "selected_test_drift_rate": sum(test_drifts) / len(test_drifts) if test_drifts else None,
        "architecture_answer_ready_rate": sum(arch_ready_on) / len(arch_ready_on) if arch_ready_on else None,
        "average_selector_vs_baseline_context_delta": sum(ctx_deltas) / len(ctx_deltas) if ctx_deltas else None,
        "architecture_tasks_lost_all_links_rate": architecture_tasks_lost_all_links_rate,
        "useful_compaction_rate": useful_compaction_rate,
        "architecture_safe_selection_rate": architecture_safe_selection_rate,
    }


def split_per_task_diffs_by_architecture(
    diffs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split per-task diffs into architecture vs generic retrieval tasks."""
    from tests.agent_eval.suites.search_stack import architecture_task_ids

    arch_ids = architecture_task_ids()
    arch = [d for d in diffs if d.get("task_id") in arch_ids]
    generic = [d for d in diffs if d.get("task_id") not in arch_ids]
    return arch, generic


def load_retrieval_quality_records_from_run_dir(run_dir: Path) -> list[dict[str, Any]]:
    """Load tasks/*/outcome.json retrieval_quality blobs."""
    out: list[dict[str, Any]] = []
    tasks_dir = Path(run_dir) / "tasks"
    if not tasks_dir.is_dir():
        return out
    for sub in sorted(tasks_dir.iterdir()):
        if not sub.is_dir():
            continue
        path = sub / "outcome.json"
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rq = raw.get("retrieval_quality") if isinstance(raw, dict) else None
        if isinstance(rq, dict):
            out.append(rq)
    return out


def test_metrics_basic():
    ctx = [
        {
            "file": "src/a.py",
            "snippet": "def f(): pass",
            "candidate_kind": "symbol",
            "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
            "relations": [{"kind": "ownership", "target_file": "src/a.py"}],
        }
    ]
    m = assert_retrieval_quality(ctx, "find f", {"retrieval_intent": "symbol"})
    assert m["has_symbol_or_region_body"] is True
    assert m["has_non_test_implementation"] is True
    assert m["relation_count"] == 1


def test_metrics_test_only_file():
    ctx = [{"file": "tests/test_x.py", "snippet": "x", "candidate_kind": "file"}]
    m = assert_retrieval_quality(ctx, "q", {})
    assert m["has_non_test_implementation"] is False


def test_aggregate_and_compare():
    recs = [
        {
            "task_id": "a",
            "tags": ["architecture", "retrieval_quality"],
            "explain_step_ok": True,
            "symbol_body_present": True,
            "impl_bias_ok": True,
            "relation_ok": True,
            "all_kinds_typed": True,
            "final_context_count": 2,
            "final_context_chars": 100,
            "replanner_triggered": False,
        },
        {
            "task_id": "b",
            "tags": ["implementation", "retrieval_quality"],
            "explain_step_ok": True,
            "symbol_body_present": False,
            "impl_bias_ok": True,
            "relation_ok": None,
            "all_kinds_typed": False,
            "final_context_count": 4,
            "final_context_chars": 200,
            "replanner_triggered": True,
        },
    ]
    agg = aggregate_retrieval_metrics(recs)
    assert agg["task_count"] == 2
    assert agg["explain_success_rate"] == 1.0
    assert agg["impl_bias_ok_rate"] == 1.0
    assert agg["relation_ok_rate"] == 1.0
    on = dict(agg)
    on["symbol_body_present_rate"] = 1.0
    cmp = compare_ab_runs(agg, on)
    assert cmp["deltas_on_minus_off"]["symbol_body_present_rate"] == 0.5
    assert verdict_kind_expansion_step1(agg, {**agg, "explain_success_rate": 1.0}) in (
        "inconclusive",
        "canary_worthy",
    )


def test_verdict_stay_off_on_context_surge():
    off = aggregate_retrieval_metrics(
        [
            {
                "tags": [],
                "explain_step_ok": True,
                "symbol_body_present": True,
                "impl_bias_ok": True,
                "relation_ok": None,
                "all_kinds_typed": True,
                "final_context_count": 2,
                "final_context_chars": 100,
                "replanner_triggered": False,
            }
        ]
    )
    on = {**off, "average_final_context_chars": 200.0}
    assert verdict_kind_expansion_step1(off, on) == "stay_off"


def test_compute_ranked_context_breakdown():
    rows = [
        {"file": "src/a.py", "candidate_kind": "symbol", "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY, "implementation_body_present": True, "relations": [{"kind": "import"}]},
        {"file": "src/b.py", "candidate_kind": "file", "retrieval_result_type": RETRIEVAL_RESULT_TYPE_FILE_HEADER},
        {"file": "tests/test_x.py", "candidate_kind": "region", "retrieval_result_type": RETRIEVAL_RESULT_TYPE_REGION_BODY},
    ]
    b = _compute_ranked_context_breakdown(rows)
    assert b["symbol_body_count"] == 1
    assert b["file_header_count"] == 1
    assert b["region_body_count"] == 1
    assert b["implementation_body_present_count"] == 1
    assert b["linked_row_count"] == 1
    assert b["test_file_row_count"] == 1
    assert b["impl_file_row_count"] == 2
    assert b["distinct_impl_file_count"] == 2
    assert b["candidate_kind_counts"]["symbol"] == 1
    assert b["candidate_kind_counts"]["file"] == 1
    assert len(b["top_files"]) <= 3


def test_aggregate_search_failure_modes():
    """Search failure modes aggregated from search_debug_records."""
    from tests.agent_eval.check_retrieval_quality import aggregate_search_failure_modes

    recs = [
        {"search_debug_records": [
            {"retrieval_empty": True, "pool_has_signal": False, "selection_loss": False},
            {"retrieval_empty": False, "pool_has_signal": True, "selection_loss": True},
            {"retrieval_empty": False, "pool_has_signal": True, "selection_loss": False},
        ]},
        {"search_debug_records": []},
        {"search_debug_records": [
            {"retrieval_empty": False, "pool_has_signal": False, "selection_loss": False},
        ]},
    ]
    sfm = aggregate_search_failure_modes(recs)
    assert sfm["search_debug_record_count"] == 4
    assert sfm["retrieval_empty_rate"] == 1 / 4
    assert sfm["pool_weak_rate"] == 2 / 4  # pool_has_signal False in 2 records
    assert sfm["selection_loss_rate"] == 1 / 4


def test_aggregate_includes_new_metrics():
    recs = [
        {"task_id": "t1", "final_context_count": 4, "final_context_chars": 200, "implementation_body_present_count": 2, "linked_row_count": 1, "symbol_body_count": 2, "file_header_count": 1, "region_body_count": 0, "test_file_row_count": 1, "impl_file_row_count": 3},
        {"task_id": "t2", "final_context_count": 6, "final_context_chars": 400, "implementation_body_present_count": 3, "linked_row_count": 2, "symbol_body_count": 3, "file_header_count": 1, "region_body_count": 1, "test_file_row_count": 0, "impl_file_row_count": 6},
    ]
    agg = aggregate_retrieval_metrics(recs)
    assert agg["average_implementation_body_present_count"] == 2.5
    assert agg["average_linked_row_count"] == 1.5
    assert agg["average_symbol_body_count"] == 2.5
    assert agg["average_test_file_row_count"] == 0.5
    assert agg["average_impl_file_row_count"] == 4.5


def test_compare_ab_deltas_new_metrics():
    off = aggregate_retrieval_metrics([{"task_id": "t1", "final_context_count": 4, "final_context_chars": 100, "implementation_body_present_count": 1, "linked_row_count": 0}])
    on = aggregate_retrieval_metrics([{"task_id": "t1", "final_context_count": 6, "final_context_chars": 150, "implementation_body_present_count": 3, "linked_row_count": 2}])
    cmp = compare_ab_runs(off, on)
    d = cmp["deltas_on_minus_off"]
    assert d["average_final_context_count"] == 2.0
    assert d["average_implementation_body_present_count"] == 2.0
    assert d["average_linked_row_count"] == 2.0


def test_diagnostic_verdict():
    off = {"task_count": 2, "average_implementation_body_present_count": 1.0, "average_linked_row_count": 0.5, "average_test_file_row_count": 0.0, "average_impl_file_row_count": 2.0}
    on = {"task_count": 2, "average_implementation_body_present_count": 2.0, "average_linked_row_count": 1.5, "average_test_file_row_count": 0.5, "average_impl_file_row_count": 3.0}
    diag = diagnostic_verdict_kind_expansion(off, on)
    assert diag["substantive_context_improved"] is True
    assert diag["linked_arch_improved"] is True
    assert "substantive+" in diag["summary"]


def test_build_per_task_diffs():
    rec_off = [{"task_id": "a", "final_context_count": 4, "linked_row_count": 0, "impl_file_row_count": 4}]
    rec_on = [{"task_id": "a", "final_context_count": 6, "linked_row_count": 2, "impl_file_row_count": 5}]
    diffs = build_per_task_diffs(rec_off, rec_on)
    assert len(diffs) == 1
    assert diffs[0]["task_id"] == "a"
    assert diffs[0]["final_context_count_delta"] == 2
    assert diffs[0]["linked_row_count_delta"] == 2


def test_architecture_task_ids():
    from tests.agent_eval.suites.search_stack import architecture_task_ids, retrieval_quality_task_ids

    arch = architecture_task_ids()
    all_rq = retrieval_quality_task_ids()
    assert arch <= all_rq
    assert "sq_entrypoint_arch" in arch
    assert "sq_2hop_arch" in arch
    assert "sq_fallback_guard" in arch
    assert "sq_config_settings" in arch
    assert "sq_symbol_exact" not in arch


def test_selector_hard_task_ids():
    from tests.agent_eval.suites.search_stack import selector_hard_task_ids

    hard = selector_hard_task_ids()
    expected = {
        "sq_hard_entrypoint_settings",
        "sq_hard_config_runtime",
        "sq_hard_fallback_callers",
        "sq_hard_dispatch_executor",
        "sq_hard_impl_not_tests",
        "sq_hard_2hop_arch",
    }
    assert hard == expected
    assert len(hard) == 6


def test_assert_architecture_quality():
    # Good multi-hop: linked>=2, impl, not test-dominated, distinct_impl>=2
    good = _assert_architecture_quality(
        {"linked_row_count": 3, "implementation_body_present_count": 2, "impl_file_row_count": 4, "test_file_row_count": 1, "distinct_impl_file_count": 2},
        "sq_2hop_arch",
    )
    assert good["architecture_linked_ok"] is True
    assert good["architecture_substantive_impl_ok"] is True
    assert good["architecture_not_test_dominated"] is True
    assert good["architecture_multi_file_ok"] is True
    assert good["architecture_ok"] is True

    # Single-hop (sq_fallback_guard): multi_file not required
    single = _assert_architecture_quality(
        {"linked_row_count": 2, "implementation_body_present_count": 1, "impl_file_row_count": 2, "test_file_row_count": 0, "distinct_impl_file_count": 1},
        "sq_fallback_guard",
    )
    assert single["architecture_multi_file_ok"] is True
    assert single["architecture_ok"] is True

    # Multi-hop with only 1 impl file fails multi_file_ok
    bad_multi = _assert_architecture_quality(
        {"linked_row_count": 2, "implementation_body_present_count": 1, "impl_file_row_count": 2, "test_file_row_count": 0, "distinct_impl_file_count": 1},
        "sq_2hop_arch",
    )
    assert bad_multi["architecture_multi_file_ok"] is False
    assert bad_multi["architecture_ok"] is False


def test_split_per_task_diffs_by_architecture():
    diffs = [
        {"task_id": "sq_entrypoint_arch"},
        {"task_id": "sq_symbol_exact"},
        {"task_id": "sq_fallback_guard"},
    ]
    arch, generic = split_per_task_diffs_by_architecture(diffs)
    assert len(arch) == 2
    assert len(generic) == 1
    arch_ids = {d["task_id"] for d in arch}
    assert "sq_entrypoint_arch" in arch_ids
    assert "sq_fallback_guard" in arch_ids
    assert generic[0]["task_id"] == "sq_symbol_exact"


def test_aggregate_architecture_metrics():
    recs = [
        {"task_id": "a", "tags": ["architecture"], "architecture_ok": True, "distinct_impl_file_count": 2},
        {"task_id": "b", "tags": ["architecture"], "architecture_ok": False, "distinct_impl_file_count": 1},
        {"task_id": "c", "tags": ["symbol"], "distinct_impl_file_count": 1},
    ]
    agg = aggregate_retrieval_metrics(recs)
    assert agg["architecture_task_count"] == 2
    assert agg["architecture_ok_rate"] == 0.5
    assert agg["average_distinct_impl_file_count"] == (2 + 1 + 1) / 3


def test_aggregate_selector_integrity_rate():
    recs = [
        {"task_id": "a", "bundle_selector_used": True, "selector_integrity_ok": True},
        {"task_id": "b", "bundle_selector_used": True, "selector_integrity_ok": False},
        {"task_id": "c", "bundle_selector_used": False},
    ]
    agg = aggregate_retrieval_metrics(recs)
    assert agg["selector_integrity_rate"] == 0.5


def test_build_record_includes_selector_metrics():
    class _Spec:
        task_id = "sq_entrypoint_arch"
        tags = ("search_stack", "retrieval_quality", "architecture")
        instruction = "How does entrypoint connect to settings?"

    _selected = [
        {
            "candidate_id": "rc_0002",
            "file": "src/b.py",
            "snippet": "import",
            "candidate_kind": "file",
            "relations": [{"kind": "import"}],
            "implementation_body_present": True,
        },
        {
            "candidate_id": "rc_0005",
            "file": "src/impl.py",
            "snippet": "def impl",
            "candidate_kind": "symbol",
            "implementation_body_present": True,
        },
    ]
    _pool = [
        {"candidate_id": "rc_0001", "file": "src/a.py", "snippet": "def a", "candidate_kind": "symbol", "implementation_body_present": True},
        {"candidate_id": "rc_0002", "file": "src/b.py", "snippet": "import", "candidate_kind": "file", "relations": [{"kind": "import"}]},
        {"candidate_id": "rc_0005", "file": "src/impl.py", "snippet": "def impl", "candidate_kind": "symbol"},
    ]
    class _State:
        context = {
            "ranked_context": _selected,
            "retrieval_candidate_pool": _pool,
            "bundle_selector_used": True,
            "bundle_selector_keep_ids": ["rc_0002", "rc_0005"],
            "bundle_selector_dropped_ids": ["rc_0001"],
            "bundle_selector_selected_pool": _selected,
            "bundle_selector_selected_impl_body_count": 2,
            "bundle_selector_selected_linked_row_count": 1,
            "bundle_selector_selected_test_row_count": 0,
            "final_answer_context_from_selected_rows_only": True,
        }
        step_results = []

    rec = build_retrieval_quality_record(_Spec(), _State(), None)
    assert rec["bundle_selector_used"] is True
    assert rec["selected_id_count"] == 2
    assert rec["selected_impl_body_count"] == 2
    assert rec["selected_linked_row_count"] == 1
    assert rec["selected_test_row_count"] == 0
    assert rec["bundle_selector_dropped_id_count"] == 1
    assert rec["final_answer_context_from_selected_rows_only"] is True
    assert rec["selector_integrity_ok"] is True
    assert "bundle_selector_observability" in rec
    obs = rec["bundle_selector_observability"]
    assert obs["used"] is True
    assert obs["keep_ids"] == ["rc_0002", "rc_0005"]
    assert obs["dropped_ids_count"] == 1
    assert obs["selected_id_count"] == 2


def test_selector_integrity_true_path():
    """When selector used and invariants hold, selector_integrity_ok is True."""
    class _Spec:
        task_id = "sq_test"
        tags = ("retrieval_quality",)
        instruction = "test"

    rows = [
        {"candidate_id": "rc_001", "file": "src/a.py", "snippet": "a", "implementation_body_present": True},
        {"candidate_id": "rc_002", "file": "src/b.py", "snippet": "b", "relations": [{}]},
    ]
    pool = [
        {"candidate_id": "rc_001", "file": "src/a.py"},
        {"candidate_id": "rc_002", "file": "src/b.py"},
        {"candidate_id": "rc_003", "file": "src/c.py"},
    ]
    class _State:
        context = {
            "ranked_context": rows,
            "retrieval_candidate_pool": pool,
            "bundle_selector_used": True,
            "bundle_selector_keep_ids": ["rc_001", "rc_002"],
            "bundle_selector_selected_pool": rows,
            "bundle_selector_skip_reason": "",
        }
        step_results = []

    rec = build_retrieval_quality_record(_Spec(), _State(), None)
    assert rec["selector_integrity_ok"] is True


def test_selector_integrity_false_path():
    """When ranked_context does not match selected pool, selector_integrity_ok is False."""
    class _Spec:
        task_id = "sq_test"
        tags = ("retrieval_quality",)
        instruction = "test"

    rows = [{"candidate_id": "rc_001", "file": "src/a.py"}]
    pool = [
        {"candidate_id": "rc_001", "file": "src/a.py"},
        {"candidate_id": "rc_002", "file": "src/b.py"},
    ]
    class _State:
        context = {
            "ranked_context": rows,
            "retrieval_candidate_pool": pool,
            "bundle_selector_used": True,
            "bundle_selector_keep_ids": ["rc_001", "rc_002"],
            "bundle_selector_selected_pool": rows,
            "bundle_selector_skip_reason": "",
        }
        step_results = []

    rec = build_retrieval_quality_record(_Spec(), _State(), None)
    assert rec["selector_integrity_ok"] is False


def test_selector_quality_metrics():
    """Selector quality metrics computed from paired records."""
    rec_off = [
        {
            "task_id": "a",
            "tags": ["architecture"],
            "final_context_count": 6,
            "implementation_body_present_count": 4,
            "linked_row_count": 2,
            "test_file_row_count": 1,
        },
    ]
    rec_on = [
        {
            "task_id": "a",
            "tags": ["architecture"],
            "final_context_count": 4,
            "selected_id_count": 4,
            "selected_impl_body_count": 3,
            "selected_linked_row_count": 2,
            "selected_test_row_count": 0,
            "implementation_body_present_count": 3,
            "linked_row_count": 2,
            "architecture_answer_ready": True,
        },
    ]
    sq = compute_selector_quality_aggregates(rec_off, rec_on)
    assert sq["selected_impl_retention_rate"] == 3 / 4  # 3/4
    assert sq["selected_linked_retention_rate"] == 2 / 2  # 1.0
    assert sq["selected_test_drift_rate"] == -1  # 0 - 1
    assert sq["architecture_answer_ready_rate"] == 1.0
    assert sq["average_selector_vs_baseline_context_delta"] == -2  # 4 - 6


def test_selector_quality_verdict_promote():
    """promote_candidate when arch preserved, retentions >= 0.5, no test drift."""
    off = {"task_count": 4, "architecture_ok_rate": 0.5}
    on = {"task_count": 4, "architecture_ok_rate": 0.5, "selected_impl_retention_rate": 0.6, "selected_linked_retention_rate": 0.6, "selected_test_drift_rate": 0}
    assert selector_quality_verdict(off, on) == "promote_candidate"


def test_selector_quality_verdict_regress_linking():
    """regress_linking when linked retention < 0.3."""
    off = {"task_count": 4}
    on = {"task_count": 4, "selected_linked_retention_rate": 0.2}
    assert selector_quality_verdict(off, on) == "regress_linking"


def test_selector_quality_verdict_regress_impl():
    """regress_impl when impl retention < 0.3."""
    off = {"task_count": 4}
    on = {"task_count": 4, "selected_impl_retention_rate": 0.2, "selected_linked_retention_rate": 0.5}
    assert selector_quality_verdict(off, on) == "regress_impl"


def test_selector_quality_verdict_regress_test_drift():
    """regress_test_drift when test drift > 0.5."""
    off = {"task_count": 4}
    on = {"task_count": 4, "selected_impl_retention_rate": 0.6, "selected_linked_retention_rate": 0.6, "selected_test_drift_rate": 1.0}
    assert selector_quality_verdict(off, on) == "regress_test_drift"


def test_selector_quality_verdict_neutral():
    """neutral when arch drops below baseline."""
    off = {"task_count": 4, "architecture_ok_rate": 0.75}
    on = {"task_count": 4, "architecture_ok_rate": 0.5, "selected_impl_retention_rate": 0.6, "selected_linked_retention_rate": 0.6, "selected_test_drift_rate": 0}
    assert selector_quality_verdict(off, on) == "neutral"


def test_aggregate_includes_selector_fields():
    agg = aggregate_retrieval_metrics(
        [
            {
                "task_id": "t1",
                "final_context_count": 2,
                "final_context_chars": 100,
                "bundle_selector_used": True,
                "selected_id_count": 2,
                "selected_impl_body_count": 1,
                "selected_linked_row_count": 1,
                "selected_test_row_count": 0,
                "final_answer_context_from_selected_rows_only": True,
            },
            {
                "task_id": "t2",
                "final_context_count": 4,
                "final_context_chars": 200,
                "bundle_selector_used": False,
                "selected_id_count": 0,
                "selected_impl_body_count": 0,
                "selected_linked_row_count": 0,
                "selected_test_row_count": 1,
                "final_answer_context_from_selected_rows_only": False,
            },
        ]
    )
    assert agg["bundle_selector_usage_rate"] == 0.5
    assert agg["average_selected_id_count"] == 1.0
    assert agg["average_selected_impl_body_count"] == 0.5
    assert agg["average_selected_linked_row_count"] == 0.5
    assert agg["average_selected_test_row_count"] == 0.5
    assert agg["selected_rows_only_rate"] == 0.5
