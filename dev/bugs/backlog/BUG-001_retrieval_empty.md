# Bug ID
BUG-001

# Title
Retrieval pipeline returns empty context for valid symbol

# Area
retrieval

# Severity
high

# Description
Searching for StepExecutor sometimes returns zero results.

# Steps to Reproduce
1. Run agent explain StepExecutor
2. Retrieval returns empty

# Expected Behavior
Symbol graph expansion should return StepExecutor methods.

# Actual Behavior
ranked_context = []

# Logs / Trace
trace_001.json

# Root Cause
(added after investigation)

# Fix
(added after fix)

# Status
open
