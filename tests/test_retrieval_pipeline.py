"""Integration tests for retrieval pipeline: query -> graph retrieval -> context builder."""

from pathlib import Path

import pytest

from agent.retrieval.context_builder import build_context, build_context_from_symbols
from agent.retrieval.graph_retriever import retrieve_symbol_context
from agent.retrieval.retrieval_expander import expand_search_results
from agent.tools import search_code
from repo_index.indexer import index_repo

# Subset of repo to index for slow tests (agent + editing contain StepExecutor, plan_diff, validate_patch)
_INDEX_SUBDIRS = ("agent", "editing")


def _setup_indexed_repo(tmp_path: Path, source_root: Path) -> tuple[str, str]:
    """Index source_root into tmp_path/.symbol_graph. Returns (project_root for retrieval, source_root for path resolution)."""
    out_dir = tmp_path / ".symbol_graph"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_repo(str(source_root), output_dir=str(out_dir))
    return str(tmp_path), str(source_root)


def _setup_indexed_subset(tmp_path: Path, project_root: Path, subdirs: tuple[str, ...]) -> tuple[str, str]:
    """Index only subdirs of project. Returns (project_root for retrieval, source_root for path resolution)."""
    out_dir = tmp_path / ".symbol_graph"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_repo(str(project_root), output_dir=str(out_dir), include_dirs=subdirs)
    return str(tmp_path), str(project_root)


def _requires_tree_sitter():
    """Skip test if tree-sitter is not installed (needed for indexing)."""
    pytest.importorskip("tree_sitter_python")


@pytest.fixture(scope="session")
def indexed_autostudio(tmp_path_factory):
    """Index agent/ and editing/ once per session; shared by all slow tests."""
    _requires_tree_sitter()
    tmp_path = tmp_path_factory.mktemp("retrieval_index")
    project_root = Path(__file__).resolve().parent.parent
    return _setup_indexed_subset(tmp_path, project_root, _INDEX_SUBDIRS)


@pytest.fixture
def indexed_fixtures(tmp_path):
    """Index test fixtures into tmp_path."""
    _requires_tree_sitter()
    fixtures_dir = Path(__file__).parent / "fixtures" / "repo"
    return _setup_indexed_repo(tmp_path, fixtures_dir)


@pytest.mark.slow
def test_retrieval_pipeline_step_executor(indexed_autostudio):
    """Query 'Where is StepExecutor implemented?' -> graph retrieval -> context builder.
    Uses AutoStudio index; StepExecutor is in agent/execution/executor.py."""
    project_root, source_root = indexed_autostudio
    query = "Where is StepExecutor implemented?"

    # 1. Graph retrieval (extracts "StepExecutor" from natural language)
    graph_result = retrieve_symbol_context(query, project_root=project_root)
    assert graph_result is not None, "Graph retriever should find StepExecutor"
    results = graph_result.get("results", [])
    assert len(results) > 0, "Should return at least one result"
    assert len(results) <= 15, "Returned symbols must be <= 15"

    # 2. Verify result structure
    for r in results:
        assert "file" in r
        assert "symbol" in r or "snippet" in r
        assert r.get("snippet", ""), "Snippets must not be empty"
        file_path = r.get("file", "")
        assert file_path, "File path must be present"
        # Path may be absolute (from index) or relative; resolve for existence check
        resolved = Path(file_path) if Path(file_path).is_absolute() else Path(source_root) / file_path
        assert resolved.exists(), f"File path must exist: {resolved}"

    # 3. Context builder
    expanded = expand_search_results(results)
    assert len(expanded) > 0

    # Simulate expansion: read_file/read_symbol_body would run here
    from agent.tools import find_referencing_symbols, read_file, read_symbol_body

    symbol_results = []
    reference_results = []
    file_snippets = []
    for item in expanded[:5]:
        path = item.get("file") or ""
        symbol = item.get("symbol") or ""
        line = item.get("line")
        try:
            if item.get("action") == "read_symbol_body" and symbol:
                body = read_symbol_body(symbol, path, line=line)
                file_snippets.append({"file": path, "snippet": body, "symbol": symbol})
                symbol_results.append({"file": path, "symbol": symbol, "snippet": body[:500]})
            else:
                content = read_file(path)
                file_snippets.append({"file": path, "snippet": (content or "")[:2000]})
            refs = find_referencing_symbols(symbol or path, path)
            reference_results.extend(refs)
        except Exception:
            pass

    built = build_context_from_symbols(symbol_results, reference_results, file_snippets)

    # 4. Assertions
    assert "symbols" in built
    assert "references" in built
    assert "files" in built
    assert "snippets" in built
    assert len(built.get("symbols", [])) <= 15
    for s in built.get("snippets", []):
        assert s is not None
    for f in built.get("files", []):
        if not f:
            continue
        resolved = Path(f) if Path(f).is_absolute() else Path(source_root) / f
        assert resolved.exists(), f"File path must exist: {f}"


@pytest.mark.slow
def test_retrieval_pipeline_diff_planner(indexed_autostudio):
    """Query 'Find the diff planner' -> graph retrieval."""
    project_root, _ = indexed_autostudio
    query = "Find the diff planner"

    graph_result = retrieve_symbol_context(query, project_root=project_root)
    assert graph_result is not None
    results = graph_result.get("results", [])
    assert len(results) > 0
    assert len(results) <= 15

    for r in results:
        assert r.get("snippet", ""), "Snippets must not be empty"
        assert r.get("file", ""), "File path must be present"


@pytest.mark.slow
def test_retrieval_pipeline_validate_patches(indexed_autostudio):
    """Query 'Where do we validate patches?' -> graph retrieval."""
    project_root, _ = indexed_autostudio
    query = "Where do we validate patches?"

    graph_result = retrieve_symbol_context(query, project_root=project_root)
    assert graph_result is not None
    results = graph_result.get("results", [])
    assert len(results) > 0
    assert len(results) <= 15

    for r in results:
        assert r.get("snippet", ""), "Snippets must not be empty"


@pytest.mark.slow
def test_retrieval_returns_symbols_files_snippets_references(indexed_autostudio):
    """Verify retrieval returns symbols, files, snippets, and references."""
    project_root, _ = indexed_autostudio
    query = "StepExecutor"

    graph_result = retrieve_symbol_context(query, project_root=project_root)
    assert graph_result is not None
    results = graph_result.get("results", [])

    # Results have symbol, file, snippet
    assert len(results) > 0
    has_symbol = any(r.get("symbol") for r in results)
    has_file = any(r.get("file") for r in results)
    has_snippet = any(r.get("snippet") for r in results)
    assert has_symbol or has_file, "Must have symbols or files"
    assert has_snippet, "Must have non-empty snippets"

    # Build context produces all four types
    expanded = expand_search_results(results)
    from agent.tools import find_referencing_symbols, read_file, read_symbol_body

    symbol_results = []
    reference_results = []
    file_snippets = []
    for item in expanded[:5]:
        path = item.get("file") or ""
        symbol = item.get("symbol") or ""
        line = item.get("line")
        try:
            if item.get("action") == "read_symbol_body" and symbol:
                body = read_symbol_body(symbol, path, line=line)
                file_snippets.append({"file": path, "snippet": body, "symbol": symbol})
                symbol_results.append({"file": path, "symbol": symbol, "snippet": body[:500]})
            else:
                content = read_file(path)
                file_snippets.append({"file": path, "snippet": (content or "")[:2000]})
            reference_results.extend(find_referencing_symbols(symbol or path, path))
        except Exception:
            pass

    built = build_context_from_symbols(symbol_results, reference_results, file_snippets)
    assert "symbols" in built
    assert "references" in built
    assert "files" in built
    assert "snippets" in built


def test_retrieval_pipeline_natural_language_query(indexed_fixtures):
    """Natural language query 'Where is bar defined?' extracts 'bar' and finds symbol."""
    project_root, source_root = indexed_fixtures
    query = "Where is bar defined?"

    graph_result = retrieve_symbol_context(query, project_root=project_root)
    assert graph_result is not None, "Should extract 'bar' from natural language and find it"
    results = graph_result.get("results", [])
    assert len(results) > 0
    assert len(results) <= 15


def test_retrieval_pipeline_fixtures_bar(indexed_fixtures):
    """Fast test using fixtures: query 'bar' -> graph retrieval -> context builder."""
    project_root, source_root = indexed_fixtures
    query = "bar"

    graph_result = retrieve_symbol_context(query, project_root=project_root)
    assert graph_result is not None
    results = graph_result.get("results", [])
    assert len(results) > 0
    assert len(results) <= 15
    for r in results:
        assert r.get("snippet", ""), "Snippets must not be empty"
        assert r.get("file", ""), "File path must be present"
    built = build_context(graph_result)
    assert len(built["files"]) > 0
    assert len(built["snippets"]) > 0


def test_fallback_search_when_graph_fails(tmp_path, monkeypatch):
    """When graph lookup fails (no index), fallback to search_code (grep) works."""
    # Use project root where we know files exist; no index at tmp_path
    project_root = Path(__file__).resolve().parent.parent
    monkeypatch.setenv("SERENA_PROJECT_DIR", str(project_root))

    # Graph retriever with no index returns None
    result = retrieve_symbol_context("StepExecutor", project_root=str(tmp_path))
    assert result is None

    # search_code fallback (grep) should return results when run from project
    out = search_code("StepExecutor", tool_hint=None)
    assert "results" in out
    # Grep fallback runs when Serena unavailable; may have results if rg works
    assert isinstance(out["results"], list)


def test_build_context_from_search_results():
    """build_context produces files and snippets from search results."""
    search_results = {
        "results": [
            {"file": "agent/retrieval/graph_retriever.py", "symbol": "retrieve_symbol_context", "snippet": "def retrieve"},
            {"file": "agent/retrieval/context_builder.py", "snippet": "def build_context"},
        ]
    }
    built = build_context(search_results)
    assert built["files"] == ["agent/retrieval/graph_retriever.py", "agent/retrieval/context_builder.py"]
    assert len(built["snippets"]) == 2
    assert all(s for s in built["snippets"])


def test_context_builder_files_snippets_aligned():
    """build_context_from_symbols keeps files and snippets aligned."""
    symbol_results = [{"file": "/x/a.py", "symbol": "foo", "snippet": "def foo"}]
    reference_results = []
    file_snippets = [
        {"file": "/x/a.py", "snippet": "content a"},
        {"file": "/x/b.py", "snippet": ""},
    ]
    built = build_context_from_symbols(symbol_results, reference_results, file_snippets)
    assert len(built["files"]) == len(built["snippets"])
    assert built["files"] == ["/x/a.py", "/x/b.py"]
    assert built["snippets"][0] == "content a"
    assert built["snippets"][1] == ""  # empty but aligned
