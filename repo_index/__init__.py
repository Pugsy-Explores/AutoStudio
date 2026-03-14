"""Repository indexing: scan repo, parse files, extract symbols and dependencies."""

from repo_index.dependency_extractor import extract_edges
from repo_index.indexer import index_repo, scan_repo
from repo_index.parser import parse_file
from repo_index.symbol_extractor import extract_symbols

__all__ = ["scan_repo", "index_repo", "parse_file", "extract_symbols", "extract_edges"]
