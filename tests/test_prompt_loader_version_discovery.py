"""Tests for highest vN.yaml resolution under prompt_versions/.../models/<model>/."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.prompt_system.loader import (
    _PROMPT_VERSIONS_DIR,
    discover_highest_v_prompt_yaml,
    load_from_flat_packaged,
    load_from_versioned,
    load_prompt,
    normalize_model_name_for_path,
)


def test_discover_highest_picks_max_n(tmp_path: Path) -> None:
    d = tmp_path / "models" / "m"
    d.mkdir(parents=True)
    (d / "v1.yaml").write_text("a: 1\n", encoding="utf-8")
    (d / "v2.yaml").write_text("a: 2\n", encoding="utf-8")
    (d / "v10.yaml").write_text("a: 10\n", encoding="utf-8")
    (d / "notes.txt").write_text("x", encoding="utf-8")
    ver, p = discover_highest_v_prompt_yaml(d)
    assert ver == "v10"
    assert p.name == "v10.yaml"


def test_discover_highest_case_insensitive_prefix(tmp_path: Path) -> None:
    d = tmp_path / "m"
    d.mkdir()
    (d / "V2.yaml").write_text("x: 1\n", encoding="utf-8")
    ver, p = discover_highest_v_prompt_yaml(d)
    assert ver == "v2"
    assert p.name == "V2.yaml"


def test_discover_highest_missing_dir_raises(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with pytest.raises(FileNotFoundError, match="does not exist"):
        discover_highest_v_prompt_yaml(missing)


def test_discover_highest_empty_dir_raises(tmp_path: Path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(FileNotFoundError, match="No v<number>.yaml"):
        discover_highest_v_prompt_yaml(d)


def test_load_prompt_planner_decision_prefers_v2_when_present() -> None:
    model = normalize_model_name_for_path("qwen2.5-coder-7b")
    assert model
    model_dir = _PROMPT_VERSIONS_DIR / "planner.decision.v1" / "models" / model
    if not (model_dir / "v2.yaml").is_file():
        pytest.skip("v2.yaml not in repo")
    t = load_prompt(
        "planner.decision.v1",
        version="latest",
        model_name="qwen2.5-coder-7b",
    )
    assert t.version == "v2"


def test_load_from_versioned_model_subdir_prefers_highest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch prompt_versions root to use a fake exploration pack with v1 + v3."""
    fake_root = tmp_path / "prompt_versions"
    pack = fake_root / "exploration.fake_test"
    mdir = pack / "models" / "testmodel"
    mdir.mkdir(parents=True)
    (mdir / "v1.yaml").write_text("system_prompt: one\n", encoding="utf-8")
    (mdir / "v3.yaml").write_text("system_prompt: three\n", encoding="utf-8")

    import agent.prompt_system.loader as loader

    monkeypatch.setattr(loader, "_PROMPT_VERSIONS_DIR", fake_root)
    t = loader.load_from_versioned("exploration.fake_test", "v1", model_name="testmodel")
    assert t is not None
    assert t.version == "v3"
    assert "three" in (t.system_prompt or "")


def test_load_from_flat_packaged_existing_dir_without_v_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_root = tmp_path / "pv"
    name = "planner.decision.v1"
    norm = normalize_model_name_for_path("qwen2.5-coder-7b")
    assert norm
    mdir = fake_root / name / "models" / norm
    mdir.mkdir(parents=True)
    (mdir / "draft.yaml").write_text("x: 1\n", encoding="utf-8")

    import agent.prompt_system.loader as loader

    monkeypatch.setattr(loader, "_PROMPT_VERSIONS_DIR", fake_root)
    with pytest.raises(FileNotFoundError, match="No v<number>.yaml"):
        load_from_flat_packaged(name, model_name="qwen2.5-coder-7b")
