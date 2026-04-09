"""
Bounded, deterministic code injection for the exploration batch selector prompt.

Only transforms how outline rows are presented (sort + full vs signature + trim marker).
Does not change symbol extraction or selection logic.
"""

from __future__ import annotations

import re
from typing import Any

SELECTOR_CODE_TRIM_MARKER = "[CODE TRIMMED DUE TO SIZE LIMIT]"
TRIMMED_LINE_PREFIX = "[trimmed] "

SELECTOR_CODE_TRIM_NOTICE = (
    f"{SELECTOR_CODE_TRIM_MARKER}\n\n"
    "Some implementations are omitted.\n"
    "Only signatures are shown for remaining symbols."
)
ANALYZER_CODE_TRIM_MARKER = "[CODE TRIMMED IN ANALYZER CONTEXT]"
ANALYZER_CODE_TRIM_NOTICE = (
    f"{ANALYZER_CODE_TRIM_MARKER}\n\n"
    "Some implementations are omitted.\n"
    "Only signatures are shown for remaining symbols."
)


def sort_outline_rows_for_selector(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Deterministic ordering: alphabetical by symbol name, then type, then start_line.
    """
    if not rows:
        return []

    def _key(r: dict[str, Any]) -> tuple[str, str, str]:
        name = str(r.get("name") or "")
        st = str(r.get("type") or "")
        sl = str(r.get("start_line") or "")
        return (name.lower(), st, sl)

    return sorted([dict(x) for x in rows], key=_key)


def _first_def_or_class_line(code: str) -> str:
    """First logical line for a function/method/class body snippet."""
    for line in (code or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped
    return ""


_DEF_LINE = re.compile(r"^(async\s+def\s+\w+|def\s+\w+|class\s+\w+)", re.MULTILINE)


def _signature_function_or_method(code: str) -> str:
    line = _first_def_or_class_line(code)
    if line.endswith(":"):
        return line
    if line and not line.endswith(":"):
        return f"{line}:"
    return line


def _class_basename(name: str) -> str:
    return name.split(".")[0] if name else ""


def _signature_class(
    row: dict[str, Any],
    sorted_rows: list[dict[str, Any]],
) -> str:
    code = str(row.get("code") or "")
    name = str(row.get("name") or "")
    base = _class_basename(name)
    header = _first_def_or_class_line(code)
    if not header:
        header = f"class {base}:"
    elif not header.startswith("class "):
        m = _DEF_LINE.search(code)
        header = m.group(0).rstrip() if m else f"class {base}:"
        if not header.endswith(":"):
            header = f"{header}:"
    lines_out = [header]
    methods: list[tuple[str, str]] = []
    prefix = f"{base}."
    for r in sorted_rows:
        if str(r.get("type") or "") != "method":
            continue
        mn = str(r.get("name") or "")
        if not mn.startswith(prefix):
            continue
        sig = _signature_function_or_method(str(r.get("code") or ""))
        if sig:
            methods.append((mn.lower(), sig))
    methods.sort(key=lambda x: x[0])
    for _, sig in methods:
        lines_out.append(f"    {sig}")
    return "\n".join(lines_out)


def _signature_for_row(
    row: dict[str, Any],
    sorted_rows: list[dict[str, Any]],
) -> str:
    st = str(row.get("type") or "")
    code = str(row.get("code") or "")
    if st == "class":
        return _signature_class(row, sorted_rows)
    return _signature_function_or_method(code)


def build_outline_signatures_only(rows: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Deterministic signatures-only outline rows (all symbols preserved)."""
    if not rows:
        return []
    sorted_rows = sort_outline_rows_for_selector(rows)
    out: list[dict[str, str]] = []
    for r in sorted_rows:
        d = {kk: str(vv) if vv is not None else "" for kk, vv in r.items()}
        d["code"] = _mark_signature_trimmed(_signature_for_row(r, sorted_rows))
        out.append(d)
    return out


def _mark_signature_trimmed(signature: str) -> str:
    """
    Prefix each non-empty signature line with `[trimmed]` while preserving indentation.
    """
    out: list[str] = []
    for line in (signature or "").splitlines():
        if not line.strip():
            out.append(line)
            continue
        indent = line[: len(line) - len(line.lstrip(" "))]
        body = line[len(indent) :]
        out.append(f"{indent}{TRIMMED_LINE_PREFIX}{body}")
    return "\n".join(out)


def _full_code_chars(rows: list[dict[str, Any]]) -> int:
    return sum(len(str(r.get("code") or "")) for r in rows)


def _notice_prefix_len(trim_notice: str) -> int:
    """Bytes/chars added once before the first signature-only ``code`` when trimming."""
    return len(trim_notice) + 2


def _total_injected_code_len_for_k(
    k: int,
    *,
    n: int,
    full_lens: list[int],
    sig_lens: list[int],
    trim_notice: str = SELECTOR_CODE_TRIM_NOTICE,
) -> int:
    """
    Total length of all ``code`` fields if the first ``k`` symbols keep full bodies and
    ``n-k`` trailing symbols use signatures, with trim notice on the first signature row.
    """
    if k < 0 or k > n:
        return 10**18
    prefix = sum(full_lens[:k])
    if k >= n:
        return prefix
    rest = sum(sig_lens[k:])
    return prefix + _notice_prefix_len(trim_notice) + rest


def build_bounded_symbol_context(
    rows: list[dict[str, Any]] | None,
    *,
    max_code_chars: int,
    trim_notice: str = SELECTOR_CODE_TRIM_NOTICE,
) -> list[dict[str, str]]:
    """
    Shared deterministic builder used by selector prompt injection and analyzer symbol
    expansion:
    - alphabetical sort
    - full code prefix until budget
    - signature-only overflow (with `[trimmed]` prefix per line)
    - one trim notice on first overflow row
    """
    if not rows:
        return []
    sorted_rows = sort_outline_rows_for_selector(rows)
    n = len(sorted_rows)
    full_lens = [len(str(r.get("code") or "")) for r in sorted_rows]
    sig_lens = [len(_mark_signature_trimmed(_signature_for_row(r, sorted_rows))) for r in sorted_rows]

    if (
        _total_injected_code_len_for_k(
            n, n=n, full_lens=full_lens, sig_lens=sig_lens, trim_notice=trim_notice
        )
        <= max_code_chars
    ):
        return [{kk: str(vv) if vv is not None else "" for kk, vv in row.items()} for row in sorted_rows]

    best_k = 0
    for k in range(n, -1, -1):
        if (
            _total_injected_code_len_for_k(
                k, n=n, full_lens=full_lens, sig_lens=sig_lens, trim_notice=trim_notice
            )
            <= max_code_chars
        ):
            best_k = k
            break

    out_rows: list[dict[str, str]] = []
    for i in range(best_k):
        r = sorted_rows[i]
        out_rows.append({kk: str(vv) if vv is not None else "" for kk, vv in r.items()})

    for i in range(best_k, n):
        r = sorted_rows[i]
        d = {kk: str(vv) if vv is not None else "" for kk, vv in r.items()}
        sig = _mark_signature_trimmed(_signature_for_row(r, sorted_rows))
        if i == best_k:
            d["code"] = f"{trim_notice}\n\n{sig}"
        else:
            d["code"] = sig
        out_rows.append(d)
    return out_rows


def prepare_outline_for_selector_prompt(
    rows: list[dict[str, Any]] | None,
    max_code_chars: int,
) -> list[dict[str, str]]:
    """
    Sort alphabetically, then choose the **largest** ``k`` such that the combined ``code``
    payload (``k`` full bodies + trim notice + signatures for the rest) fits in
    ``max_code_chars``. If even ``k=0`` does not fit, still emit all symbols (signatures
    only); total length may exceed the cap — symbols are never dropped.

    When trimming occurs, prepends SELECTOR_CODE_TRIM_NOTICE to the first signature-only
    entry's ``code`` (marker visible once).

    All input symbols appear in the output (full or signature); none are dropped.
    """
    return build_bounded_symbol_context(
        rows,
        max_code_chars=max_code_chars,
        trim_notice=SELECTOR_CODE_TRIM_NOTICE,
    )


def total_selector_outline_code_chars(rows: list[dict[str, Any]] | None) -> int:
    """Sum of ``code`` field lengths (for tests and observability)."""
    if not rows:
        return 0
    return _full_code_chars(rows)
