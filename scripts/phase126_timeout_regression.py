from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path


SCENARIOS = [
    ("easy", "Find AgentLoop and explain it"),
    ("ambiguous", "How does retry work in execution?"),
    ("failure", "Fix bug in non-existent module foobar_xyz/module.py"),
]


def _child_code(instruction: str) -> str:
    return textwrap.dedent(
        f"""
        import json
        from agent.tools.react_tools import register_all_tools
        register_all_tools()
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        out = {{}}
        try:
            result = rt.run({instruction!r}, mode="act")
            out["status"] = result.get("status")
            trace = result.get("trace")
            out["trace"] = trace.model_dump(mode="json") if trace is not None else None
            state = result.get("state")
            ex = getattr(state, "exploration_result", None)
            if ex is not None and hasattr(ex, "model_dump"):
                out["exploration"] = ex.model_dump(mode="json")
        except Exception as e:
            out["status"] = "exception"
            out["error_type"] = type(e).__name__
            out["error"] = str(e)
            try:
                ex = rt.explore({instruction!r})
                out["exploration"] = ex.model_dump(mode="json")
            except Exception as e2:
                out["explore_error_type"] = type(e2).__name__
                out["explore_error"] = str(e2)
        print(json.dumps(out))
        """
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    out_dir = repo_root / "tmp" / "phase126_timeout_regression"
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: list[dict] = []
    for name, instruction in SCENARIOS:
        started = datetime.now(timezone.utc).isoformat()
        proc: subprocess.Popen[str] | None = None
        timed_out = False
        parsed: dict = {}
        stdout_text = ""
        stderr_text = ""
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                [sys.executable, "-u", "-c", _child_code(instruction)],
                cwd=str(repo_root),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            stdout_text, stderr_text = proc.communicate(timeout=30)
            lines = [ln for ln in stdout_text.splitlines() if ln.strip()]
            last = lines[-1] if lines else "{}"
            try:
                parsed = json.loads(last)
            except Exception:
                parsed = {
                    "status": "malformed_output",
                    "error": "Could not parse child JSON output",
                }
        except subprocess.TimeoutExpired as e:
            timed_out = True
            if proc is not None:
                proc.kill()
                so, se = proc.communicate()
            else:
                so, se = "", ""
            p_so = (e.stdout or "") if isinstance(e.stdout, str) else ""
            p_se = (e.stderr or "") if isinstance(e.stderr, str) else ""
            stdout_text = p_so + (so or "")
            stderr_text = p_se + (se or "")
            parsed = {"status": "timeout", "error": "process timeout after 30s"}

        trace = parsed.get("trace") if isinstance(parsed, dict) else None
        trace_steps = trace.get("steps", []) if isinstance(trace, dict) else []
        llm_steps = [s for s in trace_steps if s.get("kind") == "llm"]
        tool_steps = [s for s in trace_steps if s.get("kind") == "tool"]
        read_source_counts: dict[str, int] = {}
        for s in tool_steps:
            rs = (s.get("metadata") or {}).get("read_source")
            if rs:
                read_source_counts[rs] = read_source_counts.get(rs, 0) + 1

        row = {
            "scenario": name,
            "instruction": instruction,
            "started_at": started,
            "timed_out": timed_out,
            "exit_code": None if proc is None else proc.returncode,
            "result": parsed,
            "metrics": {
                "llm_step_count": len(llm_steps),
                "tool_step_count": len(tool_steps),
                "read_source_counts": read_source_counts,
            },
        }
        summary.append(row)

        (out_dir / f"{name}.stdout.log").write_text(stdout_text, encoding="utf-8")
        (out_dir / f"{name}.stderr.log").write_text(stderr_text, encoding="utf-8")
        (out_dir / f"{name}.json").write_text(json.dumps(row, indent=2), encoding="utf-8")

    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(str(summary_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

