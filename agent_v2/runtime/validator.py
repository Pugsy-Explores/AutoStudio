"""ReAct step validator wrapper."""
# DO NOT import from agent.* here


class Validator:
    """Thin wrapper around action validation."""

    def __init__(self, validate_fn=None):
        self._validate_fn = validate_fn or self._validate_step

    def validate(self, step):
        return self._validate_fn(step)

    @staticmethod
    def _validate_step(step):
        raise RuntimeError(
            "Validator requires a validate_fn. "
            "Legacy validation wiring must be injected from agent_v2.runtime.bootstrap."
        )
