"""Repository indexer: scan repo, parse files, extract symbols."""

import fnmatch
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from repo_index.dependency_extractor import extract_edges
from repo_index.parser import parse_file
from repo_index.symbol_extractor import extract_symbols

logger = logging.getLogger(__name__)

SYMBOL_GRAPH_DIR = ".symbol_graph"
SYMBOLS_JSON = "symbols.json"
INDEX_SQLITE = "index.sqlite"
INDEX_PARALLEL_WORKERS = int(os.environ.get("INDEX_PARALLEL_WORKERS", "8"))


def _load_gitignore_patterns(root: Path) -> list[tuple[str, bool]]:
    """Load .gitignore from repo root. Returns list of (pattern, is_dir) tuples."""
    gitignore = root / ".gitignore"
    if not gitignore.exists():
        return []
    patterns: list[tuple[str, bool]] = []
    for line in gitignore.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("!"):
            continue
        if line.startswith("/"):
            line = line[1:]
        is_dir = line.endswith("/")
        if is_dir:
            line = line[:-1]
        patterns.append((line, is_dir))
    return patterns


def _is_ignored(path: Path, root: Path, patterns: list[tuple[str, bool]]) -> bool:
    """True if path (relative to root) matches any .gitignore pattern."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    parts = rel.parts
    rel_str = str(rel).replace("\\", "/")

    for pat, is_dir in patterns:
        # Directory pattern (e.g. venv/): match if any path segment equals or matches
        if is_dir:
            for part in parts:
                if fnmatch.fnmatch(part, pat) or part == pat:
                    return True
            continue
        # File pattern: match full path
        if fnmatch.fnmatch(rel_str, pat) or fnmatch.fnmatch(rel_str, "**/" + pat):
            return True
        if "/" not in pat and any(fnmatch.fnmatch(p, pat) for p in parts):
            return True
    return False


def _parse_single_file(path: Path, root: Path) -> tuple[list[dict], dict]:
    """Parse one file; return (symbols, {path: tree})."""
    rel = path.relative_to(root)
    tree = parse_file(str(path))
    if tree is None:
        return [], {}
    try:
        source_bytes = path.read_bytes()
    except Exception:
        source_bytes = b""
    symbols = extract_symbols(tree, str(path), source_bytes)
    return symbols, {str(path): tree}


def scan_repo(root_dir: str) -> list[dict]:
    """
    Scan repository for Python files, parse AST, extract symbols.
    Returns list of symbol records: {symbol_name, symbol_type, file, start_line, end_line, docstring}.
    """
    symbols, _ = _scan_repo_with_trees(root_dir)
    return symbols


def _scan_repo_with_trees(
    root_dir: str,
    include_dirs: tuple[str, ...] | None = None,
    ignore_gitignore: bool = True,
    verbose: bool = False,
) -> tuple[list[dict], dict]:
    """Scan repo and return (symbols, ast_trees). Uses parallel workers when INDEX_PARALLEL_WORKERS > 1.
    include_dirs: if set, only scan these subdirs of root_dir (e.g. ("agent", "editing")).
    ignore_gitignore: if True, exclude paths matching .gitignore.
    verbose: if True, log each file indexed or skipped."""
    root = Path(root_dir).resolve()
    if not root.is_dir():
        logger.warning("[indexer] not a directory: %s", root_dir)
        return [], {}

    if include_dirs:
        py_files: list[Path] = []
        for sub in include_dirs:
            d = root / sub
            if d.is_dir():
                py_files.extend(d.rglob("*.py"))
    else:
        py_files = list(root.rglob("*.py"))

    if ignore_gitignore:
        gitignore_patterns = _load_gitignore_patterns(root)
        before = len(py_files)
        py_files = [p for p in py_files if not _is_ignored(p, root, gitignore_patterns)]
        if verbose and before != len(py_files):
            logger.info("[indexer] .gitignore excluded %d files", before - len(py_files))

    logger.debug("[indexer] scan_repo found %d Python files in %s", len(py_files), root)
    if not py_files:
        logger.warning("[indexer] no Python files found in %s", root)
    all_symbols: list[dict] = []
    ast_trees: dict[str, "Tree"] = {}
    workers = max(1, min(INDEX_PARALLEL_WORKERS, 16))

    def _log_file(path: Path, status: str = "indexed") -> None:
        if verbose:
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            logger.info("[indexer] %s %s", status, rel)

    if workers <= 1:
        for path in py_files:
            symbols, trees = _parse_single_file(path, root)
            _log_file(path)
            all_symbols.extend(symbols)
            ast_trees.update(trees)
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_parse_single_file, p, root): p for p in py_files}
            for fut in as_completed(futures):
                path = futures.get(fut)
                try:
                    symbols, trees = fut.result()
                    if path:
                        _log_file(path)
                    all_symbols.extend(symbols)
                    ast_trees.update(trees)
                except Exception as e:
                    logger.warning("[indexer] parse failed for %s: %s", path, e)

    return all_symbols, ast_trees


EMBEDDINGS_SUBDIR = "embeddings"
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
INDEX_EMBEDDINGS = os.environ.get("INDEX_EMBEDDINGS", "1").lower() in ("1", "true", "yes")


def _build_embedding_index(root_dir: str, symbols: list[dict], out_path: Path) -> None:
    """Optionally build ChromaDB embedding index for vector search."""
    if not INDEX_EMBEDDINGS:
        return
    try:
        import chromadb
        from sentence_transformers import SentenceTransformer
    except ImportError:
        logger.debug("[indexer] chromadb/sentence-transformers not available, skipping embeddings")
        return

    root = Path(root_dir).resolve()
    emb_path = out_path / EMBEDDINGS_SUBDIR
    emb_path.mkdir(parents=True, exist_ok=True)

    try:
        client = chromadb.PersistentClient(path=str(emb_path))
        model = SentenceTransformer("all-MiniLM-L6-v2")

        docs = []
        metas = []
        ids_list = []

        for s in symbols:
            path_str = s.get("file", "")
            name = s.get("symbol_name", "")
            if not path_str:
                continue
            try:
                p = Path(path_str)
                if not p.exists():
                    continue
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            start = s.get("start_line", 1) - 1
            end = s.get("end_line", start + 1)
            lines = text.splitlines()
            if start < len(lines):
                chunk = "\n".join(lines[start:min(end, len(lines))])
            else:
                chunk = text[:CHUNK_SIZE]
            if not chunk.strip():
                continue
            rel = str(p.relative_to(root)) if p.is_relative_to(root) else path_str
            docs.append(chunk[:CHUNK_SIZE])
            metas.append({"path": rel, "symbol": name, "line": s.get("start_line", 0)})
            ids_list.append(f"{rel}:{name}:{s.get('start_line', 0)}")

        if not docs:
            return

        embs = model.encode(docs).tolist()
        try:
            client.delete_collection("codebase")
        except Exception:
            pass
        coll = client.create_collection("codebase")
        coll.add(documents=docs, embeddings=embs, metadatas=metas, ids=ids_list)
        logger.info("[indexer] embedding index: %d chunks", len(docs))
    except Exception as e:
        logger.warning("[indexer] embedding index failed: %s", e)


def index_repo(
    root_dir: str,
    output_dir: str | None = None,
    include_dirs: tuple[str, ...] | None = None,
    ignore_gitignore: bool = True,
    verbose: bool = False,
) -> tuple[list[dict], str]:
    """
    Scan repo, extract symbols and dependencies, build graph, write to output.
    Returns (symbols, output_path).
    include_dirs: if set, only index these subdirs (e.g. ("agent", "editing")).
    ignore_gitignore: if True, exclude paths matching .gitignore (default True).
    verbose: if True, log each file indexed.
    """
    root = Path(root_dir).resolve()
    out = output_dir or str(root / SYMBOL_GRAPH_DIR)
    out_path = Path(out)
    out_path.mkdir(parents=True, exist_ok=True)

    symbols, ast_trees = _scan_repo_with_trees(
        root_dir,
        include_dirs=include_dirs,
        ignore_gitignore=ignore_gitignore,
        verbose=verbose,
    )
    edges = extract_edges(symbols, ast_trees, str(root))

    # Write symbols JSON
    json_path = out_path / SYMBOLS_JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(symbols, f, indent=2)

    # Build graph SQLite
    db_path = out_path / INDEX_SQLITE
    if db_path.exists():
        db_path.unlink()
    from repo_graph.graph_builder import build_graph

    build_graph(symbols, edges, str(db_path))

    _build_embedding_index(root_dir, symbols, out_path)

    logger.info(
        "[indexer] wrote %d symbols, %d edges to %s (db=%s)",
        len(symbols),
        len(edges),
        json_path,
        db_path,
    )
    return symbols, str(db_path)


def update_index_for_file(file_path: str, root_dir: str | None = None) -> int:
    """
    Incrementally update the repo index for a single modified file.
    Re-parses the file, updates symbol records and dependency edges.
    Returns number of symbols updated.
    """
    path = Path(file_path).resolve()
    if not path.exists() or not path.is_file():
        logger.warning("[index_update] file not found: %s", file_path)
        return 0

    root = Path(root_dir).resolve() if root_dir else path.parent
    while root != root.parent and not (root / SYMBOL_GRAPH_DIR).is_dir():
        root = root.parent
    if not (root / SYMBOL_GRAPH_DIR).is_dir():
        root = Path(os.environ.get("SERENA_PROJECT_DIR", os.getcwd())).resolve()
    out_path = root / SYMBOL_GRAPH_DIR
    if not out_path.is_dir():
        logger.warning("[index_update] no .symbol_graph at %s", root)
        return 0

    json_path = out_path / SYMBOLS_JSON
    db_path = out_path / INDEX_SQLITE
    path_str = str(path)

    tree = parse_file(str(path))
    if tree is None:
        logger.warning("[index_update] failed to parse %s", path)
        return 0

    try:
        source_bytes = path.read_bytes()
    except Exception:
        source_bytes = b""
    new_symbols = extract_symbols(tree, path_str, source_bytes)

    # Update symbols.json
    if json_path.exists():
        with open(json_path, encoding="utf-8") as f:
            all_symbols = json.load(f)
        all_symbols = [s for s in all_symbols if s.get("file") != path_str]
    else:
        all_symbols = []
    all_symbols.extend(new_symbols)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_symbols, f, indent=2)

    # Update graph
    if not db_path.exists():
        logger.info("[index_update] no index.sqlite, skipping graph update")
        return len(new_symbols)

    from repo_graph.graph_storage import GraphStorage

    from repo_index.dependency_extractor import extract_edges_for_file

    edges_for_file = extract_edges_for_file(new_symbols, tree, path_str)

    storage = GraphStorage(str(db_path))
    try:
        storage.remove_nodes_for_file(path_str)
        name_to_id: dict[str, int] = {}
        for s in new_symbols:
            name = s.get("symbol_name", "")
            if not name:
                continue
            nid = storage.add_node(s)
            name_to_id[name] = nid
            short = name.split(".")[-1] if "." in name else name
            if short not in name_to_id:
                name_to_id[short] = nid

        for e in edges_for_file:
            src_name = e.get("source_symbol", "")
            tgt_name = e.get("target_symbol", "")
            rel = e.get("relation_type", "references")
            src_id = name_to_id.get(src_name) or name_to_id.get(
                src_name.split(".")[-1] if "." in src_name else src_name
            )
            tgt_id = name_to_id.get(tgt_name) or name_to_id.get(
                tgt_name.split(".")[-1] if "." in tgt_name else tgt_name
            )
            if src_id is None:
                node = storage.get_symbol_by_name(src_name)
                if node is None and "." in src_name:
                    node = storage.get_symbol_by_name(src_name.split(".")[-1])
                src_id = node.get("id") if node else None
            if tgt_id is None:
                node = storage.get_symbol_by_name(tgt_name)
                if node is None and "." in tgt_name:
                    node = storage.get_symbol_by_name(tgt_name.split(".")[-1])
                tgt_id = node.get("id") if node else None
            if src_id and tgt_id and src_id != tgt_id:
                storage.add_edge(src_id, tgt_id, rel)
    finally:
        storage.close()

    logger.info("[index_update] file updated: %s symbols_updated=%d", path_str, len(new_symbols))
    return len(new_symbols)


def main():
    """CLI entry: python -m repo_index.index_repo <repo_path>"""
    import sys

    logging.basicConfig(level=logging.INFO)
    repo_path = sys.argv[1] if len(sys.argv) > 1 else "."
    symbols, path = index_repo(repo_path)
    print(f"Indexed {len(symbols)} symbols -> {path}")


if __name__ == "__main__":
    main()
