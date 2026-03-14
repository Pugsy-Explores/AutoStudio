"""Apply structured edits to code using Tree-sitter AST.

Supports:
- Symbol-level: function_body_start, function_body, class_body_start, class_body
- Statement-level: statement (replace/delete), statement_after (insert)
- Block replacements: if_block, try_block, with_block, for_block
- Variable renaming: action=rename with old_name, new_name (scope: symbol)
"""

import logging
from pathlib import Path

from repo_index.parser import parse_file, parse_source

logger = logging.getLogger(__name__)

TARGET_NODES = (
    "function_body_start",
    "function_body",
    "class_body_start",
    "class_body",
    "statement",
    "statement_after",
    "if_block",
    "try_block",
    "with_block",
    "for_block",
)


def _get_text(node, source_bytes: bytes) -> str:
    """Extract text for a node from source bytes."""
    if node is None or source_bytes is None:
        return ""
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _get_base_indent(source_bytes: bytes, body_node) -> str:
    """Get the indentation of the first line in the body block."""
    if body_node is None or body_node.child_count == 0:
        return "    "
    first_child = body_node.child(0)
    line_start = source_bytes.rfind(b"\n", 0, first_child.start_byte) + 1
    indent_bytes = source_bytes[line_start:first_child.start_byte]
    decoded = indent_bytes.decode("utf-8", errors="replace")
    # Only whitespace counts as indent; strip any non-space
    return "".join(c for c in decoded if c in " \t") or "    "


def _indent_code(code: str, base_indent: str) -> str:
    """Add base_indent to each line of code (except empty lines).
    Preserves relative indentation: strips common leading indent, then adds base_indent."""
    lines = code.strip().split("\n")
    if not lines:
        return ""
    # Find minimum indent of non-empty lines
    min_indent = float("inf")
    for line in lines:
        if line.strip():
            indent_len = len(line) - len(line.lstrip())
            min_indent = min(min_indent, indent_len)
    min_indent = int(min_indent) if min_indent != float("inf") else 0
    result = []
    for line in lines:
        if line.strip():
            dedented = line[min_indent:] if len(line) >= min_indent else line.lstrip()
            result.append(base_indent + dedented)
        else:
            result.append("")
    return "\n".join(result) + "\n" if result else ""


def _find_symbol_node(root, source_bytes: bytes, symbol: str):
    """Find function_definition or class_definition node matching symbol.
    Returns (node, qualified_name) or (None, None).
    Symbol can be short name (e.g. 'bar') or qualified (e.g. 'Foo.bar').
    """

    def visit(node, parent_name: str | None = None):
        if node is None:
            return None, None
        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _get_text(name_node, source_bytes)
                if name:
                    qualified = f"{parent_name}.{name}" if parent_name else name
                    short = name
                    if symbol == qualified or symbol == short or symbol.endswith("." + short):
                        return node, qualified
                    body = node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            found, _ = visit(child, qualified)
                            if found is not None:
                                return found, qualified
        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node:
                name = _get_text(name_node, source_bytes)
                if name:
                    qualified = f"{parent_name}.{name}" if parent_name else name
                    short = name
                    if symbol == qualified or symbol == short or symbol.endswith("." + short):
                        return node, qualified
                    body = node.child_by_field_name("body")
                    if body:
                        for child in body.children:
                            found, _ = visit(child, qualified)
                            if found is not None:
                                return found, qualified
        for child in node.children:
            found, qn = visit(child, parent_name)
            if found is not None:
                return found, qn
        return None, None

    for child in root.children:
        found, _ = visit(child, None)
        if found is not None:
            return found, None
    return None, None


def load_ast(file_path: str) -> tuple | None:
    """
    Parse a Python file and return (tree, source_bytes).
    Returns None on parse error.
    """
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        return None
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        source_bytes = source.encode("utf-8")
        tree = parse_file(file_path)
        if tree is None:
            return None
        return tree, source_bytes
    except Exception as e:
        logger.debug("[ast_patcher] load_ast error %s: %s", file_path, e)
        return None


def load_ast_from_source(source: str | bytes) -> tuple | None:
    """
    Parse Python source and return (tree, source_bytes).
    Returns None on parse error.
    """
    return parse_source(source)


def _get_body_statements(body) -> list:
    """Return direct statement children of a block (excluding block wrapper if any)."""
    if body is None:
        return []
    # Block's children are statements
    return [body.child(i) for i in range(body.child_count)]


def _find_block_by_type(body, block_type: str, block_index: int, source_bytes: bytes):
    """
    Find block of given type within body. block_type: if_block, try_block, with_block, for_block.
    Returns the inner block node (consequence/body) or None.
    """
    statements = _get_body_statements(body)
    count = 0
    for stmt in statements:
        if stmt is None:
            continue
        inner_block = None
        if block_type == "if_block" and stmt.type == "if_statement":
            inner_block = stmt.child_by_field_name("consequence")
        elif block_type == "try_block" and stmt.type == "try_statement":
            inner_block = stmt.child_by_field_name("body")
        elif block_type == "with_block" and stmt.type == "with_statement":
            inner_block = stmt.child_by_field_name("body")
        elif block_type == "for_block" and stmt.type == "for_statement":
            inner_block = stmt.child_by_field_name("body")
        if inner_block is not None:
            if count == block_index:
                return inner_block
            count += 1
    return None


def _rename_identifiers_in_scope(source_bytes: bytes, scope_node, old_name: str, new_name: str) -> bytes:
    """Replace all identifier occurrences of old_name with new_name within scope_node."""
    if not old_name or old_name == new_name:
        return source_bytes
    old_b = old_name.encode("utf-8")
    new_b = new_name.encode("utf-8")

    def collect_identifier_ranges(node):
        ranges = []
        if node is None:
            return ranges
        if node.type == "identifier":
            text = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
            if text == old_name:
                ranges.append((node.start_byte, node.end_byte))
        for i in range(node.child_count):
            ranges.extend(collect_identifier_ranges(node.child(i)))
        return ranges

    ranges = collect_identifier_ranges(scope_node)
    if not ranges:
        return source_bytes
    # Sort by start_byte descending so we replace from end to avoid offset shifts
    ranges.sort(key=lambda r: r[0], reverse=True)
    result = bytearray(source_bytes)
    for start, end in ranges:
        result[start:end] = new_b
    return bytes(result)


def apply_patch(ast_tree, source_bytes: bytes, patch: dict) -> bytes:
    """
    Apply a patch to the AST/source. Mutates conceptually by building new bytes.
    patch: {symbol, action, target_node, code?, statement_index?, block_type?, block_index?,
            old_name?, new_name?}
    action: insert | replace | delete | rename
    target_node: function_body_start | function_body | class_body_start | class_body |
                 statement | statement_after | if_block | try_block | with_block | for_block
    Returns new source bytes.
    """
    symbol = patch.get("symbol", "")
    action = patch.get("action", "insert")
    target_node = patch.get("target_node", "function_body_start")
    code = patch.get("code", "")
    statement_index = patch.get("statement_index", 0)
    block_type = patch.get("block_type", "if_block")
    block_index = patch.get("block_index", 0)
    old_name = patch.get("old_name", "")
    new_name = patch.get("new_name", "")

    root = ast_tree.root_node if ast_tree else None
    if root is None:
        raise ValueError("Invalid AST tree")

    node, _ = _find_symbol_node(root, source_bytes, symbol)
    if node is None:
        raise ValueError(f"Symbol not found: {symbol}")

    body = node.child_by_field_name("body")
    if body is None:
        raise ValueError(f"No body for symbol: {symbol}")

    base_indent = _get_base_indent(source_bytes, body)
    indent = "    "

    # Variable renaming: scope is the symbol node
    if action == "rename":
        return _rename_identifiers_in_scope(source_bytes, node, old_name, new_name)

    # Block replacements: if_block, try_block, with_block, for_block
    if target_node in ("if_block", "try_block", "with_block", "for_block"):
        inner_block = _find_block_by_type(body, target_node, block_index, source_bytes)
        if inner_block is None:
            raise ValueError(f"Block not found: {target_node} index={block_index}")
        block_indent = _get_base_indent(source_bytes, inner_block)
        if action == "replace":
            replace_text = _indent_code(code, block_indent)
            return (
                source_bytes[: inner_block.start_byte]
                + replace_text.encode("utf-8")
                + source_bytes[inner_block.end_byte :]
            )
        elif action == "delete":
            pass_line = block_indent + "pass\n"
            return (
                source_bytes[: inner_block.start_byte]
                + pass_line.encode("utf-8")
                + source_bytes[inner_block.end_byte :]
            )
        raise ValueError(f"Block target supports replace/delete only, got action: {action}")

    # Statement-level patches
    statements = _get_body_statements(body)
    if target_node in ("statement", "statement_after"):
        if statement_index < 0 or statement_index >= len(statements):
            raise ValueError(f"statement_index {statement_index} out of range (0..{len(statements) - 1})")
        stmt_node = statements[statement_index]
        stmt_indent = _get_base_indent(source_bytes, stmt_node)

        if action == "insert" and target_node == "statement_after":
            lines = code.strip().split("\n")
            insert_lines = [stmt_indent + line if line.strip() else "" for line in lines]
            insert_text = "\n" + "\n".join(insert_lines) + "\n"
            insert_at = stmt_node.end_byte
            return (
                source_bytes[:insert_at]
                + insert_text.encode("utf-8")
                + source_bytes[insert_at:]
            )
        elif action == "replace" and target_node == "statement":
            # Replace from line start (include indent) to statement end
            line_start = source_bytes.rfind(b"\n", 0, stmt_node.start_byte) + 1
            replace_text = _indent_code(code, stmt_indent)
            return (
                source_bytes[:line_start]
                + replace_text.encode("utf-8")
                + source_bytes[stmt_node.end_byte :]
            )
        elif action == "delete" and target_node == "statement":
            # Remove full line (indent + statement + newline)
            line_start = source_bytes.rfind(b"\n", 0, stmt_node.start_byte) + 1
            end = stmt_node.end_byte
            if end < len(source_bytes) and source_bytes[end : end + 1] == b"\n":
                end += 1
            return source_bytes[:line_start] + source_bytes[end:]
        raise ValueError(f"Statement target: use statement_after+insert or statement+replace/delete")

    # Symbol-level patches (original behavior)
    if action == "insert":
        if target_node in ("function_body_start", "class_body_start"):
            lines = code.strip().split("\n")
            # Use base_indent so nested bodies (e.g. method inside class) get correct indent
            insert_indent = base_indent
            insert_lines = [insert_indent + line if line.strip() else "" for line in lines]
            line_start = source_bytes.rfind(b"\n", 0, body.start_byte) + 1
            insert_at = line_start
            insert_text = "\n".join(insert_lines) + "\n"
            new_bytes = (
                source_bytes[:insert_at]
                + insert_text.encode("utf-8")
                + source_bytes[insert_at:]
            )
        else:
            raise ValueError(f"Insert not supported for target_node: {target_node}")
    elif action == "replace":
        if target_node in ("function_body", "class_body"):
            replace_text = _indent_code(code, base_indent)
            # Replace from line start (include indent) so replacement aligns correctly
            line_start = source_bytes.rfind(b"\n", 0, body.start_byte) + 1
            new_bytes = (
                source_bytes[:line_start]
                + replace_text.encode("utf-8")
                + source_bytes[body.end_byte :]
            )
        else:
            raise ValueError(f"Replace requires target_node function_body or class_body, got: {target_node}")
    elif action == "delete":
        pass_line = base_indent + "pass\n"
        new_bytes = (
            source_bytes[: body.start_byte]
            + pass_line.encode("utf-8")
            + source_bytes[body.end_byte :]
        )
    else:
        raise ValueError(f"Unknown action: {action}")

    return new_bytes


def generate_code(ast_tree, source_bytes: bytes) -> str:
    """Return the source code as string from the given bytes."""
    return source_bytes.decode("utf-8", errors="replace")
