"""Tests for Phase 5.3 semantic memory (keyword overlap, recency order)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from agent_v2.config import get_semantic_memory_dir
from agent_v2.memory import semantic_memory as semantic_memory_mod
from agent_v2.memory.semantic_memory import SemanticMemory


def test_add_and_query(tmp_path: Path) -> None:
    mem = SemanticMemory(tmp_path / "semantic")
    mem.add_fact("file:main.py", "main.py defines a FastAPI application")
    out = mem.query("fastapi")
    assert len(out) == 1
    assert out[0]["key"] == "file:main.py"
    assert "FastAPI" in out[0]["text"] or "fastapi" in out[0]["text"].lower()
    assert out[0].get("text_lower") == "main.py defines a fastapi application"
    assert "timestamp" in out[0]
    assert out[0].get("tags") == []


def test_word_match_not_substring_api_in_rapid(tmp_path: Path) -> None:
    mem = SemanticMemory(tmp_path / "semantic")
    mem.add_fact("k", "this is rapid delivery")
    assert mem.query("api") == []


def test_word_match_not_substring_app_in_happens(tmp_path: Path) -> None:
    mem = SemanticMemory(tmp_path / "semantic")
    mem.add_fact("k", "it happens often")
    assert mem.query("app") == []


def test_multiple_facts_respects_limit(tmp_path: Path) -> None:
    mem = SemanticMemory(tmp_path / "semantic")
    mem.add_fact("k1", "alpha widget one")
    mem.add_fact("k2", "beta widget two")
    mem.add_fact("k3", "gamma other")
    out = mem.query("widget", limit=10)
    assert len(out) == 2
    keys = {r["key"] for r in out}
    assert keys == {"k1", "k2"}


def test_keyword_filtering_no_match(tmp_path: Path) -> None:
    mem = SemanticMemory(tmp_path / "semantic")
    mem.add_fact("a", "only oranges here")
    assert mem.query("apples") == []
    assert mem.query("oranges") != []


def test_recency_newest_first(tmp_path: Path) -> None:
    mem = SemanticMemory(tmp_path / "semantic")
    mem.add_fact("old", "shared token first wave")
    time.sleep(0.02)
    mem.add_fact("new", "shared token second wave")
    out = mem.query("shared", limit=10)
    assert len(out) == 2
    assert out[0]["key"] == "new"
    assert out[1]["key"] == "old"


def test_tags_and_source_persisted(tmp_path: Path) -> None:
    mem = SemanticMemory(tmp_path / "semantic")
    mem.add_fact("k", "hello world", tags=["t1", "t2"], source="unit_test")
    out = mem.query("hello")
    assert len(out) == 1
    assert out[0]["tags"] == ["t1", "t2"]
    assert out[0]["source"] == "unit_test"


def test_get_semantic_memory_dir_default_resolves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("AGENT_V2_SEMANTIC_MEMORY_DIR", raising=False)
    d = get_semantic_memory_dir()
    assert d == (tmp_path / ".agent_memory" / "semantic").resolve()


def test_get_semantic_memory_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = tmp_path / "custom_sem"
    monkeypatch.setenv("AGENT_V2_SEMANTIC_MEMORY_DIR", str(override))
    assert get_semantic_memory_dir() == override.resolve()


def test_query_empty_or_zero_limit(tmp_path: Path) -> None:
    mem = SemanticMemory(tmp_path / "semantic")
    mem.add_fact("k", "something")
    assert mem.query("") == []
    assert mem.query("   ") == []
    assert mem.query("something", limit=0) == []


def test_append_only_no_overwrite(tmp_path: Path) -> None:
    mem = SemanticMemory(tmp_path / "semantic")
    mem.add_fact("same", "duplicate topic one")
    mem.add_fact("same", "duplicate topic two")
    lines = (tmp_path / "semantic" / "facts.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    objs = [json.loads(L) for L in lines]
    assert objs[0]["text"] != objs[1]["text"]


def test_query_only_scans_last_n_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(semantic_memory_mod, "MAX_FACTS_READ", 3)
    mem = SemanticMemory(tmp_path / "semantic")
    for i in range(2):
        mem.add_fact(f"old{i}", f"keyword old batch {i}")
    for i in range(3):
        mem.add_fact(f"new{i}", f"keyword new batch {i}")
    out = mem.query("keyword", limit=20)
    keys = {r["key"] for r in out}
    assert keys == {"new0", "new1", "new2"}


def test_legacy_jsonl_without_text_lower(tmp_path: Path) -> None:
    path = tmp_path / "semantic" / "facts.jsonl"
    path.parent.mkdir(parents=True)
    legacy = (
        '{"key":"L","text":"hello from legacy","tags":[],"timestamp":"2020-01-01T00:00:00+00:00"}\n'
    )
    path.write_text(legacy, encoding="utf-8")
    mem = SemanticMemory(tmp_path / "semantic")
    out = mem.query("legacy")
    assert len(out) == 1 and out[0]["key"] == "L"
