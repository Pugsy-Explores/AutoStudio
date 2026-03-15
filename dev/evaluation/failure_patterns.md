# Failure Patterns

<!-- Document recurring failure patterns and root causes to prevent repeating mistakes -->

## Phase 4 Mining

Run `python scripts/run_principal_engineer_suite.py --failure-mining --mining-reps 10` to aggregate failures.

| Pattern | Count | Cause |
|---------|-------|-------|
| (run --failure-mining to populate) | | |

## Known Patterns (reference)

| Pattern | Cause |
|---------|-------|
| Retrieval Empty | query rewrite incorrect |
| Planner Hallucinated Tool | missing step constraint |
