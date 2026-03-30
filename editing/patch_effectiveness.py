"""
Generic patch effectiveness checks (Stage 23): reject no-op / unchanged edits before they count as viable applies.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

MAX_SNIPPET_LEN = 400


def meaningful_diff_line_count(before: str, after: str) -> int:
    """Count line slots involved in non-equal regions (bounded, deterministic)."""
    if before == after:
        return 0
    a = before.splitlines()
    b = after.splitlines()
    matcher = difflib.SequenceMatcher(None, a, b)
    n = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        n += max(i2 - i1, j2 - j1)
    return max(1, n) if before != after else 0


def _bounded(s: str | None, max_len: int = MAX_SNIPPET_LEN) -> str:
    if s is None:
        return ""
    t = s.replace("\r\n", "\n")
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _snippet_around(haystack: str, needle: str) -> str:
    """Bounded excerpt around first occurrence of needle (for target region telemetry)."""
    if not haystack:
        return ""
    if not needle:
        return _bounded(haystack)
    i = haystack.find(needle)
    if i < 0:
        return _bounded(haystack)
    margin = 120
    start = max(0, i - margin)
    end = min(len(haystack), i + len(needle) + margin)
    return _bounded(haystack[start:end])


def module_append_is_meaningful(code: str, original_module_source: str) -> tuple[bool, str | None]:
    """
    module_append must introduce a new def/class or a new top-level binding not present in original.
    Returns (ok, reject_reason or None).
    """
    code = code or ""
    if not code.strip():
        return False, "no_meaningful_diff"
    stmt_lines = [ln for ln in code.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not stmt_lines:
        return False, "no_meaningful_diff"

    for m in re.finditer(r"^\s*def\s+(\w+)\s*\(", code, re.MULTILINE):
        name = m.group(1)
        if not re.search(rf"^\s*def\s+{re.escape(name)}\s*\(", original_module_source, re.MULTILINE):
            return True, None
    for m in re.finditer(r"^\s*class\s+(\w+)\s*(?:\(|:)", code, re.MULTILINE):
        name = m.group(1)
        if not re.search(rf"^\s*class\s+{re.escape(name)}\s*(?:\(|:)", original_module_source, re.MULTILINE):
            return True, None
    for m in re.finditer(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[^=]", code, re.MULTILINE):
        name = m.group(1)
        if name in ("def", "class", "if", "for", "while", "with", "return"):
            continue
        if not re.search(rf"^\s*{re.escape(name)}\s*=\s*[^=]", original_module_source, re.MULTILINE):
            return True, None

    return False, "no_meaningful_diff"


def build_effectiveness_report(
    *,
    before: str,
    after: str,
    patch_kind: str,
    old_text: str | None = None,
    reject_reason: str | None = None,
) -> dict[str, Any]:
    """Single change summary for telemetry (JSON-safe, bounded)."""
    effective = reject_reason is None and before != after
    mlines = meaningful_diff_line_count(before, after)
    if reject_reason is None and before == after:
        reject_reason = "unchanged_target_region"
        effective = False
    tb = _snippet_around(before, old_text) if old_text else _bounded(before)
    ta = _snippet_around(after, old_text) if old_text else _bounded(after)
    if old_text and old_text in before and old_text in after and tb == ta and before != after:
        ta = _snippet_around(after, old_text)

    return {
        "patch_kind": patch_kind,
        "patch_effective_change": effective,
        "patch_effective_reason": reject_reason,
        "changed_region_detected": before != after,
        "target_region_before": tb,
        "target_region_after": ta,
        "meaningful_diff_line_count": mlines,
        "rejected_for_noop_or_unchanged": reject_reason is not None,
    }


def assess_text_sub(
    *,
    source_before: str,
    old: str,
    new: str,
) -> tuple[bool, str | None, str | None, dict[str, Any]]:
    """
    Returns (ok, reject_reason, new_source_if_ok, report).
    reject_reason: no_effect_change | unchanged_target_region | no_meaningful_diff
    """
    o, n = str(old), str(new)
    if not o.strip():
        return False, "empty_patch", None, {}
    if o == n:
        return False, "no_effect_change", None, {}
    if o not in source_before:
        return True, None, None, {}  # executor computes new_src; target_not_found handled there
    new_src = source_before.replace(o, n, 1)
    if new_src == source_before:
        return False, "unchanged_target_region", None, {}
    rep = build_effectiveness_report(
        before=source_before,
        after=new_src,
        patch_kind="text_sub",
        old_text=o,
        reject_reason=None,
    )
    if rep["meaningful_diff_line_count"] < 1:
        rep["patch_effective_change"] = False
        rep["patch_effective_reason"] = "no_meaningful_diff"
        rep["rejected_for_noop_or_unchanged"] = True
        return False, "no_meaningful_diff", None, {"patch_effectiveness_step": rep}
    return True, None, new_src, {"patch_effectiveness_step": rep}


def assess_after_content_change(
    *,
    source_before: str,
    source_after: str,
    patch_kind: str,
    old_text: str | None,
    module_append_code: str | None,
) -> tuple[bool, str | None, dict[str, Any]]:
    """Assess AST / structured patch after computing source_after."""
    if source_after == source_before:
        rep = build_effectiveness_report(
            before=source_before,
            after=source_after,
            patch_kind=patch_kind,
            old_text=old_text,
            reject_reason="unchanged_target_region",
        )
        rep["patch_effective_change"] = False
        rep["rejected_for_noop_or_unchanged"] = True
        return False, "unchanged_target_region", {"patch_effectiveness_step": rep}
    if module_append_code is not None:
        ok_m, r = module_append_is_meaningful(module_append_code, source_before)
        if not ok_m:
            rr = r or "no_meaningful_diff"
            rep = build_effectiveness_report(
                before=source_before,
                after=source_after,
                patch_kind=patch_kind,
                old_text=old_text,
                reject_reason=rr,
            )
            rep["patch_effective_change"] = False
            rep["patch_effective_reason"] = rr
            rep["rejected_for_noop_or_unchanged"] = True
            return False, rr, {"patch_effectiveness_step": rep}
    rep = build_effectiveness_report(
        before=source_before,
        after=source_after,
        patch_kind=patch_kind,
        old_text=old_text,
        reject_reason=None,
    )
    if rep["meaningful_diff_line_count"] < 1:
        rep["patch_effective_change"] = False
        rep["patch_effective_reason"] = "no_meaningful_diff"
        rep["rejected_for_noop_or_unchanged"] = True
        return False, "no_meaningful_diff", {"patch_effectiveness_step": rep}
    return True, None, {"patch_effectiveness_step": rep}
