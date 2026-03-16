#!/usr/bin/env python3
"""
Analyze failure trajectories from execution store (v1 or v2 schema).
Outputs markdown report: top failure patterns, by failure_type, by retry_strategy, recommendations.
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root for imports
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from agent.failure_mining.failure_clusterer import (
    cluster_by_failure_type,
    cluster_by_retry_strategy,
    compute_percentage_stats,
)
from agent.failure_mining.failure_extractor import FailureRecord


def load_v2_records(path: Path) -> list[FailureRecord]:
    """Load v2 JSONL and adapt to FailureRecord-like list."""
    records = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("schema_version") != "v2":
            continue
        success = obj.get("success", False)
        records.append(
            FailureRecord(
                task_id=(obj.get("goal") or "")[:100],
                attempt=0,
                failure_type=obj.get("failure_type") or "unknown",
                failing_step=(obj.get("test_output") or "")[:200],
                retry_strategy=obj.get("retry_strategy") or "none",
                prompt_tokens=0,
                repo_tokens=0,
                trajectory_length=len(obj.get("plan") or []),
                step_type="EDIT",
                status="success" if success else "failure",
            )
        )
    return records


def load_v1_trajectories(trajectory_dir: Path) -> list[dict]:
    """Load v1 meta trajectories (one JSON per task_id)."""
    from agent.failure_mining.trajectory_loader import load_trajectories as load_v1
    return load_v1(str(trajectory_dir)) if trajectory_dir.exists() else []


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze failure trajectories")
    parser.add_argument("--project-root", default=".", help="Project root")
    parser.add_argument("--output", "-o", default=None, help="Output markdown file (default: stdout)")
    parser.add_argument("--trajectory-dir", default="data/trajectories", help="Trajectory directory (v2: trajectories.jsonl inside it)")
    args = parser.parse_args()
    root = Path(args.project_root).resolve()
    traj_dir = root / args.trajectory_dir
    jsonl_path = traj_dir / "trajectories.jsonl"

    records: list[FailureRecord] = []
    if jsonl_path.exists():
        records = load_v2_records(jsonl_path)
    if not records:
        try:
            from agent.failure_mining.failure_extractor import extract_records
            v1_trajectories = []
            for p in traj_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text())
                    if data.get("schema_version") == "v2":
                        continue
                    data["task_id"] = p.stem
                    v1_trajectories.append(data)
                except Exception:
                    continue
            if v1_trajectories:
                records = extract_records(v1_trajectories, str(root))
        except ImportError:
            pass

    if not records:
        out = "# Failure analysis\n\nNo trajectory data found.\n"
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
        else:
            print(out)
        return 0

    failures = [r for r in records if r.status == "failure"]
    total = len(failures)
    if total == 0:
        out = "# Failure analysis\n\nNo failures in trajectory data.\n"
        if args.output:
            Path(args.output).write_text(out, encoding="utf-8")
        else:
            print(out)
        return 0

    by_ft = cluster_by_failure_type(failures)
    by_rs = cluster_by_retry_strategy(failures)
    pct_ft = compute_percentage_stats(by_ft, total)
    pct_rs = compute_percentage_stats(by_rs, total)

    lines = [
        "# Failure analysis report",
        "",
        f"Total failure records: {total}",
        "",
        "## Top failure patterns (by failure_type)",
        "",
    ]
    for ft, pct in sorted(pct_ft.items(), key=lambda x: -x[1])[:15]:
        lines.append(f"- **{ft}**: {pct:.1%} ({len(by_ft.get(ft, []))})")
    lines.extend(["", "## By retry_strategy", ""])
    for rs, pct in sorted(pct_rs.items(), key=lambda x: -x[1])[:10]:
        lines.append(f"- **{rs}**: {pct:.1%} ({len(by_rs.get(rs, []))})")
    lines.extend([
        "",
        "## Recommendations",
        "",
        "- Review high-frequency failure_types first.",
        "- For retrieval_miss / wrong_file_localization, consider broadening retrieval or query rewrite.",
        "- For incorrect_patch / test_failure, consider additional context or smaller patches.",
        "- For timeout, consider reducing scope or increasing timeouts.",
        "",
    ])
    out = "\n".join(lines)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"Wrote {args.output}")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
