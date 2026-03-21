#!/usr/bin/env python3
"""
SEARCH Quality Audit — aggregate results and run offline batch.

Usage:
  # 1. Aggregate from trace files (after running with ENABLE_SEARCH_QUALITY_AUDIT=1)
  ENABLE_SEARCH_QUALITY_AUDIT=1 python3 -m tests.agent_eval.runner --suite search_stack --output artifacts/audit_run
  python3 scripts/run_search_quality_audit.py --trace-dir .agent_memory/traces

  # 2. Run evaluator on JSONL input (instruction, search_query, retrieval_summary per line)
  python3 scripts/run_search_quality_audit.py --batch-input audit_batch.jsonl --output artifacts/audit_report.json

  # 3. Run on a few scenario tuples (for quick spot-checks)
  python3 scripts/run_search_quality_audit.py --scenarios scenarios.json

Output: % weak/bad, most common red_flags, 2-3 sample bad examples, effective_search distribution.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def _load_trace_audits(trace_dir: Path) -> list[dict]:
    """Load all search_quality_audit events from trace JSON files."""
    records: list[dict] = []
    if not trace_dir.is_dir():
        logger.warning("Trace dir not found: %s", trace_dir)
        return records
    for p in sorted(trace_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("Skip %s: %s", p.name, e)
            continue
        events = data.get("events") or []
        task_id = data.get("task_id", "?")
        query = data.get("query", "")
        for ev in events:
            if isinstance(ev, dict) and ev.get("type") == "search_quality_audit":
                payload = ev.get("payload") or {}
                payload["_trace_file"] = p.name
                payload["_task_id"] = task_id
                payload["_query"] = query
                records.append(payload)
    return records


def _run_batch_from_jsonl(path: Path) -> list[dict]:
    """Run evaluator on each line of JSONL. Each line: {instruction, search_query, retrieval_summary}."""
    from agent.eval.search_quality_audit import evaluate_search_quality

    records: list[dict] = []
    for i, line in enumerate(path.read_text(encoding="utf-8").strip().splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            logger.warning("Line %d invalid JSON: %s", i, e)
            continue
        inst = row.get("instruction", "")
        query = row.get("search_query", row.get("search_description", ""))
        summary = row.get("retrieval_summary", "")
        prev = row.get("previous_searches", "(none)")
        r = evaluate_search_quality(inst, query, summary, prev)
        r["_line"] = i
        records.append(r)
    return records


def _run_scenarios(path: Path) -> list[dict]:
    """Run evaluator on scenario tuples from JSON file."""
    from agent.eval.search_quality_audit import evaluate_search_quality

    data = json.loads(path.read_text(encoding="utf-8"))
    scenarios = data.get("scenarios", data) if isinstance(data, dict) else []
    if not isinstance(scenarios, list):
        scenarios = [data]
    records: list[dict] = []
    for i, s in enumerate(scenarios):
        if isinstance(s, dict):
            inst = s.get("instruction", "")
            query = s.get("search_query", s.get("search_description", ""))
            summary = s.get("retrieval_summary", "")
            prev = s.get("previous_searches", "(none)")
        else:
            inst, query, summary = (str(x) for x in (s[:3] if len(s) >= 3 else (*s, "")))
            prev = "(none)"
        r = evaluate_search_quality(inst, query, summary, prev)
        r["_scenario_idx"] = i
        records.append(r)
    return records


def _aggregate_and_report(records: list[dict], output_path: Path | None) -> dict:
    """Aggregate and print report. Returns aggregate dict."""
    from agent.eval.search_quality_audit import aggregate_audit_results

    agg = aggregate_audit_results(records)
    total = agg["total_searches"]
    weak_bad_rate = agg["bad_or_weak_rate"]
    eff_avg = agg["effective_search_avg"]
    red_counts = agg["red_flag_counts"]
    sample_bad = agg["sample_bad"]

    print("\n" + "=" * 60)
    print("SEARCH Quality Audit Report")
    print("=" * 60)
    print(f"Total SEARCH steps: {total}")
    print(f"Bad/weak rate: {weak_bad_rate:.1%}  (threshold: >20% = SEARCH bottleneck)")
    print(f"Effective search avg (0-9): {eff_avg:.2f}  (7-9 strong, 4-6 usable, 0-3 broken)")
    print("\nRed flag counts:")
    for flag, cnt in sorted(red_counts.items(), key=lambda x: -x[1]):
        print(f"  {flag}: {cnt}")
    print("\nSample bad/weak (up to 3):")
    for i, s in enumerate(sample_bad, 1):
        print(f"  {i}. verdict={s.get('verdict')} effective_search={s.get('effective_search')}")
        print(f"     explanation: {s.get('explanation', '')[:120]}...")
    print("=" * 60 + "\n")

    report = {
        "total_searches": total,
        "bad_or_weak_rate": weak_bad_rate,
        "effective_search_avg": eff_avg,
        "red_flag_counts": red_counts,
        "sample_bad_count": len(sample_bad),
        "sample_bad": sample_bad,
    }
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"Report written to {output_path}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="SEARCH Quality Audit — aggregate or run batch")
    parser.add_argument("--trace-dir", help="Directory of trace JSON files (from run with ENABLE_SEARCH_QUALITY_AUDIT=1)")
    parser.add_argument("--batch-input", help="JSONL file: one {instruction, search_query, retrieval_summary} per line")
    parser.add_argument("--scenarios", help="JSON file with scenarios array")
    parser.add_argument("--output", "-o", help="Write aggregate report JSON here")
    parser.add_argument("--project-root", default=".", help="Project root (for trace-dir resolution)")
    args = parser.parse_args()

    records: list[dict] = []

    if args.trace_dir:
        root = Path(args.project_root).resolve()
        trace_dir = root / args.trace_dir if not Path(args.trace_dir).is_absolute() else Path(args.trace_dir)
        records = _load_trace_audits(trace_dir)
        logger.info("Loaded %d audit events from %s", len(records), trace_dir)

    if args.batch_input:
        inp = Path(args.batch_input)
        if not inp.is_file():
            logger.error("Batch input not found: %s", inp)
            return 1
        records = _run_batch_from_jsonl(inp)
        logger.info("Ran evaluator on %d rows from %s", len(records), inp)

    if args.scenarios:
        p = Path(args.scenarios)
        if not p.is_file():
            logger.error("Scenarios file not found: %s", p)
            return 1
        records = _run_scenarios(p)
        logger.info("Ran evaluator on %d scenarios from %s", len(records), p)

    if not records:
        logger.warning("No records. Use --trace-dir, --batch-input, or --scenarios.")
        return 1

    out_path = Path(args.output) if args.output else None
    _aggregate_and_report(records, out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
