"""Phase 14 token budgeting and context control tests."""

from unittest.mock import patch

from config.agent_config import MAX_PROMPT_TOKENS, MAX_REPO_CONTEXT_TOKENS

from agent.prompt_system.context.token_counter import count_prompt_tokens, count_tokens
from agent.prompt_system.context.context_compressor import compress
from agent.prompt_system.context.context_pruner import prune_sections
from agent.prompt_system.context.context_ranker import rank_and_limit
from agent.prompt_system.context.prompt_budget_manager import PromptBudgetManager


def test_count_tokens_returns_tuple():
    cnt, approx = count_tokens("hello world", "default")
    assert isinstance(cnt, int)
    assert cnt >= 2
    assert isinstance(approx, bool)


def test_count_tokens_empty():
    cnt, _ = count_tokens("", "default")
    assert cnt == 0


def test_count_prompt_tokens():
    parts = {
        "system": "You are a helpful assistant.",
        "skills": "",
        "repo_context": "def foo(): pass",
        "history": "",
        "user_input": "Hello",
    }
    result = count_prompt_tokens(parts, "default")
    assert "total" in result
    assert result["total"] >= 5
    assert result["system"] >= 5
    assert result["user_input"] >= 1


def test_compress_skips_when_under_threshold():
    ranked = [{"file": "a.py", "symbol": "", "snippet": "x" * 100}]
    out, ratio = compress(ranked, repo_context_tokens=100, model_name="default")
    assert ratio == 1.0
    assert len(out) == 1
    assert out[0]["snippet"] == ranked[0]["snippet"]


def test_compress_runs_when_over_threshold():
    ranked = [{"file": "a.py", "symbol": "", "snippet": "x" * 10000}]
    with patch("agent.repo_intelligence.context_compressor.call_small_model", return_value="summary"):
        out, ratio = compress(ranked, repo_context_tokens=MAX_REPO_CONTEXT_TOKENS + 1000, model_name="default")
    assert ratio >= 1.0 or len(out) <= len(ranked)


def test_prune_sections_preserves_system():
    sections = {
        "system": "System instructions",
        "repo_context": "x" * 50000,
        "history": "",
        "skills": "",
        "user_input": "hi",
    }
    token_counts = count_prompt_tokens(sections, "default")
    pruned = prune_sections(sections, 1000, token_counts, "default")
    assert "system" in pruned
    assert pruned["system"] == "System instructions"
    assert pruned["user_input"] == "hi"


def test_allocate_budget():
    mgr = PromptBudgetManager()
    alloc = mgr.allocate_budget("gpt-4")
    assert alloc.system > 0
    assert alloc.repo_context > 0
    assert alloc.history >= 0
    assert alloc.skills >= 0
    assert alloc.user_input >= 0
    total = alloc.system + alloc.repo_context + alloc.history + alloc.skills + alloc.user_input
    assert total <= 128000


def test_enforce_budget_within_limit():
    mgr = PromptBudgetManager()
    parts = {"system": "sys", "skills": "", "repo_context": "x" * 100, "history": "", "user_input": "q"}
    counts = count_prompt_tokens(parts, "default")
    safe, report = mgr.enforce_budget(parts, counts, "default")
    assert safe["system"] == "sys"
    assert not report.pruning_triggered
    assert not report.emergency_truncation_triggered


def test_enforce_budget_over_limit_triggers_pruning():
    mgr = PromptBudgetManager()
    parts = {
        "system": "sys",
        "skills": "s" * 5000,
        "repo_context": "r" * 50000,
        "history": "h" * 5000,
        "user_input": "q",
    }
    counts = count_prompt_tokens(parts, "default")
    safe, report = mgr.enforce_budget(parts, counts, "default", prompt_name="planner")
    assert report.pruning_triggered
    total_after = count_prompt_tokens(safe, "default")["total"]
    assert total_after <= MAX_PROMPT_TOKENS or report.emergency_truncation_triggered


def test_enforce_budget_fallback_key():
    mgr = PromptBudgetManager()
    parts = {
        "system": "sys",
        "skills": "s" * 20000,
        "repo_context": "r" * 50000,
        "history": "h" * 20000,
        "user_input": "q",
    }
    counts = count_prompt_tokens(parts, "default")
    safe, report = mgr.enforce_budget(parts, counts, "default", prompt_name="planner")
    assert report.fallback_key == "planner_compact"


def test_rank_and_limit_enforces_max_snippets():
    candidates = [{"file": f"f{i}.py", "symbol": "", "snippet": "code"} for i in range(20)]
    with patch("agent.retrieval.context_ranker._get_llm_relevance_batch", return_value=[0.5] * 20):
        ranked = rank_and_limit("query", candidates, max_snippets=5)
    assert len(ranked) <= 5


def test_rank_and_limit_truncates_long_snippets():
    candidates = [{"file": "a.py", "symbol": "", "snippet": "line\n" * 500}]
    with patch("agent.retrieval.context_ranker._get_llm_relevance_batch", return_value=[0.5]):
        ranked = rank_and_limit("query", candidates, max_code_lines=50)
    assert len(ranked) == 1
    lines = ranked[0]["snippet"].splitlines()
    assert len(lines) <= 51
