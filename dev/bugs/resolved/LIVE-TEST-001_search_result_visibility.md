================================================================================
BUG FIX PLAN — Critical Issue #1: Search Result Visibility
================================================================================

Issue ID: LIVE-TEST-001
Priority: P0 (CRITICAL - BLOCKS PRODUCTION)
Discovered: 2026-03-25 Live Integration Testing
Impact: 10x performance degradation, 100% task failure rate

================================================================================
ROOT CAUSE (CONFIRMED)
================================================================================

File: agent_v2/runtime/tool_mapper.py
Function: summarize_tool_result()
Lines: 83-86

Current code:
```python
if name == "search":
    results = data.get("results") or data.get("candidates") or []
    count = len(results) if isinstance(results, list) else data.get("count", "some")
    return f"Search returned {count} result(s)"  # ← BUG: Only count, no paths!
```

What's happening:
1. Search tool executes successfully ✅
2. Returns dict with results=[{file, snippet, ...}, ...] ✅
3. tool_mapper.summarize_tool_result() extracts count ONLY ❌
4. ExecutionOutput.summary = "Search returned 25 result(s)" ❌
5. ObservationBuilder.build() receives ExecutionResult with summary-only output ❌
6. LLM sees: "Search returned 25 result(s)" — NO file paths ❌
7. LLM hallucinates path ❌
8. open_file fails ❌
9. Retry → same hallucination ❌
10. Replan → same problem ❌

================================================================================
THE FIX
================================================================================

Strategy: Include top N file paths with snippets in ExecutionOutput.summary

Implementation:
```python
if name == "search":
    results = data.get("results") or data.get("candidates") or []
    count = len(results) if isinstance(results, list) else data.get("count", "some")
    
    # Build summary with file paths
    if isinstance(results, list) and results:
        lines = [f"Search returned {count} result(s):"]
        for i, r in enumerate(results[:10], 1):  # Top 10
            if isinstance(r, dict):
                file_path = r.get("file") or r.get("path") or ""
                snippet = (r.get("snippet") or r.get("content") or "")[:100]
                snippet = snippet.replace("\n", " ").strip()
                if file_path:
                    lines.append(f"  {i}. {file_path}")
                    if snippet:
                        lines.append(f"     {snippet}...")
        return "\n".join(lines)
    
    return f"Search returned {count} result(s)"
```

This ensures:
- LLM sees actual file paths ✅
- LLM sees code snippets for context ✅
- Top 10 results shown (balance between context and token usage) ✅
- Numbered list for easy reference ✅

================================================================================
VERIFICATION PLAN
================================================================================

1. Apply fix to agent_v2/runtime/tool_mapper.py
2. Run Phase 3 test again (exploration with search)
3. Verify observation contains file paths
4. Run Phase 4-9 tests
5. Measure performance improvement (expect 5-10x speedup)
6. Verify retry/replan rate drops to <10%

Expected outcomes after fix:
- Phase 3: 5s (down from 24.5s)
- Phase 4: 10s (down from 59.9s)
- Phase 5: 20s (down from 102.9s)
- Phase 8: 20s (down from 98s)
- Phase 9: 30s (down from 195.6s)

Total estimated fix time: 10 minutes
Total estimated verification time: 5 minutes

================================================================================
ADDITIONAL FIXES (LOWER PRIORITY)
================================================================================

Fix #2: PlanValidator task mode validation (P1)
Fix #3: PlanValidator action name validation (P1)
Fix #4: Pass search results to argument generator (P2)
Fix #5: Normalize 'read' → 'open_file' (P2)

See LIVE_INTEGRATION_RCA_2026-03-25.md for details.

================================================================================
