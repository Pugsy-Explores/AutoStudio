from agent_v2.runtime.agent_loop import AgentLoop
from agent_v2.state.agent_state import AgentState


class _ActionGenerator:
    def __init__(self, steps):
        self._steps = list(steps)
        self._idx = 0

    def next_action(self, _state):
        if self._idx >= len(self._steps):
            return None
        step = self._steps[self._idx]
        self._idx += 1
        return step


class _Validator:
    def validate(self, _step):
        return True


class _Dispatcher:
    def __init__(self, results):
        self._results = list(results)
        self._idx = 0

    def execute(self, _step, _state):
        result = self._results[self._idx]
        self._idx += 1
        return result


class _ObservationBuilder:
    def build(self, _action, result):
        return f"ok:{getattr(result, 'output', '')}"


def _step(step_id: int, action: str = "SEARCH"):
    return {
        "id": step_id,
        "action": action,
        "_react_thought": "t",
        "_react_action_raw": action.lower(),
        "_react_args": {},
    }


def test_success_path_keeps_retry_state_clean():
    state = AgentState(instruction="x")
    loop = AgentLoop(
        dispatcher=_Dispatcher([{"success": True, "output": "done", "error": None}]),
        validator=_Validator(),
        action_generator=_ActionGenerator([_step(1)]),
        observation_builder=_ObservationBuilder(),
    )

    out = loop.run(state)
    assert out.retry_count == 0
    assert out.metadata.get("retry_count", 0) == 0
    assert out.last_error is None
    assert out.metadata.get("failure_streak", 0) == 0
    assert len(out.history) == 1
    assert out.history[0]["observation"].startswith("ok:")


def test_failure_observation_and_streak_recorded():
    state = AgentState(instruction="x")
    loop = AgentLoop(
        dispatcher=_Dispatcher(
            [
                {"success": False, "output": "", "error": "bad path"},
                {"success": False, "output": "", "error": "bad path"},
                {"success": False, "output": "", "error": "bad path"},
            ]
        ),
        validator=_Validator(),
        action_generator=_ActionGenerator([_step(1)]),
        observation_builder=_ObservationBuilder(),
    )

    out = loop.run(state)
    assert out.retry_count == 2
    assert out.metadata.get("retry_count") == 2
    assert out.last_error == "bad path"
    assert out.metadata.get("failure_streak") == 3
    assert out.history[-1]["observation"] == "ERROR: bad path"


def test_stops_after_retry_limit():
    state = AgentState(instruction="x")
    steps = [_step(1), _step(2), _step(3), _step(4)]
    results = [
        {"success": False, "output": "", "error": "e1"},
        {"success": False, "output": "", "error": "e2"},
        {"success": False, "output": "", "error": "e3"},
        {"success": False, "output": "", "error": "e4"},
    ]
    loop = AgentLoop(
        dispatcher=_Dispatcher(results),
        validator=_Validator(),
        action_generator=_ActionGenerator(steps),
        observation_builder=_ObservationBuilder(),
    )

    out = loop.run(state)
    assert len(out.history) == 3
    assert out.retry_count == 2
    assert out.metadata.get("retry_count") == 2
    assert out.metadata.get("failure_streak") == 3
