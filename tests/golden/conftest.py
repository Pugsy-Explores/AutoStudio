"""Golden test fixtures — indexed workspaces for retrieval/selector."""

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def indexed_autostudio(tmp_path_factory):
    """Index agent/ and editing/ once per session; shared by golden retrieval tests."""
    from repo_index.indexer import index_repo

    tmp_path = tmp_path_factory.mktemp("golden_retrieval_index")
    project_root = Path(__file__).resolve().parents[2]
    out_dir = tmp_path / ".symbol_graph"
    out_dir.mkdir(parents=True, exist_ok=True)
    index_repo(
        str(project_root),
        output_dir=str(out_dir),
        include_dirs=("agent", "editing"),
        build_embeddings=False,
    )
    return str(tmp_path), str(project_root)
