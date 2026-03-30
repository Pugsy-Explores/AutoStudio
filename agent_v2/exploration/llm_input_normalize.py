"""
Lossless, text-native formatting of exploration LLM inputs (Scoper, Selector, Analyzer).

See agent_v2/docs/llm_input_transformation_agent_v2.md. No upstream schema changes;
formatting only. Large fields use preview + full sections (no truncation of full copies).
"""

from __future__ import annotations

from typing import Any, Literal

PREVIEW_HEAD_LINES_DEFAULT = 20
PREVIEW_TAIL_LINES_DEFAULT = 10
PREVIEW_SPLIT_MARGIN = 2

_SCOPER_KNOWN = frozenset({"index", "file_path", "sources", "snippets", "symbols"})
_SELECTOR_PAYLOAD_KEYS = frozenset(
    {
        "file_path",
        "symbol",
        "source",
        "symbols",
        "snippet_summary",
        "source_channels",
        "outline_for_prompt",
        "repo",
    }
)
_CONTEXT_BLOCK_KNOWN = frozenset(
    {
        "file_path",
        "start",
        "end",
        "content",
        "origin_reason",
        "symbol",
        "relationship_refs",
    }
)


def _unit_delimiter(kind: Literal["CANDIDATE", "ITEM", "CONTEXT_BLOCK"], i: int, end: bool) -> str:
    edge = "END" if end else "START"
    label = {"CANDIDATE": "CANDIDATE", "ITEM": "ITEM", "CONTEXT_BLOCK": "CONTEXT BLOCK"}[kind]
    return f"----- {label} {i} {edge} ---"


def split_preview_full(
    text: str,
    head: int,
    tail: int,
    *,
    margin: int = PREVIEW_SPLIT_MARGIN,
) -> tuple[Literal["full_only", "preview_plus_full"], str, str, str]:
    """
    Returns (mode, first_lines_chunk, last_lines_chunk, full_text).
    first/last are empty when mode == full_only. full_text is always lossless.
    """
    if text == "":
        return ("full_only", "", "", "")
    lines = text.splitlines()
    n = len(lines)
    threshold = head + tail + margin
    if n <= threshold:
        return ("full_only", "", "", text)
    first = "\n".join(lines[:head])
    last = "\n".join(lines[-tail:]) if tail else ""
    return ("preview_plus_full", first, last, text)


def _emit_preview_then_full(
    lines: list[str],
    *,
    base_name: str,
    head: int,
    tail: int,
    first: str,
    last: str,
    full: str,
    mode: Literal["full_only", "preview_plus_full"],
) -> None:
    if mode == "full_only":
        _emit_multiline_key(lines, base_name, full)
        return
    lines.append(f"{base_name}_preview:")
    lines.append(f"  first_{head}_lines: |")
    if not first:
        lines.append("    ")
    else:
        for line in first.splitlines():
            lines.append(f"    {line}")
    lines.append(f"  last_{tail}_lines: |")
    if not last:
        lines.append("    ")
    else:
        for line in last.splitlines():
            lines.append(f"    {line}")
    _emit_multiline_key(lines, f"{base_name}_full", full)


def _scalar_plain(v: Any) -> str:
    if v is None:
        return ""
    s = str(v)
    if "\n" in s:
        return s.replace("\n", "\\n")
    return s


def _render_extra_fields_text_native(obj: Any, indent: str = "  ") -> list[str]:
    """Structured text lines for extra_fields (no JSON)."""
    lines: list[str] = []
    if obj is None:
        lines.append(f"{indent}(null)")
        return lines
    if isinstance(obj, dict):
        for k in sorted(obj.keys(), key=lambda x: str(x)):
            v = obj[k]
            key = str(k)
            if isinstance(v, dict):
                lines.append(f"{indent}{key}:")
                lines.extend(_render_extra_fields_text_native(v, indent + "  "))
            elif isinstance(v, list):
                lines.append(f"{indent}{key}:")
                for item in v:
                    if isinstance(item, dict):
                        lines.append(f"{indent}  -")
                        for ik in sorted(item.keys(), key=lambda x: str(x)):
                            lines.append(f"{indent}    {ik}: {_scalar_plain(item[ik])}")
                    else:
                        lines.append(f"{indent}  - {_scalar_plain(item)}")
            else:
                lines.extend(_scalar_key_value_lines(key, v, indent))
        return lines
    if isinstance(obj, list):
        for item in obj:
            lines.append(f"{indent}- {_scalar_plain(item)}")
        return lines
    lines.append(f"{indent}{_scalar_plain(obj)}")
    return lines


def _scalar_key_value_lines(key: str, val: Any, indent: str) -> list[str]:
    if val is None:
        return [f"{indent}{key}:"]
    if isinstance(val, bool):
        return [f"{indent}{key}: {str(val).lower()}"]
    s = str(val)
    if "\n" not in s:
        return [f"{indent}{key}: {s}"]
    return [f"{indent}{key}: |"] + [f"{indent}  {line}" for line in s.splitlines()]


def _emit_multiline_key(lines_out: list[str], key: str, text: str, indent: str = "") -> None:
    lines_out.append(f"{indent}{key}: |")
    if text == "":
        lines_out.append(f"{indent}  ")
        return
    for line in text.splitlines():
        lines_out.append(f"{indent}  {line}")


def _emit_scoper_snippets(
    lines_out: list[str],
    snippets: list[Any],
    head: int,
    tail: int,
) -> None:
    """Always emit snippets_full; add snippets_preview for long entries."""
    lines_out.append("snippets_full:")
    preview_blocks: list[str] = []
    for j, raw in enumerate(snippets):
        s = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
        lines_out.append("  - |")
        if not s:
            lines_out.append("      ")
        else:
            for line in s.splitlines():
                lines_out.append(f"      {line}")
        mode, first_c, last_c, _full = split_preview_full(s, head, tail)
        if mode == "preview_plus_full" and (first_c or last_c):
            pb: list[str] = [f"  - index: {j}"]
            pb.append(f"    first_{head}_lines: |")
            for line in (first_c or "").splitlines():
                pb.append(f"      {line}")
            if not (first_c or "").splitlines():
                pb.append("      ")
            pb.append(f"    last_{tail}_lines: |")
            for line in (last_c or "").splitlines():
                pb.append(f"      {line}")
            if not (last_c or "").splitlines():
                pb.append("      ")
            preview_blocks.append("\n".join(pb))
    if preview_blocks:
        lines_out.append("snippets_preview:")
        for pb in preview_blocks:
            lines_out.extend(pb.splitlines())


def normalize_scoper(
    *,
    instruction: str,
    rows: list[dict[str, Any]],
    preview_line_limits: tuple[int, int] = (
        PREVIEW_HEAD_LINES_DEFAULT,
        PREVIEW_TAIL_LINES_DEFAULT,
    ),
) -> str:
    head, tail = preview_line_limits
    out: list[str] = ["[Global]", f"instruction: {instruction}", ""]
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            row = {"value": row}
        out.append(_unit_delimiter("CANDIDATE", i, False))
        known = {k: row[k] for k in _SCOPER_KNOWN if k in row}
        extra = {k: v for k, v in row.items() if k not in _SCOPER_KNOWN}
        if "index" in known:
            out.append(f"index: {known['index']}")
        if "file_path" in known:
            out.append(f"file_path: {known['file_path']}")
        if "sources" in known:
            out.append("sources:")
            for s in list(known["sources"] or []):
                out.append(f"  - {s}")
        if "snippets" in known:
            _emit_scoper_snippets(out, list(known["snippets"] or []), head, tail)
        if "symbols" in known:
            out.append("symbols:")
            for sym in list(known["symbols"] or []):
                out.append(f"  - {sym if sym is not None else ''}")
        if extra:
            out.append("extra_fields:")
            out.extend(_render_extra_fields_text_native(extra, "  "))
        out.append(_unit_delimiter("CANDIDATE", i, True))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _merge_selector_item(row_index: int, raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    extra = {k: v for k, v in raw.items() if k not in _SELECTOR_PAYLOAD_KEYS}
    merged: dict[str, Any] = {
        "id": row_index,
        "file_path": raw.get("file_path", "") or "",
        "symbol": raw.get("symbol", "") or "",
        "source": raw.get("source", "") or "",
        "symbols": list(raw.get("symbols") or []),
        "snippet_summary": raw.get("snippet_summary") or "",
        "source_channels": list(raw.get("source_channels") or []),
        "outline_for_prompt": list(raw.get("outline_for_prompt") or []),
        "repo": raw.get("repo", "") or "",
    }
    return merged, extra


def _format_selector_item_body(
    merged: dict[str, Any],
    extra: dict[str, Any],
    head: int,
    tail: int,
) -> list[str]:
    lines: list[str] = []
    lines.append(f"id: {merged['id']}")
    lines.append(f"file_path: {merged['file_path']}")
    lines.append(f"symbol: {merged['symbol']}")
    lines.append(f"source: {merged['source']}")
    lines.append("symbols:")
    for x in merged["symbols"]:
        lines.append(f"  - {x}")
    ss = str(merged.get("snippet_summary") or "")
    mode, first_c, last_c, full = split_preview_full(ss, head, tail)
    _emit_preview_then_full(
        lines,
        base_name="snippet_summary",
        head=head,
        tail=tail,
        first=first_c,
        last=last_c,
        full=full,
        mode=mode,
    )
    lines.append("source_channels:")
    for x in merged["source_channels"]:
        lines.append(f"  - {x}")
    lines.append("outline_for_prompt:")
    for entry in merged["outline_for_prompt"]:
        if isinstance(entry, dict):
            lines.append("  -")
            for dk in sorted(entry.keys()):
                lines.append(f"      {dk}: {entry[dk]}")
        else:
            lines.append(f"  - {entry}")
    lines.append(f"repo: {merged['repo']}")
    if extra:
        lines.append("extra_fields:")
        lines.extend(_render_extra_fields_text_native(extra, "  "))
    return lines


def normalize_selector_batch(
    *,
    instruction: str,
    intent: str,
    limit: int,
    explored_block: str,
    items: list[dict[str, Any]],
    preview_line_limits: tuple[int, int] = (
        PREVIEW_HEAD_LINES_DEFAULT,
        PREVIEW_TAIL_LINES_DEFAULT,
    ),
) -> str:
    head, tail = preview_line_limits
    out: list[str] = [
        "[Global]",
        f"instruction: {instruction}",
        f"intent: {intent}",
        f"limit: {limit}",
    ]
    _emit_multiline_key(out, "explored_block", explored_block or "")
    out.append("")
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            raw = {"value": raw}
        merged, extra = _merge_selector_item(i, raw)
        out.append(_unit_delimiter("ITEM", i, False))
        out.extend(_format_selector_item_body(merged, extra, head, tail))
        out.append(_unit_delimiter("ITEM", i, True))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def normalize_selector_single(
    *,
    items: list[dict[str, Any]],
    preview_line_limits: tuple[int, int] = (
        PREVIEW_HEAD_LINES_DEFAULT,
        PREVIEW_TAIL_LINES_DEFAULT,
    ),
) -> str:
    """Item blocks only (no [Global]); use when instruction lives in the prompt template."""
    head, tail = preview_line_limits
    out: list[str] = []
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            raw = {"value": raw}
        merged, extra = _merge_selector_item(i, raw)
        out.append(_unit_delimiter("ITEM", i, False))
        out.extend(_format_selector_item_body(merged, extra, head, tail))
        out.append(_unit_delimiter("ITEM", i, True))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def format_explored_locations_for_prompt(
    explored_location_keys: set[tuple[str, str]] | None,
    *,
    max_rows: int,
) -> str:
    """Text-native list of inspected locations (replaces JSON in explored_block)."""
    if not explored_location_keys:
        return ""
    rows = sorted(explored_location_keys, key=lambda t: (t[0], t[1]))[:max_rows]
    lines = [
        "Locations already inspected in this run (choose different file/symbol pairs "
        "unless no alternative exists):",
    ]
    for fp, sym in rows:
        lines.append("  -")
        lines.append(f"      file_path: {fp}")
        lines.append(f"      symbol: {sym or ''}")
    return "\n".join(lines)


def _format_context_block_body(
    blk: dict[str, Any],
    head: int,
    tail: int,
) -> list[str]:
    lines: list[str] = []
    known = {k: blk[k] for k in _CONTEXT_BLOCK_KNOWN if k in blk}
    extra = {k: v for k, v in blk.items() if k not in _CONTEXT_BLOCK_KNOWN}
    for key in ("file_path", "start", "end", "symbol", "origin_reason"):
        if key in known:
            lines.append(f"{key}: {known[key]}")
    if "relationship_refs" in known:
        lines.append("relationship_refs:")
        for r in list(known.get("relationship_refs") or []):
            lines.append(f"  - {r}")
    content = str(known.get("content") or "")
    mode, first_c, last_c, full = split_preview_full(content, head, tail)
    _emit_preview_then_full(
        lines,
        base_name="content",
        head=head,
        tail=tail,
        first=first_c,
        last=last_c,
        full=full,
        mode=mode,
    )
    if extra:
        lines.append("extra_fields:")
        lines.extend(_render_extra_fields_text_native(extra, "  "))
    return lines


def normalize_analyzer(
    *,
    instruction: str,
    intent: str,
    task_intent_summary: str,
    file_path: str,
    snippet: str,
    symbol_relationships_block: str,
    context_blocks: list[dict[str, Any]],
    preview_line_limits: tuple[int, int] = (
        PREVIEW_HEAD_LINES_DEFAULT,
        PREVIEW_TAIL_LINES_DEFAULT,
    ),
    upstream_selection_confidence: str | None = None,
) -> str:
    head, tail = preview_line_limits
    out: list[str] = [
        "[Global]",
        f"instruction: {instruction}",
        f"intent: {intent}",
        f"task_intent_summary: {task_intent_summary}",
        f"file_path: {file_path}",
    ]
    usc = str(upstream_selection_confidence or "").strip().lower()
    if usc in ("high", "medium", "low"):
        out.append(f"upstream_selection_confidence: {usc}")
    else:
        out.append("upstream_selection_confidence: (not provided)")
    smode, sfirst, slast, sfull = split_preview_full(snippet or "", head, tail)
    _emit_preview_then_full(
        out,
        base_name="snippet",
        head=head,
        tail=tail,
        first=sfirst,
        last=slast,
        full=sfull,
        mode=smode,
    )
    out.append("")
    out.append("[Relationships]")
    _emit_multiline_key(out, "symbol_relationships_block", symbol_relationships_block or "")
    out.append("")
    for i, blk in enumerate(context_blocks[:6]):
        if not isinstance(blk, dict):
            blk = {"value": blk}
        out.append(_unit_delimiter("CONTEXT_BLOCK", i, False))
        out.extend(_format_context_block_body(blk, head, tail))
        out.append(_unit_delimiter("CONTEXT_BLOCK", i, True))
        out.append("")
    return "\n".join(out).rstrip() + "\n"
