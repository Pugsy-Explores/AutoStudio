"""Test executor with a fake SEARCH + EDIT plan. SEARCH uses Serena adapter."""

from agent.executor import StepExecutor
from agent.state import AgentState


def main() -> None:
    fake_plan = {
        "steps": [
            {"id": 1, "action": "SEARCH", "description": "Find JWT token generation", "reason": "Locate code to change"},
            {"id": 2, "action": "EDIT", "description": "Change expiration to 24 hours", "reason": "User request"},
        ]
    }
    state = AgentState(
        instruction="Find where JWT tokens are generated and change expiration to 24 hours.",
        current_plan=fake_plan,
        completed_steps=[],
        step_results=[],
        context={},
    )
    executor = StepExecutor()
    results = executor.execute_plan(fake_plan, state)

    print("\n--- Test results ---")
    for r in results:
        print(f"Step {r.step_id} [{r.action}] success={r.success} latency={r.latency_seconds:.3f}s")
        if r.error:
            print(f"  error: {r.error}")
        else:
            out = r.output
            if isinstance(out, dict):
                if "results" in out:
                    print(f"  Serena results: {len(out['results'])}")
                    for i, res in enumerate(out["results"][:5]):
                        snip = str(res.get("snippet", ""))[:80]
                        if len(str(res.get("snippet", ""))) > 80:
                            snip += "..."
                        print(f"    [{i+1}] file={res.get('file')} line={res.get('line')} snippet={snip}")
                elif "error" in out:
                    print(f"  output: {out}")
                else:
                    print(f"  output: {out}")
            else:
                print(f"  output: {str(out)[:200]}...")
    # Context should be populated by SEARCH
    if results and results[0].action == "SEARCH":
        assert "search_results" in state.context
        assert "files" in state.context
        assert "snippets" in state.context
    assert len(results) == 2
    assert results[0].action == "SEARCH" and results[0].success
    assert results[1].action == "EDIT" and results[1].success
    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()
