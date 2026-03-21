"""Stage 46: repo_map lookup tiers — normalization + optional library-backed typo fallback."""

import json
from pathlib import Path

import pytest

from agent.retrieval import repo_map_lookup
from agent.retrieval.repo_map_lookup import lookup_repo_map
from config import retrieval_config
from config.repo_graph_config import REPO_MAP_JSON, SYMBOL_GRAPH_DIR


def _write_repo_map(tmp_path: Path, symbols: dict) -> str:
    root = tmp_path
    d = root / SYMBOL_GRAPH_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / REPO_MAP_JSON
    path.write_text(json.dumps({"symbols": symbols}), encoding="utf-8")
    return str(root)


@pytest.fixture
def typo_on(monkeypatch):
    monkeypatch.setattr(retrieval_config, "ENABLE_REPO_MAP_TYPO_FALLBACK", True)


@pytest.fixture
def typo_off(monkeypatch):
    monkeypatch.setattr(retrieval_config, "ENABLE_REPO_MAP_TYPO_FALLBACK", False)


class TestLookupRepoMapStage46:
    def test_exact_hit_preserved(self, tmp_path):
        sym = {"StepExecutor": {"file": "agent/ex.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("StepExecutor", project_root=root)
        assert len(out) == 1
        assert out[0]["anchor"] == "StepExecutor"
        assert out[0]["file"] == "agent/ex.py"

    def test_exact_wins_over_typo_candidate(self, tmp_path, typo_on):
        """Exact tier match returns immediately; typo tier does not reorder or add."""
        sym = {
            "StepExecutor": {"file": "exact.py"},
            "StubExecutor": {"file": "typo.py"},
        }
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("StepExecutor", project_root=root)
        anchors = [x["anchor"] for x in out]
        assert anchors == ["StepExecutor"]

    def test_spaced_and_camel_equivalent(self, tmp_path):
        sym = {"FooBar": {"file": "f.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("foo bar", project_root=root)
        anchors = [x["anchor"] for x in out]
        assert "FooBar" in anchors

    def test_hyphen_underscore_normalized(self, tmp_path):
        sym = {"step_executor": {"file": "s.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("step-executor", project_root=root)
        anchors = [x["anchor"] for x in out]
        assert "step_executor" in anchors

    def test_descriptor_phrase_finds_symbol(self, tmp_path):
        sym = {"StepExecutor": {"file": "e.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("StepExecutor class", project_root=root)
        anchors = [x["anchor"] for x in out]
        assert "StepExecutor" in anchors

    def test_typo_disabled_miss(self, tmp_path, typo_off):
        sym = {"StepExecutor": {"file": "e.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("StipExecutor", project_root=root)
        assert out == []

    def test_typo_enabled_one_edit(self, tmp_path, typo_on):
        sym = {"StepExecutor": {"file": "e.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("StipExecutor", project_root=root)
        anchors = [x["anchor"] for x in out]
        assert anchors == ["StepExecutor"]

    def test_distance_gt_one_no_match(self, tmp_path, typo_on):
        sym = {"PolicyEngine": {"file": "p.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("PlocyEngXYZ", project_root=root)
        assert out == []

    def test_short_token_no_fuzzy(self, tmp_path, typo_on):
        """Normalized query term length < 6 skips typo tier (tiers 1–3 must miss)."""
        sym = {"abcdf": {"file": "a.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("abcde", project_root=root)
        assert out == []

    def test_denylisted_term_no_typo(self, tmp_path, typo_on, monkeypatch):
        """Denylist applies to typo tier only; symbol chosen so tiers 1–2 do not match."""
        monkeypatch.setattr(
            repo_map_lookup,
            "_TYPO_GENERIC_DENYLIST",
            frozenset({"blocktoken"}),
        )
        sym = {"BlockTokn": {"file": "b.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("blocktoken", project_root=root)
        assert out == []

    def test_short_query_run_no_typo_flood(self, tmp_path, typo_on):
        sym = {"foo": {"file": "a.py"}, "bar": {"file": "b.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("run", project_root=root)
        assert out == []

    def test_canonical_tier_when_substring_fails(self, tmp_path):
        """foo_bar token vs FooBar key: substring tier does not connect; canonical tier does."""
        sym = {"FooBar": {"file": "f.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("foo_bar", project_root=root)
        assert [x["anchor"] for x in out] == ["FooBar"]

    def test_typo_cap_respected(self, tmp_path, typo_on, monkeypatch):
        monkeypatch.setattr(retrieval_config, "REPO_MAP_TYPO_MAX_MATCHES", 3)
        sym = {
            "Byaaaaaa": {"file": "1.py"},
            "Bzaaaaaa": {"file": "2.py"},
            "Bwaaaaaa": {"file": "3.py"},
            "Bvaaaaaa": {"file": "4.py"},
        }
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("Bxaaaaaa", project_root=root)
        assert len(out) == 3
        anchors = [x["anchor"] for x in out]
        assert anchors == ["Bvaaaaaa", "Bwaaaaaa", "Byaaaaaa"]

    def test_typo_deterministic_order_same_distance(self, tmp_path, typo_on):
        sym = {
            "Byaaaaaa": {"file": "1.py"},
            "Bzaaaaaa": {"file": "2.py"},
        }
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("Bxaaaaaa", project_root=root)
        assert [x["anchor"] for x in out] == ["Byaaaaaa", "Bzaaaaaa"]

    def test_normalized_snake_typo_single_token(self, tmp_path, typo_on):
        """Normalized alphanumerics align snake_case query token with repo symbol (tier 2/3 miss)."""
        sym = {"step_executor": {"file": "s.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("step_exector", project_root=root)
        assert [x["anchor"] for x in out] == ["step_executor"]


class TestRepoMapTypoGuardsStage46_1:
    """Guard tests: tier short-circuit, schema, NL boundedness, mixed forms without typo tier."""

    def test_substring_hit_prevents_typo_extra_anchors(self, tmp_path, typo_on):
        """Tier 2 match returns early; StipExecutor is not appended via tier 4."""
        sym = {
            "StepExecutor": {"file": "a.py"},
            "StipExecutor": {"file": "b.py"},
        }
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("Step", project_root=root)
        assert [x["anchor"] for x in out] == ["StepExecutor"]

    def test_canonical_hit_prevents_typo_extra_anchors(self, tmp_path, typo_on):
        """Tier 3 match returns early; typo tier does not add a second symbol."""
        sym = {
            "FooBar": {"file": "a.py"},
            "FoodBar": {"file": "b.py"},
        }
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("foo_bar", project_root=root)
        assert [x["anchor"] for x in out] == ["FooBar"]

    def test_mixed_identifier_forms_without_typo_tier(self, tmp_path):
        """PascalCase, snake, hyphen, spaced queries resolve via tiers 1–3 only."""
        sym = {"step_executor": {"file": "s.py"}}
        root = _write_repo_map(tmp_path, sym)
        for q in ("StepExecutor", "step_executor", "step-executor", "step executor"):
            out = lookup_repo_map(q, project_root=root)
            assert [x["anchor"] for x in out] == ["step_executor"]

    def test_natural_language_sentence_bounded_no_flood(self, tmp_path, typo_on, monkeypatch):
        """Long prose: identifier-like tokens only; result count stays within typo cap."""
        monkeypatch.setattr(retrieval_config, "REPO_MAP_TYPO_MAX_MATCHES", 3)
        sym = {f"Sym{i:03d}": {"file": f"{i}.py"} for i in range(40)}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map(
            "please explain whether the quick brown fox jumps over the lazy dog handler",
            project_root=root,
        )
        assert len(out) <= 3
        for row in out:
            assert set(row.keys()) == {"anchor", "file"}

    def test_output_rows_are_anchor_and_file_only(self, tmp_path, typo_on):
        sym = {"StepExecutor": {"file": "e.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("StipExecutor", project_root=root)
        assert len(out) == 1
        assert set(out[0].keys()) == {"anchor", "file"}
        assert out[0]["anchor"] == "StepExecutor"
        assert out[0]["file"] == "e.py"

    def test_typo_fallback_identical_across_repeated_calls(self, tmp_path, typo_on):
        sym = {"StepExecutor": {"file": "e.py"}}
        root = _write_repo_map(tmp_path, sym)
        q = "StipExecutor"
        a = lookup_repo_map(q, project_root=root)
        b = lookup_repo_map(q, project_root=root)
        c = lookup_repo_map(q, project_root=root)
        assert a == b == c

    def test_all_short_or_denied_terms_skip_typo_without_crash(self, tmp_path, typo_on):
        """No eligible typo terms → [] without scanning symbols for edit distance."""
        sym = {"StepExecutor": {"file": "e.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("run get set", project_root=root)
        assert out == []

    def test_builtin_denylist_term_runner_not_denied(self, tmp_path, typo_on):
        """Production denylist is exact token match; 'runner' is not listed."""
        sym = {"RunnerX": {"file": "r.py"}}
        root = _write_repo_map(tmp_path, sym)
        out = lookup_repo_map("RunnerY", project_root=root)
        assert [x["anchor"] for x in out] == ["RunnerX"]
