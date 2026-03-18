import json

from agent.models.model_client import call_reasoning_model


class LLMEvaluator:
    def evaluate(self, instruction: str, plan: dict, step_results: list) -> dict:
        from agent.prompt_system import get_registry

        prompt = get_registry().get_instructions(
            "llm_evaluator",
            variables={
                "instruction": instruction,
                "plan": plan,
                "step_results": self._format_results(step_results),
            },
        )

        try:
            response = call_reasoning_model(
                prompt,
                task_name="llm_evaluator",
                prompt_name="llm_evaluator",
            )
            data = json.loads(response)
            return {
                "is_success": bool(data.get("is_success")),
                "confidence": float(data.get("confidence", 0.0)),
                "reason": data.get("reason", ""),
                "error": "",
            }
        except Exception:
            return {"is_success": None, "confidence": 0.0, "reason": "", "error": "parse_error"}

    def _format_results(self, results):
        return [
            {
                "action": getattr(r, "action", ""),
                "success": getattr(r, "success", False),
                "error": getattr(r, "error", None),
                "files_modified": getattr(r, "files_modified", []),
            }
            for r in results
        ]

