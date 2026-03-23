#!/usr/bin/env python3
"""
Run AutoStudio in ReAct mode (live execution).
Captures full trace with JSON actions and react_history.
Usage:
  python scripts/run_react_live.py "Add a docstring to the main function in agent/__main__.py"
  python scripts/run_react_live.py "Fix the bug in tests/test_react_schema.py"
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from config.config_validator import validate_config
from config.logging_config import configure_logging
from agent.orchestrator.agent_controller import run_controller

validate_config()
configure_logging()


def main():
    if len(sys.argv) > 1:
        instruction = " ".join(sys.argv[1:]).strip()
    else:
        instruction = input("Instruction: ").strip()
    if not instruction:
        print("No instruction provided.", file=sys.stderr)
        sys.exit(1)

    print(f"[react_live] instruction={instruction[:80]}...")
    print("[react_live] Running...")

    result = run_controller(instruction, project_root=str(ROOT))
    state = result["state"]
    loop_output = result.get("loop_output") or {}

    react_history = loop_output.get("react_history") or state.context.get("react_history", [])

    # Build JSON actions trace (thought, action, args per step)
    json_actions = []
    for i, entry in enumerate(react_history):
        json_actions.append({
            "step": i + 1,
            "thought": entry.get("thought", ""),
            "action": entry.get("action", ""),
            "args": entry.get("args", {}),
            "observation_preview": (entry.get("observation", "") or "")[:300] + ("..." if len(entry.get("observation", "") or "") > 300 else ""),
        })

    out_dir = ROOT / "Docs" / "react_runs"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    trace_path = out_dir / f"react_trace_{ts}.json"

    trace_data = {
        "instruction": instruction,
        "timestamp": ts,
        "patches_applied": loop_output.get("patches_applied", 0),
        "files_modified": loop_output.get("files_modified", []),
        "errors_encountered": loop_output.get("errors_encountered", []),
        "tool_calls": loop_output.get("tool_calls", 0),
        "react_history_count": len(react_history),
        "json_actions": json_actions,
        "react_history_full": react_history,
    }

    with open(trace_path, "w", encoding="utf-8") as f:
        json.dump(trace_data, f, indent=2, default=str)

    print(f"\n[react_live] Trace written to {trace_path}")
    print(f"[react_live] Steps: {len(react_history)} | Patches: {loop_output.get('patches_applied', 0)} | Errors: {len(loop_output.get('errors_encountered', []))}")

    print("\n--- JSON Actions ---")
    for a in json_actions:
        print(f"  Step {a['step']}: {a['action']} {a['args']}")

    print("\n--- Step Results ---")
    for r in state.step_results:
        status = "OK" if r.success else "FAIL"
        print(f"  {r.step_id} [{r.action}] {status} {r.latency_seconds:.2f}s")
        if r.error:
            print(f"    error: {r.error[:150]}...")

    return trace_path


if __name__ == "__main__":
    main()
