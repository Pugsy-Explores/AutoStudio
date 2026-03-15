"""Validate configuration values at agent startup."""


def validate_config() -> None:
    """Assert critical config values are within expected ranges."""
    from config import agent_config, editing_config, retrieval_config

    assert retrieval_config.MAX_CONTEXT_SNIPPETS > 0, "MAX_CONTEXT_SNIPPETS must be > 0"
    assert retrieval_config.MAX_SEARCH_RESULTS > 0, "MAX_SEARCH_RESULTS must be > 0"
    assert retrieval_config.MAX_SYMBOL_EXPANSION >= 1, "MAX_SYMBOL_EXPANSION must be >= 1"
    assert retrieval_config.GRAPH_EXPANSION_DEPTH >= 1, "GRAPH_EXPANSION_DEPTH must be >= 1"
    assert editing_config.MAX_FILES_EDITED > 0, "MAX_FILES_EDITED must be > 0"
    assert editing_config.MAX_PATCH_SIZE > 0, "MAX_PATCH_SIZE must be > 0"
    assert agent_config.MAX_REPLAN_ATTEMPTS >= 1, "MAX_REPLAN_ATTEMPTS must be >= 1"
    assert agent_config.MAX_TASK_RUNTIME_SECONDS > 0, "MAX_TASK_RUNTIME_SECONDS must be > 0"
