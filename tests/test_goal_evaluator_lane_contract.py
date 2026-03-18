from agent.memory.state import AgentState
from agent.orchestrator.goal_evaluator import GoalEvaluator


def test_goal_evaluator_rejects_success_when_lane_violation_present():
    evaluator = GoalEvaluator()
    state = AgentState(
        instruction="explain something",
        current_plan={"plan_id": "p", "steps": []},
        context={"lane_violations": [{"error": "lane_violation"}]},
        step_results=[
            # Even with a successful EXPLAIN, lane violations must prevent goal_met.
            type("SR", (), {"action": "EXPLAIN", "success": True, "patch_size": 0, "files_modified": []})()
        ],
    )
    assert evaluator.evaluate("explain something", state) is False
