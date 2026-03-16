"""Detect symbol-like queries that should bypass the cross-encoder reranker.

Symbol queries (exact function names, class names, filenames) are better
served by lexical + graph retrieval than by a semantic cross-encoder, which
adds latency without improving relevance for exact-match lookups.
"""

from __future__ import annotations

import re

# File extension pattern (e.g. foo.py, bar.ts, config.json)
_FILE_EXT_RE = re.compile(
    r"\b\w[\w\-]*\.(?:py|ts|tsx|js|jsx|json|yaml|yml|toml|cfg|md|txt|sh|go|rs|java|cpp|c|h)\b",
    re.IGNORECASE,
)

# CamelCase identifier (class / type names)
_CAMEL_CASE_RE = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+\b")

# snake_case with optional trailing parens (function name or call)
_SNAKE_FUNC_RE = re.compile(r"\b[a-z_][a-z0-9_]{2,}(?:\(\))?\b")

# Python / JS keyword prefixes that signal a symbol lookup
_KEYWORD_PREFIX_RE = re.compile(
    r"^\s*(?:def |class |import |from |function |const |let |var |type |interface )",
    re.IGNORECASE,
)

# Single-word queries of reasonable length — likely a symbol name
_SINGLE_WORD_RE = re.compile(r"^\s*\w{2,40}\s*$")


def is_symbol_query(query: str) -> tuple[bool, str]:
    """Return (bypass, reason) for the given query.

    bypass=True means the reranker should be skipped in favour of the
    existing lexical + graph retrieval path.
    """
    if not query or not query.strip():
        return False, ""

    stripped = query.strip()

    if _FILE_EXT_RE.search(stripped):
        return True, "filename_pattern"

    if _KEYWORD_PREFIX_RE.match(stripped):
        return True, "keyword_prefix"

    if _CAMEL_CASE_RE.search(stripped):
        return True, "camel_case_identifier"

    # Short single-word queries that look like snake_case symbols
    if _SINGLE_WORD_RE.match(stripped) and "_" in stripped:
        return True, "snake_case_symbol"

    # Bare single word (no spaces, no special chars) — probably a symbol name
    if _SINGLE_WORD_RE.match(stripped) and " " not in stripped:
        return True, "single_word_symbol"

    return False, ""
