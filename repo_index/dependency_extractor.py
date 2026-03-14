"""Extract dependency edges: imports, calls, inheritance, references."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

RELATION_TYPES = ("imports", "calls", "call_graph", "inherits", "references", "control_flow", "data_flow")


def _get_text(node, source_bytes: bytes) -> str:
    if node is None or source_bytes is None:
        return ""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _build_symbol_map(symbols: list[dict]) -> dict[tuple[str, int], str]:
    """Map (file, start_line) -> symbol_name for containment lookup."""
    m: dict[tuple[str, int], str] = {}
    for s in symbols:
        path = s.get("file", "")
        line = s.get("start_line", 0)
        name = s.get("symbol_name", "")
        if path and name:
            m[(path, line)] = name
    return m


def _find_containing_symbol(line: int, file_path: str, symbol_map: dict, symbols: list) -> str | None:
    """Find the innermost symbol that contains the given line in the file."""
    candidates = [(s["start_line"], s["end_line"], s["symbol_name"]) for s in symbols if s.get("file") == file_path]
    candidates = [(s, e, n) for s, e, n in candidates if s <= line <= e]
    if not candidates:
        return None
    # Prefer innermost (smallest range)
    candidates.sort(key=lambda x: (x[1] - x[0], x[0]))
    return candidates[0][2]


def _extract_imports(node, source_bytes: bytes, file_path: str, line: int, symbol_map: dict, symbols: list) -> list[dict]:
    """Extract import edges from import/import_from nodes."""
    edges = []
    source = _find_containing_symbol(line, file_path, symbol_map, symbols)
    if not source:
        module_name = Path(file_path).stem
        source = module_name

    if node.type == "import_statement":
        # import x, y
        for child in node.children:
            if child.type == "dotted_name":
                target = _get_text(child, source_bytes)
                if target:
                    edges.append({"source_symbol": source, "target_symbol": target, "relation_type": "imports"})
    elif node.type == "import_from_statement":
        # from x import y, z
        module_node = node.child_by_field_name("module_name")
        module = _get_text(module_node, source_bytes) if module_node else ""
        for child in node.children:
            if child.type == "dotted_name":
                # Could be alias: y as z
                target = _get_text(child, source_bytes)
                if target and target != module:
                    full_target = f"{module}.{target}" if module else target
                    edges.append({"source_symbol": source, "target_symbol": full_target, "relation_type": "imports"})
        if module and not any(e["target_symbol"].startswith(module) for e in edges):
            edges.append({"source_symbol": source, "target_symbol": module, "relation_type": "imports"})

    return edges


def _extract_calls(node, source_bytes: bytes, file_path: str, line: int, symbol_map: dict, symbols: list) -> list[dict]:
    """Extract call edges from call nodes."""
    edges = []
    source = _find_containing_symbol(line, file_path, symbol_map, symbols)
    if not source:
        module_name = Path(file_path).stem
        source = module_name

    if node.type == "call":
        func_node = node.child_by_field_name("function")
        if func_node:
            if func_node.type == "identifier":
                target = _get_text(func_node, source_bytes)
                if target:
                    edges.append({"source_symbol": source, "target_symbol": target, "relation_type": "calls"})
                    edges.append({"source_symbol": source, "target_symbol": target, "relation_type": "call_graph"})
            elif func_node.type == "attribute":
                # a.b()
                attr = func_node.child_by_field_name("attribute")
                if attr:
                    target = _get_text(attr, source_bytes)
                    if target:
                        edges.append({"source_symbol": source, "target_symbol": target, "relation_type": "calls"})
                        edges.append({"source_symbol": source, "target_symbol": target, "relation_type": "call_graph"})

    return edges


def _extract_control_flow(node, source_bytes: bytes, file_path: str, line: int, symbol_map: dict, symbols: list) -> list[dict]:
    """Extract control flow edges: condition symbols in if/for/while/try."""
    edges = []
    source = _find_containing_symbol(line, file_path, symbol_map, symbols)
    if not source:
        module_name = Path(file_path).stem
        source = module_name

    def extract_from_condition(cond_node):
        if cond_node is None:
            return
        if cond_node.type == "identifier":
            target = _get_text(cond_node, source_bytes)
            if target:
                edges.append({"source_symbol": source, "target_symbol": target, "relation_type": "control_flow"})
        elif cond_node.type == "call":
            func = cond_node.child_by_field_name("function")
            if func and func.type == "identifier":
                target = _get_text(func, source_bytes)
                if target:
                    edges.append({"source_symbol": source, "target_symbol": target, "relation_type": "control_flow"})
        for c in cond_node.children:
            extract_from_condition(c)

    if node.type == "if_statement":
        cond = node.child_by_field_name("condition")
        extract_from_condition(cond)
    elif node.type == "while_statement":
        cond = node.child_by_field_name("condition")
        extract_from_condition(cond)
    elif node.type == "for_statement":
        iter_node = node.child_by_field_name("iterable")
        extract_from_condition(iter_node)

    return edges


def _extract_data_flow(node, source_bytes: bytes, file_path: str, line: int, symbol_map: dict, symbols: list) -> list[dict]:
    """Extract data flow edges: assignment target (variable being defined)."""
    edges = []
    source = _find_containing_symbol(line, file_path, symbol_map, symbols)
    if not source:
        return edges

    def get_assignment_target(n):
        if n is None:
            return None
        if n.type == "identifier":
            return _get_text(n, source_bytes)
        if n.type == "pattern" and n.child_count > 0:
            first = n.child(0)
            if first.type == "identifier":
                return _get_text(first, source_bytes)
        return None

    if node.type == "assignment":
        left = node.child_by_field_name("left")
        target = get_assignment_target(left)
        if target:
            edges.append({"source_symbol": source, "target_symbol": target, "relation_type": "data_flow"})
    elif node.type == "augmented_assignment":
        left = node.child_by_field_name("left")
        target = get_assignment_target(left)
        if target:
            edges.append({"source_symbol": source, "target_symbol": target, "relation_type": "data_flow"})

    return edges


def _extract_references(node, source_bytes: bytes, file_path: str, line: int, symbol_map: dict, symbols: list) -> list[dict]:
    """Extract reference edges: attribute access a.b references b."""
    edges = []
    source = _find_containing_symbol(line, file_path, symbol_map, symbols)
    if not source:
        return edges

    if node.type == "attribute":
        attr = node.child_by_field_name("attribute")
        if attr:
            target = _get_text(attr, source_bytes)
            if target:
                edges.append({"source_symbol": source, "target_symbol": target, "relation_type": "references"})

    return edges


def _extract_inheritance(node, source_bytes: bytes, file_path: str, line: int, symbol_map: dict, symbols: list) -> list[dict]:
    """Extract inheritance edges from class_definition."""
    edges = []
    name_node = node.child_by_field_name("name")
    if not name_node:
        return edges
    source = _get_text(name_node, source_bytes)
    if not source:
        return edges

    base_node = node.child_by_field_name("superclasses")
    if base_node:
        for child in base_node.children:
            if child.type in ("identifier", "dotted_name", "attribute"):
                target = _get_text(child, source_bytes)
                if target:
                    edges.append({"source_symbol": source, "target_symbol": target, "relation_type": "inherits"})

    return edges


def _walk_and_collect(node, source_bytes: bytes, file_path: str, symbol_map: dict, symbols: list, edges: list):
    """Recursively walk AST and collect edges."""
    if node is None:
        return
    line = node.start_point[0] + 1

    if node.type in ("import_statement", "import_from_statement"):
        edges.extend(_extract_imports(node, source_bytes, file_path, line, symbol_map, symbols))
    elif node.type == "call":
        edges.extend(_extract_calls(node, source_bytes, file_path, line, symbol_map, symbols))
    elif node.type == "class_definition":
        edges.extend(_extract_inheritance(node, source_bytes, file_path, line, symbol_map, symbols))
    elif node.type in ("if_statement", "while_statement", "for_statement"):
        edges.extend(_extract_control_flow(node, source_bytes, file_path, line, symbol_map, symbols))
    elif node.type in ("assignment", "augmented_assignment"):
        edges.extend(_extract_data_flow(node, source_bytes, file_path, line, symbol_map, symbols))
    elif node.type == "attribute":
        edges.extend(_extract_references(node, source_bytes, file_path, line, symbol_map, symbols))

    for child in node.children:
        _walk_and_collect(child, source_bytes, file_path, symbol_map, symbols, edges)


def extract_edges_for_file(
    symbols: list[dict],
    tree: "Tree",
    file_path: str,
) -> list[dict]:
    """
    Extract dependency edges for a single file.
    symbols: symbol records for this file only.
    Returns list of {source_symbol, target_symbol, relation_type}.
    """
    symbol_map = _build_symbol_map(symbols)
    edges: list[dict] = []
    if tree is None or tree.root_node is None:
        return edges
    try:
        path = Path(file_path)
        if path.exists():
            source_bytes = path.read_bytes()
        else:
            source_bytes = b""
    except Exception:
        source_bytes = b""
    _walk_and_collect(tree.root_node, source_bytes, file_path, symbol_map, symbols, edges)
    return edges


def extract_edges(
    symbols: list[dict],
    ast_trees: dict[str, "Tree"],
    root_dir: str,
) -> list[dict]:
    """
    Extract dependency edges from symbols and AST trees.
    Returns list of {source_symbol, target_symbol, relation_type}.
    relation_type: "imports" | "calls" | "inherits" | "references"
    """
    symbol_map = _build_symbol_map(symbols)
    edges: list[dict] = []
    root = Path(root_dir).resolve()

    for file_path, tree in ast_trees.items():
        if tree is None or tree.root_node is None:
            logger.debug("[dependency_extractor] skipping %s: no valid tree", file_path)
            continue
        try:
            path = Path(file_path)
            if path.exists():
                source_bytes = path.read_bytes()
            else:
                source_bytes = b""
        except Exception:
            source_bytes = b""

        _walk_and_collect(tree.root_node, source_bytes, file_path, symbol_map, symbols, edges)

    logger.debug("[dependency_extractor] extracted %d edges from %d files", len(edges), len(ast_trees))
    return edges
