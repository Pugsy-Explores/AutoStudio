"""Agent loop and controller configuration."""

import os


MAX_TASK_RUNTIME_SECONDS = int(os.getenv("MAX_TASK_RUNTIME_SECONDS", str(15 * 60)))
MAX_REPLAN_ATTEMPTS = int(os.getenv("MAX_REPLAN_ATTEMPTS", "5"))
MAX_STEP_TIMEOUT_SECONDS = int(os.getenv("MAX_STEP_TIMEOUT_SECONDS", "15"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "32000"))  # Hard cap before LLM reasoning call
