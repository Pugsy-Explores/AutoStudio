"""Tree-sitter parser for Python source files."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PARSER = None
_PY_LANGUAGE = None


def _get_parser():
    """Lazy-init parser and Python language."""
    global _PARSER, _PY_LANGUAGE
    if _PARSER is None:
        try:
            import tree_sitter_python as tspython
            from tree_sitter import Language, Parser

            _PY_LANGUAGE = Language(tspython.language())
            _PARSER = Parser(_PY_LANGUAGE)
        except ImportError as e:
            logger.warning("[parser] tree-sitter not available: %s", e)
            return None, None
    return _PARSER, _PY_LANGUAGE


def parse_file(file_path: str) -> "Tree | None":
    """
    Parse a Python file and return the AST tree.
    Returns None on parse error or if tree-sitter is unavailable.
    """
    parser, _ = _get_parser()
    if parser is None:
        return None

    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return None

    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        source_bytes = source.encode("utf-8")
        tree = parser.parse(source_bytes)
        return tree
    except Exception as e:
        logger.debug("[parser] parse error %s: %s", file_path, e)
        return None


def parse_source(source: str | bytes) -> "tuple[Tree, bytes] | None":
    """
    Parse Python source (str or bytes) and return (tree, source_bytes).
    Returns None on parse error or if tree-sitter is unavailable.
    """
    parser, _ = _get_parser()
    if parser is None:
        return None
    try:
        source_bytes = source.encode("utf-8") if isinstance(source, str) else source
        tree = parser.parse(source_bytes)
        if tree is None:
            return None
        return tree, source_bytes
    except Exception as e:
        logger.debug("[parser] parse_source error: %s", e)
        return None
