#!/usr/bin/env python3
"""
Replay trace: load trace file, show stages, optionally show a specific stage.

Usage:
  python scripts/replay_trace.py <trace_file_or_id> [--mode interactive|print] [--stage N] [--project-root .]

Examples:
  python scripts/replay_trace.py abc123_1234567890 --mode print
  python scripts/replay_trace.py .agent_memory/traces/abc123_1234567890.json --stage 2
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure AutoStudio root is on path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AGENT_MEMORY_DIR = ".agent_memory"
TRACES_SUBDIR = "traces"


def _resolve_trace_path(arg: str, project_root: Path) -> Path | None:
    """Resolve trace file path from trace_id or file path."""
    p = Path(arg)
    if p.is_file():
        return p
    if p.is_absolute():
        return p if p.exists() else None
    traces_dir = project_root / AGENT_MEMORY_DIR / TRACES_SUBDIR
    trace_file = traces_dir / f"{arg}.json"
    if trace_file.exists():
        return trace_file
    return None


def _print_stage(idx: int, stage: dict, stage_only: bool = False) -> None:
    """Print a single stage."""
    step_id = stage.get("step_id")
    stage_name = stage.get("stage", "?")
    latency_ms = stage.get("latency_ms", 0)
    summary = stage.get("summary") or {}
    if stage_only:
        print(f"\n--- Stage {idx}: {stage_name} ---")
        print(f"  step_id: {step_id}")
        print(f"  latency_ms: {latency_ms}")
        print(f"  summary: {json.dumps(summary, indent=2)}")
    else:
        summary_str = json.dumps(summary)[:80] + ("..." if len(json.dumps(summary)) > 80 else "")
        print(f"  {idx}. {stage_name} (step_id={step_id}, latency_ms={latency_ms:.0f}) {summary_str}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay trace: load and show stages")
    parser.add_argument("trace", help="Trace file path or trace_id (e.g. abc123_1234567890)")
    parser.add_argument("--mode", choices=["interactive", "print"], default="print")
    parser.add_argument("--stage", type=int, help="Show only stage at index N (1-based)")
    parser.add_argument("--project-root", default=".", help="Project root for traces dir")
    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    trace_path = _resolve_trace_path(args.trace, project_root)
    if not trace_path:
        logger.error("Trace not found: %s", args.trace)
        return 1

    try:
        data = json.loads(trace_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to load trace: %s", e)
        return 1

    trace_id = data.get("trace_id", "?")
    query = data.get("query", "(no query)")
    task_id = data.get("task_id", "?")

    print(f"Trace: {trace_id}")
    print(f"Query: {query}")
    print(f"Task: {task_id}")

    stages = data.get("stages", [])
    if not stages:
        print("\nNo stages found (trace may be from older format).")
        events = data.get("events", [])
        if events:
            print(f"\nEvents ({len(events)}):")
            for e in events[:10]:
                print(f"  - {e.get('type', '?')}: {e.get('payload', {})}")
        return 0

    print(f"\nStages ({len(stages)}):")
    for i, s in enumerate(stages, 1):
        stage_name = s.get("stage", "?")
        print(f"  {i} {stage_name}")

    if args.stage is not None:
        idx = args.stage
        if 1 <= idx <= len(stages):
            _print_stage(idx, stages[idx - 1], stage_only=True)
        else:
            logger.error("Stage index %d out of range (1-%d)", idx, len(stages))
            return 1
        return 0

    if args.mode == "print":
        print("\n--- Stage details ---")
        for i, s in enumerate(stages, 1):
            _print_stage(i, s, stage_only=False)
        return 0

    # interactive
    print("\nPress Enter for next (q=quit)")
    for i, s in enumerate(stages, 1):
        _print_stage(i, s, stage_only=True)
        line = input("> ").strip().lower()
        if line == "q":
            break

    return 0


if __name__ == "__main__":
    sys.exit(main())
