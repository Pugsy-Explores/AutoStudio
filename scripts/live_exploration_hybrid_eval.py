#!/usr/bin/env python3
"""
Live evaluation: ExplorationEngineV2 → WorkingMemory → adapter → (optional) LLM synthesis → FinalExplorationSchema.

Uses the real repo, real Dispatcher (_dispatch_react), and dynamic symbol discovery (no hardcoded symbols).

Mode:
  --stub-exploration-llm — deterministic branch-router; strict structural assertions (e.g. bucket D empty evidence).
  --llm — live reasoning model; behavioral assertions only where noted (buckets D, F).

Usage (from AutoStudio repo root):
  python3 scripts/live_exploration_hybrid_eval.py
  python3 scripts/live_exploration_hybrid_eval.py --buckets A,B,D --max-cases 4
  python3 scripts/live_exploration_hybrid_eval.py --bucket F --llm  # hybrid compare needs synthesis LLM

Environment:
  AGENT_V2_ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS — overridden by --synthesis / harness patches for runs.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Repo root (parent of scripts/)
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _ensure_repo_path() -> None:
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Dynamic repo discovery (no hardcoded symbol names)
# ---------------------------------------------------------------------------


def _iter_agent_py_files(root: Path, *, max_files: int = 400) -> list[Path]:
    out: list[Path] = []
    agent = root / "agent_v2"
    if not agent.is_dir():
        return out
    for p in sorted(agent.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        if "tests" in p.parts or p.name.startswith("test_"):
            continue
        out.append(p)
        if len(out) >= max_files:
            break
    return out


def _parse_module_symbols(path: Path) -> tuple[list[str], list[str]]:
    try:
        src = path.read_text(encoding="utf-8")
    except OSError:
        return [], []
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return [], []
    classes: list[str] = []
    funcs: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            classes.append(node.name)
        elif isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
            funcs.append(node.name)
    return classes, funcs


def _count_occurrences_in_agent_v2(root: Path, token: str) -> int:
    n = 0
    agent = root / "agent_v2"
    for p in agent.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if token in t:
            n += 1
    return n


def _find_class_relpath(repo_root: Path, class_name: str) -> str | None:
    """First agent_v2 module that defines ``class_name`` (AST), else None."""
    for p in _iter_agent_py_files(repo_root):
        try:
            cls_names, _ = _parse_module_symbols(p)
        except OSError:
            continue
        if class_name in cls_names:
            return str(p.relative_to(repo_root))
    return None


def _extract_config_constant_names(config_path: Path) -> list[str]:
    if not config_path.is_file():
        return []
    text = config_path.read_text(encoding="utf-8", errors="ignore")
    names = re.findall(r"^([A-Z][A-Z0-9_]*)\s*(?::[^=]+)?=\s*", text, re.MULTILINE)
    # Filter obvious noise
    return [n for n in names if len(n) >= 8 and not n.startswith("DEFAULT_")][:24]


@dataclass
class RepoDiscovery:
    """Symbols and paths discovered from the working tree."""

    simple_class: str
    simple_class_file: str
    multi_hop_class: str
    multi_hop_function: str
    config_constant: str
    cross_file_class: str
    cross_file_class_file: str
    cross_file_hits: int


def discover_repo(repo_root: Path) -> RepoDiscovery:
    py_files = _iter_agent_py_files(repo_root)
    best_class: tuple[str, str] | None = None  # (name, relpath)
    multi_cls = "ExplorationEngineV2"
    multi_fn = "explore"
    engine_path = repo_root / "agent_v2" / "exploration" / "exploration_engine_v2.py"
    if engine_path.is_file():
        cls_e, fn_e = _parse_module_symbols(engine_path)
        for c in cls_e:
            if c == "ExplorationEngineV2":
                multi_cls = c
                break
        for f in fn_e:
            if f == "explore":
                multi_fn = f
                break
    for p in py_files:
        cls, _fn = _parse_module_symbols(p)
        rel = str(p.relative_to(repo_root))
        for c in cls:
            if "exploration" in rel and "engine" in p.name.lower() and c == "ExplorationEngineV2":
                best_class = (c, rel)
                break
        if best_class:
            break

    if best_class is None:
        for p in py_files:
            cls, _f = _parse_module_symbols(p)
            rel = str(p.relative_to(repo_root))
            for c in cls:
                if len(c) >= 8:
                    best_class = (c, rel)
                    break
            if best_class:
                break

    if best_class is None:
        raise RuntimeError("Could not discover any public class under agent_v2/")

    simple_name, simple_rel = best_class
    cfg_path = repo_root / "agent_v2" / "config.py"
    consts = _extract_config_constant_names(cfg_path)
    cfg_const = consts[0] if consts else "ENABLE_EXPLORATION_ENGINE_V2"

    cross_candidates = [
        "ExplorationEngineV2",
        "ExplorationWorkingMemory",
        "Dispatcher",
        "FinalExplorationSchema",
        "QueryIntentParser",
    ]
    cross_cls = multi_cls
    best_hits = 0
    for name in cross_candidates:
        h = _count_occurrences_in_agent_v2(repo_root, name)
        if h > best_hits:
            best_hits = h
            cross_cls = name

    cross_rel = _find_class_relpath(repo_root, cross_cls) or simple_rel

    return RepoDiscovery(
        simple_class=simple_name,
        simple_class_file=simple_rel,
        multi_hop_class=multi_cls,
        multi_hop_function=multi_fn,
        config_constant=cfg_const,
        cross_file_class=cross_cls,
        cross_file_class_file=cross_rel,
        cross_file_hits=best_hits,
    )


# ---------------------------------------------------------------------------
# Harness (imports deferred until env applied)
# ---------------------------------------------------------------------------


def _wire_engine_dispatcher(runner: Any, dispatcher: Any) -> None:
    """ExplorationRunner keeps nested dispatcher refs; keep them consistent."""
    runner.dispatcher = dispatcher
    engine = getattr(runner, "_engine_v2", None)
    if engine is None:
        return
    if hasattr(engine, "_dispatcher"):
        engine._dispatcher = dispatcher
    reader = getattr(engine, "_inspection_reader", None)
    if reader is not None and hasattr(reader, "_dispatcher"):
        reader._dispatcher = dispatcher
    expander = getattr(engine, "_graph_expander", None)
    if expander is not None and hasattr(expander, "_dispatcher"):
        expander._dispatcher = dispatcher


def _patch_synthesis_flags(enabled: bool) -> None:
    import agent_v2.config as cfg
    import agent_v2.exploration.exploration_engine_v2 as eng
    import agent_v2.runtime.exploration_runner as erun

    cfg.ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS = enabled
    eng.ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS = enabled
    erun.ENABLE_EXPLORATION_RESULT_LLM_SYNTHESIS = enabled


def _patch_scoper(enabled: bool) -> None:
    import agent_v2.config as cfg
    import agent_v2.runtime.exploration_runner as erun

    cfg.ENABLE_EXPLORATION_SCOPER = enabled
    erun.ENABLE_EXPLORATION_SCOPER = enabled


def _make_llm_fn(
    *,
    use_llm: bool,
    synthesis_fail: bool,
) -> Callable[[str], str] | None:
    if not use_llm:
        return None
    from agent.models.model_client import call_reasoning_model
    from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_V2

    def _fn(prompt: str) -> str:
        # Result synthesis-only prompt (see exploration_llm_synthesizer._build_prompt)
        if (
            synthesis_fail
            and "You synthesize exploration results" in prompt
            and "objective_coverage" in prompt
        ):
            return "NOT_JSON {{{"
        return call_reasoning_model(prompt, task_name=EXPLORATION_TASK_V2, max_tokens=2048)

    return _fn


def make_exploration_stub_fn(
    d: RepoDiscovery,
    *,
    intent_empty: bool,
    stub_class: str | None = None,
    stub_file: str | None = None,
) -> Callable[[str], str]:
    """
    Deterministic branch-router for exploration LLM stages (same idea as unit tests).
    Uses repo-derived class name + file path — no fixed symbol strings in instructions.
    Optional ``stub_class`` / ``stub_file`` align the stub with a specific instruction (e.g. bucket F).
    """
    cls = stub_class or d.simple_class
    fp = stub_file or d.simple_class_file

    def _fn(prompt: str) -> str:
        p = prompt or ""
        # exploration.scoper — base v1.yaml
        if "Performing codebase exploration with strict precision" in p:
            return '{"selected_indices": [0]}'
        # exploration.scoper — models/qwen2.5-coder-7b/v1.yaml (different copy)
        if "narrowing down search candidates" in p:
            return '{"selected_indices": [0]}'
        if (
            "Limit (maximum selected items):" in p
            or "Select the most relevant candidates for deep inspection" in p
        ):
            return '{"selected_indices": [0]}'
        if "Candidates (indexed):" in p:
            return '{"selected_indices": [0]}'
        if "You are selecting the most relevant code location" in p:
            return json.dumps({"file_path": fp, "symbol": cls})
        if "Classify the snippet based on whether it directly contributes" in p:
            return (
                '{"status":"partial","needs":["definition"],'
                '"reason":"stub","next_action":"expand"}'
            )
        if intent_empty:
            return '{"symbols":[],"keywords":[],"intents":["locate_logic"]}'
        return json.dumps(
            {"symbols": [cls], "keywords": ["explore", "codebase"], "intents": ["find_definition"]}
        )

    return _fn


def _wrap_stub_for_optional_synthesis(
    exploration_fn: Callable[[str], str],
    *,
    synthesis_fail: bool,
) -> Callable[[str], str]:
    """Route result-synthesis prompts to the real reasoning model; exploration stays stubbed."""
    from agent.models.model_client import call_reasoning_model
    from agent_v2.exploration.exploration_task_names import EXPLORATION_TASK_V2

    def _fn(prompt: str) -> str:
        if "You synthesize exploration results" in prompt:
            if synthesis_fail:
                return "NOT_JSON {{{"
            return call_reasoning_model(prompt, task_name=EXPLORATION_TASK_V2, max_tokens=2048)
        return exploration_fn(prompt)

    return _fn


def build_exploration_runner(
    *,
    repo_root: Path,
    use_llm: bool,
    synthesis: bool,
    synthesis_fail: bool,
    scoper: bool = True,
    llm_generate_fn_override: Callable[[str], str] | None = None,
) -> Any:
    _ensure_repo_path()
    os.environ.setdefault("SKIP_STARTUP_CHECKS", "1")
    os.environ["AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2"] = "1"
    os.chdir(repo_root)
    os.environ["SERENA_PROJECT_DIR"] = str(repo_root)

    _patch_synthesis_flags(synthesis)
    _patch_scoper(scoper)

    from agent.tools.react_tools import register_all_tools

    register_all_tools()

    from agent.execution.step_dispatcher import _dispatch_react
    from agent_v2.runtime.bootstrap import _exploration_action_fn, _next_action
    from agent_v2.runtime.action_generator import ActionGenerator
    from agent_v2.runtime.exploration_runner import ExplorationRunner
    from agent_v2.runtime.dispatcher import Dispatcher

    if llm_generate_fn_override is not None:
        llm_fn = llm_generate_fn_override
    else:
        llm_fn = _make_llm_fn(use_llm=use_llm, synthesis_fail=synthesis_fail)

    runner = ExplorationRunner(
        action_generator=ActionGenerator(fn=_next_action, exploration_fn=_exploration_action_fn),
        dispatcher=Dispatcher(execute_fn=_dispatch_react),
        llm_generate_fn=llm_fn,
        enable_v2=True,
    )
    _wire_engine_dispatcher(runner, runner.dispatcher)
    return runner


# ---------------------------------------------------------------------------
# Assertions & fingerprints
# ---------------------------------------------------------------------------


def fingerprint_evidence_paths(final: Any) -> tuple[tuple[str, str, str], ...]:
    items = sorted(final.evidence, key=lambda it: (getattr(it.source, "ref", ""), it.item_id))
    return tuple(
        (
            str(getattr(it.source, "ref", "") or ""),
            (it.content.summary or "")[:160],
            str(getattr(it.metadata, "tool_name", "") or ""),
        )
        for it in items
    )


def fingerprint_relationships(final: Any) -> tuple[tuple[str, str, str], ...]:
    return tuple(
        sorted(
            ((e.from_key, e.to_key, e.type) for e in final.relationships),
            key=lambda t: (t[0], t[1], t[2]),
        )
    )


def fingerprint_gaps(final: Any) -> tuple[str, ...]:
    return tuple(final.exploration_summary.knowledge_gaps)


def assert_memory_matches_final(memory: Any, final: Any) -> None:
    snap = memory.get_summary()
    mev = snap.get("evidence") or []
    mrel = snap.get("relationships") or []
    mgaps = [str(g.get("description") or "") for g in (snap.get("gaps") or []) if g.get("description")]

    assert len(final.evidence) == len(mev), "evidence count vs working memory summary"
    f_files = sorted(str(it.source.ref) for it in final.evidence)
    m_files = sorted(str(ev.get("file") or "") for ev in mev)
    assert f_files == m_files, "evidence file paths differ from memory"
    assert len(final.relationships) == len(mrel), "relationship count"
    assert list(final.exploration_summary.knowledge_gaps) == mgaps, "gaps list mismatch"


def assert_schema_roundtrip(final: Any) -> None:
    from agent_v2.schemas.final_exploration import FinalExplorationSchema

    data = final.model_dump(mode="python")
    FinalExplorationSchema.model_validate(data)


def assert_no_string_relationships(final: Any) -> None:
    from agent_v2.schemas.final_exploration import ExplorationRelationshipEdge

    for e in final.relationships:
        assert isinstance(e, ExplorationRelationshipEdge)


# ---------------------------------------------------------------------------
# Outcome quality — lightweight signal validation (not scoring / heuristics)
# ---------------------------------------------------------------------------

_VAGUE_GAP_SUBSTRINGS: tuple[str, ...] = (
    "need more context",
    "more context",
    "insufficient context",
    "unclear",
    "unknown",
    "more details",
    "missing details",
)


def _item_text_blob(it: Any) -> str:
    loc = str(getattr(getattr(it, "source", None), "location", "") or "")
    parts = [
        loc,
        str(it.content.summary or ""),
        " ".join(str(x) for x in (it.content.key_points or [])),
        " ".join(str(x) for x in (it.content.entities or [])),
        str(it.snippet or ""),
    ]
    return "\n".join(parts)


def _target_hits_evidence_item(it: Any, t: str) -> bool:
    """True if target appears in grounded text or in evidence file ref (path signal)."""
    if t in _item_text_blob(it):
        return True
    ref = str(getattr(it.source, "ref", "") or "")
    return bool(ref and t in ref)


def _edge_file_part(key: str) -> str:
    k = (key or "").strip()
    if "::" in k:
        return k.split("::", 1)[0].strip()
    return k


def outcome_quality_sanity_checks(
    final: Any,
    instruction: str,
    *,
    targets: frozenset[str],
    skip: bool,
) -> None:
    """
    Minimal checks that output still relates to the instruction (signal validation).

    1) At least one evidence row mentions a target symbol, OR a relationship endpoint does,
       OR (when relationships exist) edges tie to evidence file paths.
    2) Each relationship edge touches a target substring or an evidence file path.
    3) Non-empty gaps are actionable (minimum length; reject short vague-only lines).
    4) When optional LLM synthesis succeeded, key_insights are not empty echoes of the instruction.
    """
    if skip or not targets:
        return

    if not final.evidence and not final.relationships:
        raise AssertionError(
            "Outcome quality: expected evidence or relationships when quality targets are set "
            f"(targets={sorted(targets)})"
        )

    ev_files = {str(it.source.ref) for it in final.evidence}

    # --- 1: signal vs targets (evidence OR graph)
    if final.evidence:
        ev_hit = any(
            any(_target_hits_evidence_item(it, t) for t in targets)
            for it in final.evidence
        )
        rel_hit = any(
            any(t in (e.from_key + e.to_key) for t in targets)
            for e in final.relationships
        )
        if not ev_hit and not rel_hit:
            raise AssertionError(
                "Outcome quality: expected evidence or relationships to reference instruction targets "
                f"{sorted(targets)}"
            )
    elif final.relationships:
        rel_hit = any(
            any(t in (e.from_key + e.to_key) for t in targets)
            for e in final.relationships
        )
        if not rel_hit:
            raise AssertionError(
                "Outcome quality: relationships do not reference instruction targets "
                f"{sorted(targets)}"
            )

    # --- 2: edges connect to targets or discovered evidence files
    if final.relationships:
        for e in final.relationships:
            blob = f"{e.from_key} {e.to_key}"
            target_touch = any(t in blob for t in targets)
            file_touch = (
                _edge_file_part(e.from_key) in ev_files or _edge_file_part(e.to_key) in ev_files
            )
            if not target_touch and not file_touch:
                raise AssertionError(
                    "Outcome quality: relationship edge must reference a target symbol or connect "
                    f"evidence file paths; got {e.from_key} --{e.type}--> {e.to_key}"
                )

    # --- 3: gaps
    for g in final.exploration_summary.knowledge_gaps:
        gs = (g or "").strip()
        if len(gs) < 12:
            raise AssertionError(f"Outcome quality: gap too short to be actionable: {g!r}")
        low = gs.lower()
        if len(gs) < 36 and any(v in low for v in _VAGUE_GAP_SUBSTRINGS):
            raise AssertionError(f"Outcome quality: gap looks vague-only: {g!r}")

    # --- 4: key_insights (only when optional LLM synthesis actually succeeded)
    if final.trace.llm_used and final.trace.synthesis_success and final.key_insights:
        ins_low = instruction.strip().lower()
        for ki in final.key_insights:
            s = (ki or "").strip()
            if len(s) < 15:
                raise AssertionError(f"Outcome quality: key_insight too short / trivial: {ki!r}")
            sl = s.lower()
            if sl == ins_low or (len(ins_low) > 12 and ins_low in sl):
                raise AssertionError("Outcome quality: key_insight echoes instruction (trivial)")


@dataclass
class CaseResult:
    bucket: str
    label: str
    instruction: str
    final: Any
    memory: Any | None
    skipped_reason: str | None = None


def run_instruction(
    runner: Any,
    instruction: str,
) -> tuple[Any, Any]:
    final = runner.run(instruction)
    engine = runner._engine_v2
    memory = getattr(engine, "last_working_memory", None)
    return final, memory


def log_case_line(
    *,
    instruction: str,
    final: Any,
) -> None:
    md = final.metadata
    tr = final.trace
    print(
        f"  instruction: {instruction[:200]}{'…' if len(instruction) > 200 else ''}\n"
        f"  termination_reason: {md.termination_reason}\n"
        f"  status: {final.status} (completion={md.completion_status})\n"
        f"  evidence_count: {len(final.evidence)}\n"
        f"  relationships_count: {len(final.relationships)}\n"
        f"  gaps: {list(final.exploration_summary.knowledge_gaps)}\n"
        f"  key_insights: {final.key_insights}\n"
        f"  llm_used: {tr.llm_used} synthesis_success: {tr.synthesis_success}\n"
    )


# ---------------------------------------------------------------------------
# Bucket builders
# ---------------------------------------------------------------------------


@dataclass
class EvalCase:
    bucket: str
    label: str
    instruction: str
    quality_targets: frozenset[str] = field(default_factory=frozenset)
    skip_outcome_quality: bool = False
    """Bucket D only: bogus symbol string for live robustness checks."""
    d_bogus_symbol: str | None = None
    check: Callable[[CaseResult], None] | None = None


def _build_cases(d: RepoDiscovery, buckets: set[str]) -> list[EvalCase]:
    cases: list[EvalCase] = []

    if "A" in buckets:
        cases.append(
            EvalCase(
                bucket="A",
                label="simple_single_hop",
                instruction=(
                    f"Find where class {d.simple_class} is defined and summarize how it is used."
                ),
                quality_targets=frozenset({d.simple_class}),
                check=lambda r: _check_A(r, d),
            )
        )

    if "B" in buckets:
        cases.append(
            EvalCase(
                bucket="B",
                label="multi_hop_graph",
                instruction=(
                    f"Understand the flow of {d.multi_hop_class}: trace callers of "
                    f"{d.multi_hop_function} and related entry points."
                ),
                quality_targets=frozenset({d.multi_hop_class, d.multi_hop_function}),
                check=_check_B,
            )
        )

    if "C" in buckets:
        cases.append(
            EvalCase(
                bucket="C",
                label="config_partial",
                instruction=(
                    f"Where is configuration constant {d.config_constant} referenced, "
                    f"and what environment or code paths affect its behavior?"
                ),
                # Config symbol often does not appear in capped evidence rows under stub/short runs;
                # outcome quality for C is optional (enable with live LLM or widen harness).
                quality_targets=frozenset({d.config_constant}),
                skip_outcome_quality=True,
                check=_check_C,
            )
        )

    if "D" in buckets:
        bogus = f"NonexistentSymbol{uuid.uuid4().hex[:12]}"
        cases.append(
            EvalCase(
                bucket="D",
                label="no_candidates",
                instruction=f"Find the definition and all usages of {bogus} in the repository.",
                quality_targets=frozenset({bogus}),
                skip_outcome_quality=True,
                d_bogus_symbol=bogus,
                check=None,
            )
        )

    if "E" in buckets:
        cases.append(
            EvalCase(
                bucket="E",
                label="utility_stop_best_effort",
                instruction="Locate the function named zzz_nonexistent_exploration_probe_utility.",
                skip_outcome_quality=True,
                check=_check_E,
            )
        )

    if "F" in buckets:
        cases.append(
            EvalCase(
                bucket="F",
                label="hybrid_determinism",
                instruction=(
                    f"Summarize responsibilities of {d.cross_file_class} and list one file where it appears."
                ),
                quality_targets=frozenset({d.cross_file_class}),
                check=None,
            )
        )

    if "G" in buckets:
        cases.append(
            EvalCase(
                bucket="G",
                label="synthesis_failure_safety",
                instruction=(
                    f"Find where {d.simple_class} is defined."
                ),
                quality_targets=frozenset({d.simple_class}),
                check=_check_G,
            )
        )

    return cases


def _check_A(r: CaseResult, d: RepoDiscovery) -> None:
    assert r.memory is not None
    assert 1 <= len(r.final.evidence) <= 6
    assert len(r.final.relationships) <= 4
    assert_schema_roundtrip(r.final)
    assert_memory_matches_final(r.memory, r.final)


def _check_B(r: CaseResult) -> None:
    assert r.memory is not None
    assert_schema_roundtrip(r.final)
    assert_memory_matches_final(r.memory, r.final)
    assert_no_string_relationships(r.final)
    # Multi-hop: expect either multiple evidence rows or at least one relationship edge
    if len(r.final.evidence) < 2 and len(r.final.relationships) == 0:
        raise AssertionError("Bucket B expected multi-hop signal (more evidence or relationships)")


def _check_C(r: CaseResult) -> None:
    assert r.memory is not None
    assert_schema_roundtrip(r.final)
    assert_memory_matches_final(r.memory, r.final)
    if r.final.status != "incomplete":
        return
    gaps = r.final.exploration_summary.knowledge_gaps
    for g in gaps:
        gl = g.strip().lower()
        assert len(gl) >= 8
        assert "more context" not in gl


# Bucket D: stub = deterministic empty evidence; live = safe termination + no bogus grounding.
_D_STUB_TERMINATION_OK: frozenset[str] = frozenset(
    {
        "no_relevant_candidate",
        "no_results",
        "stalled",
        "unknown",
        "missing_symbol_signal",
        "low_relevance",
        "too_broad",
        "too_narrow",
        "pending_exhausted",
    }
)
_D_LIVE_TERMINATION_OK: frozenset[str] = frozenset(
    {"pending_exhausted", "no_improvement_streak", "max_steps"}
)


def _evidence_strongly_matches_bogus(it: Any, bogus: str) -> bool:
    """True if grounded file text or structured entities claim the bogus symbol exists in-repo."""
    if bogus in (it.snippet or ""):
        return True
    for x in it.content.entities or []:
        if bogus in str(x):
            return True
    return False


def _check_D(r: CaseResult, *, stub_mode: bool, bogus: str) -> None:
    assert_schema_roundtrip(r.final)
    tr = r.final.metadata.termination_reason
    if stub_mode:
        assert r.final.evidence == []
        assert tr in _D_STUB_TERMINATION_OK
        return
    assert tr in _D_LIVE_TERMINATION_OK, f"Bucket D live: unexpected termination_reason={tr!r}"
    if r.final.evidence:
        for it in r.final.evidence:
            if _evidence_strongly_matches_bogus(it, bogus):
                raise AssertionError(
                    "Bucket D live: evidence row appears to ground the bogus symbol in-repo "
                    f"(snippet/entities/key_points); bogus={bogus!r}"
                )


def _check_E(r: CaseResult) -> None:
    assert_schema_roundtrip(r.final)
    if r.final.metadata.termination_reason == "no_improvement_streak":
        assert r.final.status in ("complete", "incomplete")
    else:
        r.skipped_reason = "termination was not no_improvement_streak (environment-dependent)"


def _check_G(r: CaseResult) -> None:
    assert r.final.trace.llm_used is True
    assert r.final.trace.synthesis_success is False
    assert_schema_roundtrip(r.final)


_F_SELECTOR_EMPTY_MSG = "no matchable selections"


def run_bucket_F_hybrid(
    repo_root: Path,
    d: RepoDiscovery,
    *,
    use_llm: bool,
    scoper: bool,
    stub_exploration_llm: bool,
    apply_outcome_quality: bool,
) -> None:
    """
    Same exploration wiring twice: synthesis off vs on; compare factual fields.

    Exploration stages use the same per-task call_reasoning_model routing when llm_generate_fn is None.
    Synthesis only runs when enabled; it must not change evidence, relationships, or gaps.

    Live LLM: empty batch selector is probabilistic — one retry, then skip (llm_selector_unstable).
    Stub: strict — any failure propagates.
    """
    instr = (
        f"Summarize responsibilities of {d.cross_file_class} and list one file where it appears."
    )

    def _run_once() -> tuple[Any, Any]:
        # Run 1: no synthesis layer
        base_stub = (
            make_exploration_stub_fn(
                d,
                intent_empty=False,
                stub_class=d.cross_file_class,
                stub_file=d.cross_file_class_file,
            )
            if stub_exploration_llm
            else None
        )
        f1_override = base_stub
        f2_override = (
            _wrap_stub_for_optional_synthesis(base_stub, synthesis_fail=False)
            if stub_exploration_llm
            else None
        )

        _patch_synthesis_flags(False)
        r1 = build_exploration_runner(
            repo_root=repo_root,
            use_llm=use_llm,
            synthesis=False,
            synthesis_fail=False,
            scoper=scoper,
            llm_generate_fn_override=f1_override,
        )
        f1, _ = run_instruction(r1, instr)
        _patch_synthesis_flags(True)
        r2 = build_exploration_runner(
            repo_root=repo_root,
            use_llm=use_llm,
            synthesis=True,
            synthesis_fail=False,
            scoper=scoper,
            llm_generate_fn_override=f2_override,
        )
        f2, _ = run_instruction(r2, instr)
        return f1, f2

    attempt = 0
    while True:
        try:
            f1, f2 = _run_once()
            break
        except ValueError as e:
            if _F_SELECTOR_EMPTY_MSG not in str(e):
                raise
            if stub_exploration_llm or not use_llm:
                raise
            attempt += 1
            if attempt == 1:
                print("  note: bucket F retry after empty LLM selector batch")
                continue
            print("  note: bucket F skipped (llm_selector_unstable)")
            return

    fp1 = (
        f1.status,
        f1.metadata.termination_reason,
        fingerprint_evidence_paths(f1),
        fingerprint_relationships(f1),
        fingerprint_gaps(f1),
    )
    fp2 = (
        f2.status,
        f2.metadata.termination_reason,
        fingerprint_evidence_paths(f2),
        fingerprint_relationships(f2),
        fingerprint_gaps(f2),
    )
    if fp1 != fp2:
        raise AssertionError(f"Hybrid compare: deterministic fields differ:\n  {fp1!r}\n  {fp2!r}")
    # Outcome quality needs grounded evidence rows; stubbed exploration often caps before
    # the defining file/symbol appears. Compare fingerprints under stub; validate targets without stub.
    if apply_outcome_quality and not stub_exploration_llm:
        rel = d.cross_file_class_file.replace("\\", "/")
        outcome_quality_sanity_checks(
            f1,
            instr,
            targets=frozenset({d.cross_file_class, rel}),
            skip=False,
        )
    if f2.trace.synthesis_success:
        print("  [F] synthesis layer ran; compare key_insights / objective_coverage manually if needed.")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live exploration + hybrid adapter evaluation")
    p.add_argument("--repo-root", type=Path, default=_REPO_ROOT, help="Project root (default: AutoStudio root)")
    p.add_argument(
        "--buckets",
        type=str,
        default="A,B,C,D,E,F",
        help="Comma-separated buckets A–G (default: A–F). G=synthesis failure safety (needs --llm --synthesis-fail)",
    )
    p.add_argument("--max-cases", type=int, default=32, help="Maximum number of eval cases to run")
    p.add_argument("--llm", action="store_true", help="Use call_reasoning_model for exploration + synthesis stages")
    p.add_argument(
        "--stub-exploration-llm",
        action="store_true",
        help=(
            "Use deterministic branch-router for exploration LLM calls (repo-derived symbols in stub JSON). "
            "Recommended for CI. Mutually exclusive with --llm for exploration (synthesis in bucket F still uses real model when enabled)."
        ),
    )
    p.add_argument(
        "--synthesis-fail",
        action="store_true",
        help="For bucket G only: force synthesis JSON failure (requires --llm)",
    )
    p.add_argument("--fail-fast", action="store_true", help="Stop on first assertion error")
    p.add_argument(
        "--no-scoper",
        action="store_true",
        help="Disable exploration scoper (faster; avoids strict empty selection on edge queries)",
    )
    p.add_argument(
        "--d-keep-scoper",
        action="store_true",
        help="For bucket D only: keep scoper enabled (strict mode may raise on bogus symbols)",
    )
    p.add_argument(
        "--no-quality-sanity",
        action="store_true",
        help="Disable outcome quality sanity checks (instruction targets vs evidence/relationships/gaps/insights)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.llm and args.stub_exploration_llm:
        print("Use either --llm or --stub-exploration-llm, not both.")
        return 1
    repo_root = Path(args.repo_root).resolve()
    bucket_set = {x.strip().upper() for x in args.buckets.split(",") if x.strip()}

    print(f"Repo: {repo_root}")
    print("Discovering symbols under agent_v2/ …")
    discovery = discover_repo(repo_root)
    print(
        f"  simple_class={discovery.simple_class} ({discovery.simple_class_file})\n"
        f"  multi_hop={discovery.multi_hop_class}.{discovery.multi_hop_function}\n"
        f"  config_constant={discovery.config_constant}\n"
        f"  cross_file={discovery.cross_file_class} ({discovery.cross_file_class_file}, hits≈{discovery.cross_file_hits})\n"
    )

    cases = _build_cases(discovery, bucket_set)[: args.max_cases]
    if not cases:
        print("No cases selected.")
        return 1

    errors: list[str] = []
    for case in cases:
        print(f"\n=== Bucket {case.bucket} — {case.label} ===")
        if case.bucket == "F":
            try:
                run_bucket_F_hybrid(
                    repo_root,
                    discovery,
                    use_llm=args.llm,
                    scoper=not args.no_scoper,
                    stub_exploration_llm=args.stub_exploration_llm,
                    apply_outcome_quality=not args.no_quality_sanity,
                )
            except Exception as e:
                errors.append(f"{case.bucket}: {e}")
                print(f"  ERROR: {e}")
                if args.fail_fast:
                    return 1
            continue

        synthesis = False
        syn_fail = False
        use_llm = bool(args.llm)
        if case.bucket == "G":
            if not args.llm:
                print("  SKIP G: requires --llm")
                continue
            synthesis = True
            syn_fail = bool(args.synthesis_fail)
            if not syn_fail:
                print("  SKIP G: pass --synthesis-fail to enable synthesis failure injection")
                continue

        try:
            _patch_synthesis_flags(synthesis)
            scoper_on = not args.no_scoper
            if case.bucket == "D" and not args.d_keep_scoper:
                scoper_on = False
            intent_empty = case.bucket == "D"
            stub_fn = None
            if args.stub_exploration_llm:
                stub_fn = make_exploration_stub_fn(discovery, intent_empty=intent_empty)
                if synthesis:
                    stub_fn = _wrap_stub_for_optional_synthesis(stub_fn, synthesis_fail=syn_fail)

            runner = build_exploration_runner(
                repo_root=repo_root,
                use_llm=use_llm,
                synthesis=synthesis,
                synthesis_fail=syn_fail,
                scoper=scoper_on,
                llm_generate_fn_override=stub_fn,
            )
            final, memory = run_instruction(runner, case.instruction)
            cr = CaseResult(
                bucket=case.bucket,
                label=case.label,
                instruction=case.instruction,
                final=final,
                memory=memory,
            )
            log_case_line(instruction=case.instruction, final=final)
            assert_schema_roundtrip(final)
            if memory is not None:
                assert_memory_matches_final(memory, final)
            assert_no_string_relationships(final)
            if case.bucket == "D" and case.d_bogus_symbol:
                _check_D(cr, stub_mode=args.stub_exploration_llm, bogus=case.d_bogus_symbol)
            elif case.check:
                case.check(cr)
            if not args.no_quality_sanity and not case.skip_outcome_quality:
                outcome_quality_sanity_checks(
                    cr.final,
                    case.instruction,
                    targets=case.quality_targets,
                    skip=False,
                )
            if cr.skipped_reason:
                print(f"  note: {cr.skipped_reason}")
        except Exception as e:
            errors.append(f"{case.bucket} {case.label}: {e}")
            print(f"  ERROR: {e}")
            if args.fail_fast:
                return 1

    if errors:
        print("\nFailures:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\nAll selected cases passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
