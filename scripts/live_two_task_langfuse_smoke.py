#!/usr/bin/env python3
"""
Run two simple act-mode tasks under a hard timeout; print JSON summary for RCA.
Loads repo .env via agent_v2.observability.langfuse_client (dotenv).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


def _child_code(instruction: str) -> str:
    return textwrap.dedent(
        f"""
        import json
        import os
        from agent.tools.react_tools import register_all_tools
        register_all_tools()
        from agent_v2.runtime.bootstrap import create_runtime

        _path = os.environ["LIVE_TASK_RESULT_JSON"]
        rt = create_runtime()
        out = {{}}
        try:
            result = rt.run({instruction!r}, mode="act")
            out["status"] = result.get("status")
            state = result.get("state")
            lf = None
            if state is not None and hasattr(state, "metadata"):
                lf = state.metadata.get("langfuse_trace")
            if lf is not None:
                out["langfuse_trace_type"] = type(lf).__name__
                out["langfuse_trace_id"] = getattr(lf, "trace_id", None)
            else:
                out["langfuse_trace_type"] = None
                out["langfuse_trace_id"] = None
            trace = result.get("trace")
            if trace is not None:
                steps = getattr(trace, "steps", None) or []
                out["llm_step_count"] = sum(
                    1 for s in steps if getattr(s, "kind", None) == "llm"
                )
                out["tool_step_count"] = sum(
                    1 for s in steps if getattr(s, "kind", None) == "tool"
                )
            ex = getattr(state, "exploration_result", None)
            if ex is not None and hasattr(ex, "model_dump"):
                out["exploration"] = ex.model_dump(mode="json")
        except Exception as e:
            out["status"] = "exception"
            out["error_type"] = type(e).__name__
            out["error"] = str(e)
        with open(_path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print(_path)
        """
    )


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    timeout_s = int(os.environ.get("LIVE_TASK_TIMEOUT_SEC", "90"))
    tasks = [
        ("t1_agentloop", "Find AgentLoop and explain it in one sentence."),
        ("t2_modemanager", "Where is ModeManager class defined? Reply with file path only."),
    ]
    out_dir = repo / "tmp" / "live_langfuse_smoke"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = []
    for name, instruction in tasks:
        started = datetime.now(timezone.utc).isoformat()
        result_json = out_dir / f"{name}.result.json"
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["LIVE_TASK_RESULT_JSON"] = str(result_json)
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", _child_code(instruction)],
            cwd=str(repo),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        timed_out = False
        try:
            so, se = proc.communicate(timeout=timeout_s)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            proc.kill()
            so, se = proc.communicate()
            rc = proc.returncode

        parsed: dict = {}
        if result_json.is_file():
            try:
                parsed = json.loads(result_json.read_text(encoding="utf-8"))
            except Exception as e:
                parsed = {"parse_error": True, "read_error": str(e)}
        else:
            parsed = {"parse_error": True, "missing_result_file": str(result_json)}

        row = {
            "name": name,
            "instruction": instruction,
            "started_at": started,
            "timeout_sec": timeout_s,
            "timed_out": timed_out,
            "exit_code": rc,
            "stderr_head": (se or "")[:2000],
            "result": parsed,
        }
        summary.append(row)
        (out_dir / f"{name}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")
        (out_dir / f"{name}.stdout.log").write_text(so or "", encoding="utf-8")
        (out_dir / f"{name}.stderr.log").write_text(se or "", encoding="utf-8")

    out_path = out_dir / "summary.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
