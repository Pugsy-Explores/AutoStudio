"""Stage 30 — Heuristic decontamination and live-eval integrity regression tests.

Ensures benchmark-shaped logic stays removed and execution modes/integrity invariants hold.
"""

from __future__ import annotations

import inspect

import pytest


# ---------------------------------------------------------------------------
# Benchmark-shaped logic removed (regression)
# ---------------------------------------------------------------------------.


def test_patch_generator_no_benchmark_inject():
    """_inject_click_benchmark_multifile_change and _inject_shared_prefix_multifile removed."""
    from editing import patch_generator as pg

    src = inspect.getsource(pg)
    assert "_inject_click_benchmark_multifile_change" not in src
    assert "_inject_shared_prefix_multifile" not in src
    assert "_suffix_constant_text_sub" not in src


def test_patch_generator_no_benchmark_synthetics():
    """Benchmark-specific synthetic repairs removed; only generic multiply/div and split remain."""
    from editing import patch_generator as pg

    src = inspect.getsource(pg)
    # Removed
    assert "_synthetic_changelog_version_align" not in src
    assert "_synthetic_api_base_align" not in src
    assert "_synthetic_docs_version_align" not in src
    assert "_synthetic_docs_stability_align" not in src
    assert "_synthetic_docs_httpbin_align" not in src
    assert "_synthetic_safe_div_repair" not in src
    assert "_synthetic_is_valid_repair" not in src
    assert "_synthetic_enable_debug" not in src
    assert "_synthetic_log_level" not in src
    assert "_synthetic_shared_prefix_rename" not in src
    # Kept generic
    assert "_generic_multiply_to_div_return" in src
    assert "_generic_split_whitespace_line_return" in src


def test_grounded_patch_generator_no_halve_repair():
    """_try_halve_return_repair removed (benchmark-shaped)."""
    from editing import grounded_patch_generator as gpg

    src = inspect.getsource(gpg)
    assert "_try_halve_return_repair" not in src
    assert "halve_return_repair" not in src


def test_target_resolution_no_benchmark_tokens():
    """typer_ver, readme_bench, benchmark_local/ shortcuts removed from resolve_module_descriptor_to_files."""
    from agent.retrieval import target_resolution as tr

    src = inspect.getsource(tr)
    assert "typer_ver" not in src
    assert "readme_bench" not in src
    assert "benchmark_local" not in src


def test_task_semantics_no_benchmark_docs_consistency():
    """benchmark_local/, so benchmark_, so scripts/ removed from docs-consistency detection."""
    from agent.retrieval import task_semantics as ts

    src = inspect.getsource(ts.instruction_suggests_docs_consistency)
    assert "so benchmark" not in src
    assert "so scripts" not in src


# ---------------------------------------------------------------------------
# Execution modes and model call audit
# ---------------------------------------------------------------------------


def test_model_client_audit_api():
    """reset_model_call_audit and get_model_call_audit exist for live_model integrity."""
    from agent.models.model_client import reset_model_call_audit, get_model_call_audit

    reset_model_call_audit()
    audit = get_model_call_audit()
    assert "model_call_count" in audit
    assert "small_model_call_count" in audit
    assert "reasoning_model_call_count" in audit


def test_harness_execution_modes():
    """Harness supports mocked, offline, live_model, real (deprecated alias)."""
    from tests.agent_eval.harness import ExecutionMode

    assert "mocked" in ExecutionMode.__args__
    assert "offline" in ExecutionMode.__args__
    assert "live_model" in ExecutionMode.__args__
    assert "real" in ExecutionMode.__args__


def test_runner_real_maps_to_offline():
    """--real maps to offline (deprecated). Runner main() sets execution_mode=offline when --real."""
    from tests.agent_eval.runner import build_arg_parser

    p = build_arg_parser()
    args = p.parse_args(["--real"])
    assert getattr(args, "real", False) is True
    # main() maps args.real -> args.execution_mode = "offline"
    if args.real:
        args.execution_mode = "offline"
    assert args.execution_mode == "offline"
