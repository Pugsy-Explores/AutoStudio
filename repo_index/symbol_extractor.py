"""Extract symbol definitions from Tree-sitter AST."""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

SYMBOL_TYPES = ("class", "function", "method", "module")


def _get_text(node, source_bytes: bytes) -> str:
    """Extract text for a node from source bytes."""
    if node is None or source_bytes is None:
        return ""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _get_docstring(node, source_bytes: bytes) -> str:
    """Extract docstring from function/class body. Handles single-line, triple-quoted, multi-line."""
    if node is None or source_bytes is None:
        return ""
    body = node.child_by_field_name("body")
    if body is None or body.child_count == 0:
        return ""
    first = body.child(0)
    if first.type == "expression_statement":
        expr = first.child(0)
        if expr.type == "string":
            raw = _get_text(expr, source_bytes)
            # Strip quotes (single, double, triple)
            raw = re.sub(r'^["\']{1,3}', "", raw)
            raw = re.sub(r'["\']{1,3}$', "", raw)
            return raw.strip()
    return ""


def _get_type_info(node, source_bytes: bytes) -> dict:
    """Extract type information from function/class: params and return_type."""
    info: dict = {"params": {}, "return_type": ""}
    if node is None or source_bytes is None:
        return info
    if node.type != "function_definition":
        return info
    params_node = node.child_by_field_name("parameters")
    if params_node:
        for child in params_node.children:
            if child.type == "typed_parameter":
                type_node = child.child_by_field_name("type")
                if type_node:
                    name_node = child.child(0) if child.child_count > 0 else None
                    if name_node and name_node.type == "identifier":
                        name = _get_text(name_node, source_bytes)
                        type_str = _get_text(type_node, source_bytes)
                        if name and not name.startswith("*"):
                            info["params"][name] = type_str
            elif child.type == "typed_default_parameter":
                name_node = child.child_by_field_name("name")
                type_node = child.child_by_field_name("type")
                if name_node and type_node:
                    name = _get_text(name_node, source_bytes)
                    type_str = _get_text(type_node, source_bytes)
                    if name:
                        info["params"][name] = type_str
    return_node = node.child_by_field_name("return_type")
    if return_node:
        info["return_type"] = _get_text(return_node, source_bytes).strip()
    return info


def _get_signature(node, source_bytes: bytes, name: str) -> str:
    """Extract function signature string (def name(params) -> return_type)."""
    if node is None or source_bytes is None or node.type != "function_definition":
        return ""
    params_node = node.child_by_field_name("parameters")
    params_str = _get_text(params_node, source_bytes) if params_node else "()"
    return_node = node.child_by_field_name("return_type")
    return_str = _get_text(return_node, source_bytes).strip() if return_node else ""
    if return_str:
        return f"def {name}{params_str} -> {return_str}"
    return f"def {name}{params_str}"


def _qualified_name(parent_name: str | None, name: str) -> str:
    """Build qualified symbol name."""
    if parent_name:
        return f"{parent_name}.{name}"
    return name


def extract_symbols(ast_tree, file_path: str, source_bytes: bytes | None = None) -> list[dict]:
    """
    Extract symbol definitions from AST tree.
    Returns list of {symbol_name, symbol_type, file, start_line, end_line, docstring}.
    symbol_type: "class" | "function" | "method" | "module"
    """
    if ast_tree is None:
        return []

    root = ast_tree.root_node
    if root is None:
        return []

    path_str = str(Path(file_path).resolve())
    if source_bytes is None:
        try:
            source_bytes = Path(file_path).read_bytes()
        except Exception:
            source_bytes = b""

    symbols: list[dict] = []
    # Module-level: treat file as a module
    module_name = Path(file_path).stem
    if module_name and module_name != "__init__":
        symbols.append({
            "symbol_name": module_name,
            "symbol_type": "module",
            "file": path_str,
            "start_line": 1,
            "end_line": 1,
            "docstring": "",
            "type_info": {},
            "signature": "",
        })

    def visit(node, parent_name: str | None = None):
        if node is None:
            return
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _get_text(name_node, source_bytes)
                if name:
                    sym_type = "method" if parent_name else "function"
                    type_info = _get_type_info(node, source_bytes)
                    signature = _get_signature(node, source_bytes, name)
                    symbols.append({
                        "symbol_name": _qualified_name(parent_name, name),
                        "symbol_type": sym_type,
                        "file": path_str,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "docstring": _get_docstring(node, source_bytes),
                        "type_info": type_info,
                        "signature": signature,
                    })
                    scope = _qualified_name(parent_name, name)
                    body = node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            visit(child, scope)
        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _get_text(name_node, source_bytes)
                if name:
                    symbols.append({
                        "symbol_name": _qualified_name(parent_name, name),
                        "symbol_type": "class",
                        "file": path_str,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "docstring": _get_docstring(node, source_bytes),
                        "type_info": {},
                        "signature": "",
                    })
                    scope = _qualified_name(parent_name, name)
                    body = node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            visit(child, scope)
        else:
            for child in node.children:
                visit(child, parent_name)

    for child in root.children:
        visit(child, None)

    return symbols
