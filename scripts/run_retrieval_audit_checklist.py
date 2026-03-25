#!/usr/bin/env python3
"""Run a repeatable 4-prompt retrieval audit and emit JSON output.

This script is intentionally deterministic and file-system based so it can run
in CI/local without network/model dependencies.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "tmp" / "retrieval_audit_report.json"


@dataclass
class Match:
    path: str
    line: int
    text: str


def iter_py_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("artifacts/") or rel.startswith(".venv/") or "/fixtures/" in rel:
            continue
        yield path


def scan_patterns(root: Path, patterns: list[re.Pattern[str]]) -> list[Match]:
    out: list[Match] = []
    for py_file in iter_py_files(root):
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for idx, line in enumerate(text.splitlines(), start=1):
            for p in patterns:
                if p.search(line):
                    out.append(
                        Match(
                            path=py_file.relative_to(root).as_posix(),
                            line=idx,
                            text=line.strip(),
                        )
                    )
                    break
    return out


def prompt1_retry_failure(root: Path) -> dict:
    pats = [
        re.compile(r"\bretry\b", re.IGNORECASE),
        re.compile(r"\bfailure\b", re.IGNORECASE),
        re.compile(r"\berror\b", re.IGNORECASE),
        re.compile(r"\bfailure_streak\b"),
        re.compile(r"\bretry_count\b"),
    ]
    matches = scan_patterns(root, pats)
    top = matches[:80]
    return {
        "prompt": "Find retry/failure handling across the system",
        "match_count": len(matches),
        "matches": [m.__dict__ for m in top],
        "summary": {
            "has_agent_v2_retry_loop": any("agent_v2/runtime/agent_loop.py" in m.path for m in matches),
            "has_dispatch_contract_normalization": any("agent_v2/runtime/dispatcher.py" in m.path for m in matches),
            "has_legacy_retry_logic": any("agent/execution/step_dispatcher.py" in m.path for m in matches),
        },
    }


def prompt2_arch_trace(root: Path) -> dict:
    required = [
        ("agent_v2/__main__.py", [r"create_runtime\(", r"parse_mode\(", r"runtime\.run\("]),
        ("agent_v2/runtime/runtime.py", [r"class AgentRuntime", r"ModeManager", r"AgentLoop"]),
        ("agent_v2/runtime/mode_manager.py", [r"def _run_act", r"def _run_plan", r"def _run_deep_plan"]),
        ("agent_v2/runtime/agent_loop.py", [r"class AgentLoop", r"dispatcher\.execute"]),
        ("agent_v2/runtime/dispatcher.py", [r"ToolResult", r"normalize_result"]),
        ("agent_v2/runtime/bootstrap.py", [r"def create_runtime", r"V2PlannerAdapter"]),
    ]
    items: list[dict] = []
    all_ok = True
    for rel, symbol_patterns in required:
        file_path = root / rel
        ok = file_path.exists()
        found: list[str] = []
        if ok:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            for sp in symbol_patterns:
                if re.search(sp, text):
                    found.append(sp)
        file_ok = ok and len(found) == len(symbol_patterns)
        all_ok = all_ok and file_ok
        items.append({"file": rel, "exists": ok, "required_patterns": symbol_patterns, "found_patterns": found, "ok": file_ok})
    return {
        "prompt": "Trace runtime execution end-to-end",
        "ok": all_ok,
        "checks": items,
    }


def prompt3_edit_flow(root: Path) -> dict:
    required_links = [
        ("agent/execution/step_dispatcher.py", r"_edit_react"),
        ("agent/execution/step_dispatcher.py", r"_generate_patch_once"),
        ("agent_v2/primitives/editor.py", r"def apply_patch"),
        ("editing/patch_generator.py", r"def to_structured_patches"),
        ("editing/patch_executor.py", r"def execute_patch"),
    ]
    checks: list[dict] = []
    all_ok = True
    for rel, pat in required_links:
        p = root / rel
        exists = p.exists()
        found = False
        if exists:
            txt = p.read_text(encoding="utf-8", errors="replace")
            found = re.search(pat, txt) is not None
        ok = exists and found
        all_ok = all_ok and ok
        checks.append({"file": rel, "pattern": pat, "ok": ok})
    return {
        "prompt": "Find editing components and full flow",
        "ok": all_ok,
        "checks": checks,
    }


def prompt4_legacy_refs(root: Path) -> dict:
    pats = [
        re.compile(r"execution_loop"),
        re.compile(r"run_controller"),
        re.compile(r"run_hierarchical"),
        re.compile(r"deterministic_runner"),
        re.compile(r"agent\.orchestrator"),
        re.compile(r"deprecated", re.IGNORECASE),
    ]
    matches = scan_patterns(root, pats)
    safe_prefixes = ("tests/", "scripts/", "docs/", "Docs/")
    safe = [m for m in matches if m.path.startswith(safe_prefixes)]
    active = [m for m in matches if not m.path.startswith(safe_prefixes)]
    return {
        "prompt": "Search for remaining deprecated/legacy references",
        "match_count": len(matches),
        "safe_reference_count": len(safe),
        "active_reference_count": len(active),
        "active_references": [m.__dict__ for m in active[:120]],
        "status": "warn" if active else "ok",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 4-prompt retrieval audit checklist.")
    parser.add_argument("--repo-root", default=str(REPO_ROOT), help="Repository root path.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON file path.")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    out_path = Path(args.output).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "repo_root": root.as_posix(),
        "checks": {
            "prompt_1_retry_failure": prompt1_retry_failure(root),
            "prompt_2_architecture_trace": prompt2_arch_trace(root),
            "prompt_3_edit_flow": prompt3_edit_flow(root),
            "prompt_4_legacy_refs": prompt4_legacy_refs(root),
        },
    }

    # Small top-level verdict.
    report["ok"] = (
        report["checks"]["prompt_2_architecture_trace"]["ok"]
        and report["checks"]["prompt_3_edit_flow"]["ok"]
        and report["checks"]["prompt_4_legacy_refs"]["status"] in {"ok", "warn"}
    )

    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(
        {
            "ok": report["ok"],
            "output": out_path.as_posix(),
            "prompt_2_ok": report["checks"]["prompt_2_architecture_trace"]["ok"],
            "prompt_3_ok": report["checks"]["prompt_3_edit_flow"]["ok"],
            "legacy_status": report["checks"]["prompt_4_legacy_refs"]["status"],
            "legacy_active_refs": report["checks"]["prompt_4_legacy_refs"]["active_reference_count"],
        },
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
