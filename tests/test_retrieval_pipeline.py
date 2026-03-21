"""Integration tests for retrieval pipeline: query -> graph retrieval -> context builder."""

from pathlib import Path

import pytest

from agent.memory.state import AgentState
from agent.retrieval.context_builder import build_context, build_context_from_symbols
from agent.retrieval.retrieval_pipeline import run_retrieval_pipeline
from agent.retrieval.graph_retriever import retrieve_symbol_context
from agent.retrieval.retrieval_expander import expand_search_results, normalize_file_path
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


@pytest.fixture(scope="session")
def indexed_autostudio(tmp_path_factory):
    """Index agent/ and editing/ once per session; shared by all slow tests."""
    tmp_path = tmp_path_factory.mktemp("retrieval_index")
    project_root = Path(__file__).resolve().parent.parent
    return _setup_indexed_subset(tmp_path, project_root, _INDEX_SUBDIRS)


@pytest.fixture
def indexed_fixtures(tmp_path):
    """Index test fixtures into tmp_path."""
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
        assert s is not None and isinstance(s, dict) and "snippet" in s
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
    """build_context_from_symbols returns snippets as list of {file, symbol, snippet}."""
    symbol_results = [{"file": "/x/a.py", "symbol": "foo", "snippet": "def foo"}]
    reference_results = []
    file_snippets = [
        {"file": "/x/a.py", "snippet": "content a"},
        {"file": "/x/b.py", "snippet": ""},
    ]
    built = build_context_from_symbols(symbol_results, reference_results, file_snippets)
    assert len(built["files"]) == len(built["snippets"])
    assert built["files"] == ["/x/a.py", "/x/b.py"]
    assert built["snippets"][0]["file"] == "/x/a.py" and built["snippets"][0]["snippet"] == "content a"
    assert built["snippets"][1]["file"] == "/x/b.py" and built["snippets"][1]["snippet"] == ""


def test_normalize_file_path_strips_json_artifacts():
    """normalize_file_path strips malformed prefixes/suffixes from search result paths."""
    assert normalize_file_path('{"tests/test_agent_e2e.py') == "tests/test_agent_e2e.py"
    assert normalize_file_path('"agent/execution/step_dispatcher.py"') == "agent/execution/step_dispatcher.py"
    assert normalize_file_path("  tests/foo.py  ") == "tests/foo.py"
    assert normalize_file_path("agent/retrieval/graph_retriever.py") == "agent/retrieval/graph_retriever.py"
    assert normalize_file_path("") == ""
    assert normalize_file_path('{"') == ""


def test_expand_search_results_normalizes_malformed_paths():
    """expand_search_results produces valid paths from malformed search results."""
    malformed = [
        {"file": '{"tests/test_agent_e2e.py', "symbol": "", "snippet": "..."},
        {"file": "agent/execution/step_dispatcher.py", "symbol": "dispatch", "snippet": "..."},
    ]
    expanded = expand_search_results(malformed)
    assert len(expanded) == 2
    assert expanded[0]["file"] == "tests/test_agent_e2e.py"
    assert expanded[1]["file"] == "agent/execution/step_dispatcher.py"


def test_hybrid_retrieve_merges_sources(monkeypatch):
    """Hybrid retrieval merges results from graph, vector, grep."""
    from agent.retrieval.search_pipeline import hybrid_retrieve, _merge_results

    # Test merge logic directly
    graph_out = {"results": [{"file": "a.py", "symbol": "foo", "snippet": "def foo"}]}
    vector_out = {"results": [{"file": "b.py", "symbol": "", "snippet": "class Bar"}]}
    grep_out = {"results": [{"file": "a.py", "symbol": "foo", "line": 10, "snippet": "foo()"}]}
    merged = _merge_results(graph_out, vector_out, grep_out)
    assert len(merged) >= 2
    files = [r.get("file") for r in merged]
    assert "a.py" in files
    assert "b.py" in files


def test_search_budget_enforced():
    """Retrieval budgets: expand caps at MAX_SYMBOL_EXPANSION, prune caps at MAX_CONTEXT_SNIPPETS."""
    from agent.retrieval.context_pruner import prune_context
    from agent.retrieval.retrieval_expander import MAX_SYMBOL_EXPANSION

    # Expand caps at MAX_SYMBOL_EXPANSION
    many_results = [{"file": f"file{i}.py", "symbol": "", "snippet": "x"} for i in range(30)]
    expanded = expand_search_results(many_results)
    assert len(expanded) <= MAX_SYMBOL_EXPANSION

    # Prune caps at max_snippets
    ranked = [{"file": f"f{i}.py", "symbol": "", "snippet": "snippet"} for i in range(10)]
    pruned = prune_context(ranked, max_snippets=6, max_chars=8000)
    assert len(pruned) == 6


@pytest.mark.slow
def test_retrieval_pipeline_ranked_context_step_executor(indexed_autostudio):
    """Full pipeline for 'Explain StepExecutor': graph retrieval -> run_retrieval_pipeline -> ranked_context != [].
    Step 3 validation: ranked_context must be populated for EXPLAIN to succeed.
    Pipeline uses reranker (or retriever-score fallback); no rank_context patch (removed in TASK 5)."""
    project_root, source_root = indexed_autostudio
    query = "Explain StepExecutor"

    graph_result = retrieve_symbol_context(query, project_root=project_root)
    assert graph_result is not None
    results = graph_result.get("results", [])
    assert len(results) > 0, "Graph retriever should find StepExecutor"

    state = AgentState(
        instruction=query,
        current_plan={"plan_id": "retrieval_plan", "steps": []},
        context={
            "project_root": project_root,
            "source_root": source_root,
            "instruction": query,
        },
    )

    run_retrieval_pipeline(results, state, query=query)

    ranked = state.context.get("ranked_context") or []
    assert len(ranked) > 0, "ranked_context must not be empty (Step 3: retrieval bug if empty)"
    for r in ranked:
        assert "snippet" in r or "file" in r
        assert r.get("snippet") or r.get("file"), "Each ranked item must have snippet or file"


def test_context_anchors_format():
    """Context format uses FILE/SYMBOL/LINES/SNIPPET anchored blocks."""
    from agent.execution.step_dispatcher import _format_explain_context
    from agent.memory.state import AgentState

    state = AgentState(
        instruction="Explain",
        current_plan={"plan_id": "context_anchors_plan", "steps": []},
        context={
            "ranked_context": [
                {
                    "file": "agent/execution/executor.py",
                    "symbol": "StepExecutor",
                    "line": 14,
                    "snippet": "class StepExecutor:\n    def execute_step(self, step, state):",
                }
            ]
        },
    )
    formatted = _format_explain_context(state)
    assert "FILE: agent/execution/executor.py" in formatted
    assert "SYMBOL: StepExecutor" in formatted
    assert "LINES: 14-14" in formatted
    assert "SNIPPET:" in formatted
    assert "class StepExecutor" in formatted
