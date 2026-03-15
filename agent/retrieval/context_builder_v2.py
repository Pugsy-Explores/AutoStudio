"""
Context builder v2: assemble reasoning context in anchored FILE/SYMBOL/LINES/SNIPPET format.
"""

import logging

from config.retrieval_config import DEFAULT_MAX_CHARS

logger = logging.getLogger(__name__)


def assemble_reasoning_context(
    snippets: list[dict],
    max_chars: int = DEFAULT_MAX_CHARS,
) -> str:
    """
    Assemble reasoning context from snippets in anchored format.

    Output format:
        FILE: executor.py
        SYMBOL: StepExecutor
        LINES: 40-80

        SNIPPET:
        class StepExecutor:
          ...

    Args:
        snippets: List of {file, symbol, snippet, line_range?} or {file, symbol, snippet, line?}.
        max_chars: Approximate token budget (char limit); stop when exceeded.

    Returns:
        Formatted string for reasoning prompt. Deduplicates by (file, symbol).
    """
    if not snippets:
        return ""

    seen: set[tuple[str, str]] = set()
    parts: list[str] = []
    total_chars = 0
    num_added = 0

    for c in snippets:
        if not isinstance(c, dict):
            continue
        file_path = c.get("file") or "(no file)"
        symbol = c.get("symbol") or ""
        key = (file_path, symbol)
        if key in seen:
            continue
        seen.add(key)

        snippet = (c.get("snippet") or "").strip()
        if not snippet:
            continue

        block_lines = [
            f"FILE: {file_path}",
        ]
        if symbol:
            block_lines.append(f"SYMBOL: {symbol}")

        line_val = c.get("line_range") or c.get("line")
        if line_val is not None:
            if isinstance(line_val, (list, tuple)) and len(line_val) >= 2:
                block_lines.append(f"LINES: {line_val[0]}-{line_val[1]}")
            else:
                block_lines.append(f"LINES: {line_val}-{line_val}")

        block_lines.append("")
        block_lines.append("SNIPPET:")
        block_lines.append(snippet[:800] + ("..." if len(snippet) > 800 else ""))

        block = "\n".join(block_lines)
        if total_chars + len(block) > max_chars:
            break
        total_chars += len(block)
        num_added += 1
        parts.append(block)
        parts.append("")

    if not parts:
        return ""

    result = "\n".join(parts).strip()
    logger.info("[context_builder_v2] %d snippets, %d chars", num_added, total_chars)
    return result + "\n\n"
