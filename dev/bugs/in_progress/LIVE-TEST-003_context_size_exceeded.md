================================================================================
BUG FIX — Issue #3: Phase 10 Context Size Exceeded
================================================================================

Issue ID: LIVE-TEST-003
Priority: P1 (HIGH - BLOCKS FULL INTEGRATION)
Discovered: 2026-03-25 Live Integration Testing (Post-Fix Run #2)
Status: IDENTIFIED → FIX READY

================================================================================
ROOT CAUSE
================================================================================

Error:
```
Error code: 400 - request (30266 tokens) exceeds the available 
context size (16896 tokens), try increasing it
```

What happened:
1. Phase 10 runs full integration: explore → plan → execute → retry → replan
2. Each step accumulates observations in prompt history
3. Observations now include detailed search results (10 files with snippets)
4. Multiple open_file results include full file contents
5. Retry/replan cycles add previous plan + failure context
6. Total prompt size: 30,266 tokens vs 16,896 limit

Why this is happening NOW (not in baseline):
- **Baseline**: Search results were truncated to "Search returned N result(s)" 
  → Prompts were much smaller
- **Post-Fix**: Search results include 10 file paths + 100-char snippets each
  → ~200-300 tokens per search result
  → 3-4 searches = ~1000 tokens additional context
  → This is GOOD (LLM needs this info), but requires larger context window

================================================================================
THE FIX (3 OPTIONS)
================================================================================

Option A: Increase LLM Context Size (RECOMMENDED FOR IMMEDIATE UNBLOCK)
------------------------------------------------------------------------

llama.cpp configuration parameter: -c (context size)

Current: 16896 tokens (16K)
Required: 32768 tokens (32K) or 65536 (64K)

Steps:
1. Stop llama.cpp server
2. Restart with: `llama-server -c 32768 -m <model_path> --port 8081`
3. Verify with: `curl http://localhost:8081/v1/models`
4. Re-run Phase 10 test

Pros:
- Simple, immediate fix
- No code changes
- Handles longer tasks

Cons:
- Requires more RAM (~2-4GB additional)
- Slower inference for very long prompts
- Doesn't scale indefinitely

Recommended: Use 32K for now, can increase to 64K if needed.

Option B: Implement Observation Pruning (RECOMMENDED FOR PRODUCTION)
-----------------------------------------------------------------------

Strategy: Truncate observations in prompt while keeping full data in state.

Files to modify:
1. agent_v2/runtime/observation_builder.py
2. agent_v2/runtime/plan_executor.py or step dispatcher

Implementation:
```python
def build_observation(action: str, result: ExecutionResult, max_tokens: int = 500):
    """Build observation with token budget."""
    full_obs = _build_full_observation(action, result)
    
    # Truncate if too long
    if estimate_tokens(full_obs) > max_tokens:
        if action == "open_file":
            # Show first N lines + "... (truncated)"
            content = result.output.data.get("content", "")
            lines = content.split("\n")[:20]  # First 20 lines
            return "\n".join(lines) + f"\n... ({len(lines)} more lines truncated)"
        
        if action == "search":
            # Already showing top 10, could reduce to top 5
            return full_obs[:max_tokens] + "... (truncated)"
    
    return full_obs
```

Configuration:
- Add `MAX_OBSERVATION_TOKENS` to config
- Default: 500 tokens per observation
- For open_file: show first 20-30 lines, truncate rest
- For search: already limited to top 10

Pros:
- Sustainable for very long tasks
- Predictable token usage
- No LLM restart needed

Cons:
- More complex to implement
- Requires careful testing
- May lose important context if truncation is too aggressive

Option C: Observation Windowing (FUTURE)
-----------------------------------------

Strategy: Keep only last N observations in prompt, archive rest in state.

Implementation:
- Add `OBSERVATION_WINDOW_SIZE` config (default: 5)
- Keep last 5 observations in prompt
- Store full history in state.context["observation_archive"]
- On replan, can selectively restore relevant observations

Pros:
- Constant token usage regardless of task length
- Enables very long task support

Cons:
- Most complex to implement
- LLM loses visibility into early task context
- Requires sophisticated context selection logic

================================================================================
RECOMMENDED APPROACH
================================================================================

Phase 1 (Immediate - Today):
1. Increase LLM context to 32K (`llama-server -c 32768 ...`)
2. Re-run Phase 10 test
3. Verify success

Phase 2 (This Week):
1. Implement Option B (observation pruning)
2. Add MAX_OBSERVATION_TOKENS config
3. Test with 16K context (should work with pruning)
4. Re-test full suite

Phase 3 (Future):
1. Monitor token usage in production
2. If tasks regularly exceed 32K, implement Option C (windowing)

================================================================================
TESTING PLAN
================================================================================

After applying fix:

Test 1: Phase 10 only
```bash
python3 tests/test_live_integration_all_phases.py --phase 10 --verbose
```
Expected: ✅ PASS in 90-120s

Test 2: Full suite (Phases 1-12)
```bash
python3 tests/test_live_integration_all_phases.py --verbose
```
Expected: 12/12 PASS in 8-10 minutes

Test 3: Complex task (stress test)
```bash
python3 -m agent_v2 "Implement a new retrieval stage in the pipeline"
```
Expected: Complete without context overflow

================================================================================
IMMEDIATE ACTION ITEMS
================================================================================

1. [ ] Update llama.cpp startup command to use -c 32768
2. [ ] Document new context size requirement in README
3. [ ] Re-run Phase 10 test
4. [ ] Update system requirements docs
5. [ ] Plan observation pruning implementation (Phase 2)

================================================================================
