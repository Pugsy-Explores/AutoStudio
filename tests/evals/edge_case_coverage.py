"""
Shared edge-case category labels and coverage reporting for live eval suites.

Categories (YAML ``edge_category`` on each case; omit or ``baseline`` for legacy rows):

  empty_input, low_signal, partial_context, no_relevance,
  ambiguous, conflicting, over_specified, baseline

``edge_handling`` (optional, mainly for ``empty_input``):

  - ``system_handled``: no model call (e.g. empty pool short-circuit).
  - ``model_handled``: model runs on empty/whitespace/weak instruction or minimal pool.

``trace_id`` (optional): defaults to case ``id``; use the same ``trace_id`` across modules
to correlate rows in post-hoc analysis.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

EDGE_CATEGORY_KEYS: tuple[str, ...] = (
    "empty_input",
    "low_signal",
    "partial_context",
    "no_relevance",
    "ambiguous",
    "conflicting",
    "over_specified",
    "baseline",
)


def normalize_edge_category(raw: Any) -> str:
    s = str(raw or "baseline").strip().lower().replace("-", "_")
    if s in EDGE_CATEGORY_KEYS:
        return s
    return "baseline"


def count_categories(cases: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {k: 0 for k in EDGE_CATEGORY_KEYS}
    for case in cases:
        cat = normalize_edge_category(case.get("edge_category"))
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def count_categories_from_records(
    records: list[tuple[Any, dict[str, Any], dict[str, Any]]],
) -> dict[str, int]:
    counts = {k: 0 for k in EDGE_CATEGORY_KEYS}
    for _tier, case, _row in records:
        cat = normalize_edge_category(case.get("edge_category"))
        counts[cat] = counts.get(cat, 0) + 1
    return counts


def missing_categories(counts: dict[str, int]) -> list[str]:
    """Categories with zero cases (excluding baseline)."""
    out: list[str] = []
    for k in EDGE_CATEGORY_KEYS:
        if k == "baseline":
            continue
        if counts.get(k, 0) == 0:
            out.append(k)
    return sorted(out)


def non_baseline_case_count(counts: dict[str, int]) -> int:
    return sum(counts.get(k, 0) for k in EDGE_CATEGORY_KEYS if k != "baseline")


def normalize_edge_handling(raw: Any) -> str:
    s = str(raw or "").strip().lower().replace("-", "_")
    if s in ("system_handled", "model_handled"):
        return s
    return ""


def infer_edge_handling(case: dict[str, Any]) -> str:
    """Best-effort when YAML omits ``edge_handling``."""
    explicit = normalize_edge_handling(case.get("edge_handling"))
    if explicit:
        return explicit
    # Only empty *candidate pool* paths skip the model in scoper/selector.
    if case.get("allow_empty_candidates"):
        return "system_handled"
    cat = normalize_edge_category(case.get("edge_category"))
    if cat == "empty_input":
        return "model_handled"
    return ""


CategoryMetrics = dict[str, dict[str, int]]


def compute_category_metrics(
    records: list[tuple[Any, dict[str, Any], dict[str, Any]]],
    *,
    row_hard_fail: Callable[[dict[str, Any]], bool],
) -> CategoryMetrics:
    """
    Per category (excluding baseline): totals, warning volume, cases with any warning, hard-fail cases.

    Failure density hints:
    - ``warning_lines`` / ``total`` ≈ average warnings per case in category.
    - ``hard_fail`` / ``total`` ≈ strict failure rate for that category (module-defined).
    """
    m: CategoryMetrics = {}
    for k in EDGE_CATEGORY_KEYS:
        if k == "baseline":
            continue
        m[k] = {
            "total": 0,
            "warning_lines": 0,
            "cases_with_warnings": 0,
            "hard_fail": 0,
        }

    for _tier, case, row in records:
        cat = normalize_edge_category(case.get("edge_category"))
        if cat == "baseline":
            continue
        bucket = m[cat]
        bucket["total"] += 1
        ws = row.get("warnings") or []
        bucket["warning_lines"] += len(ws)
        if ws:
            bucket["cases_with_warnings"] += 1
        if row_hard_fail(row):
            bucket["hard_fail"] += 1

    return m


def print_category_metrics_block(module_name: str, metrics: CategoryMetrics) -> None:
    lines = [
        f"[category_metrics] module={module_name}",
        "",
    ]
    for key in EDGE_CATEGORY_KEYS:
        if key == "baseline":
            continue
        b = metrics.get(key) or {
            "total": 0,
            "warning_lines": 0,
            "cases_with_warnings": 0,
            "hard_fail": 0,
        }
        tot = b["total"]
        wd = b["warning_lines"]
        cf = b["cases_with_warnings"]
        hf = b["hard_fail"]
        if tot == 0:
            lines.append(
                f"- {key}: total=0 warning_lines=0 cases_with_warnings=0 hard_fail=0 "
                f"(warn_density=n/a hard_rate=n/a)"
            )
            continue
        lines.append(
            f"- {key}: total={tot} warning_lines={wd} "
            f"cases_with_warnings={cf} hard_fail={hf} "
            f"(warn_density={wd / tot:.3f} hard_rate={hf / tot:.3f})"
        )
    lines.append("")
    print("\n".join(lines))


def print_edge_case_coverage_report(
    module_name: str,
    counts: dict[str, int],
    *,
    metrics: CategoryMetrics | None = None,
) -> None:
    lines = [
        "=== EDGE CASE COVERAGE REPORT ===",
        "",
        f"Module: {module_name}",
    ]
    for key in EDGE_CATEGORY_KEYS:
        if key == "baseline":
            continue
        lines.append(f"- {key}: {counts.get(key, 0)}")
    lines.append(f"- baseline (untagged / legacy): {counts.get('baseline', 0)}")
    lines.append("")
    lines.append("[SUMMARY]")
    total_tagged = non_baseline_case_count(counts)
    lines.append(f"- Total edge-tagged cases (non-baseline): {total_tagged}")
    lines.append("- Modules covered: 1/4 (run all four eval entrypoints for full matrix)")
    miss = missing_categories(counts)
    lines.append(f"- Missing categories (this module): {miss if miss else '[]'}")
    lines.append("")
    print("\n".join(lines))
    if metrics:
        print_category_metrics_block(module_name, metrics)


def print_trace_log(
    module_name: str,
    records: list[tuple[Any, dict[str, Any], dict[str, Any]]],
    *,
    row_hard_fail: Callable[[dict[str, Any]], bool],
) -> None:
    """Emit ``trace_id`` lines for cross-module correlation (grep / join by ``trace_id``)."""
    lines = [
        "=== TRACE LOG (cross-module correlation) ===",
        "",
        f"Module: {module_name}",
        "",
    ]
    for _t, case, row in records:
        tid = str(case.get("trace_id") or case.get("id") or "?")
        cat = normalize_edge_category(case.get("edge_category"))
        if cat == "baseline":
            continue
        eh = infer_edge_handling(case)
        ws = row.get("warnings") or []
        hf = 1 if row_hard_fail(row) else 0
        lines.append(
            f"trace_id={tid} edge_category={cat} edge_handling={eh or '(n/a)'} "
            f"warnings={len(ws)} row_hard_fail={hf}"
        )
    lines.append("")
    print("\n".join(lines))


def print_edge_failure_patterns_diagnostic(
    module_name: str,
    warning_texts: list[str],
) -> None:
    """Map accumulated warning strings to coarse failure-mode labels."""
    joined = " ".join(w.lower() for w in warning_texts)
    tags: list[str] = []

    def hit(*needles: str) -> bool:
        return all(n in joined for n in needles)

    if "empty" in joined and ("candidate" in joined or "selection" in joined or "output" in joined):
        tags.append("fails on empty input / empty pool")
    if "noise" in joined or "over-selection" in joined or "peripheral" in joined:
        tags.append("over-selects in noisy context")
    if "missing gap" in joined or ("partial" in joined and "gap" in joined):
        tags.append("misses gaps when context partial")
    if "overconfidence" in joined or "sufficient when missing" in joined or "hallucinat" in joined:
        tags.append("overconfidence when context wrong or under-specified")
    if "redundant" in joined:
        tags.append("redundant selection in similar-candidate pools")
    if "must_include" in joined or "must_select" in joined or "missing core" in joined:
        tags.append("missed required signals under weak or noisy evidence")

    lines = [
        "=== EDGE FAILURE PATTERNS ===",
        "",
        f"Module: {module_name}",
        "",
    ]
    if not tags:
        lines.append("(no pattern keywords matched this run)")
    else:
        for t in sorted(set(tags)):
            lines.append(f"- {t}")
    lines.append("")
    print("\n".join(lines))
