"""Agent loop and controller configuration."""

import os


MAX_TASK_RUNTIME_SECONDS = int(os.getenv("MAX_TASK_RUNTIME_SECONDS", str(15 * 60)))
MAX_REPLAN_ATTEMPTS = int(os.getenv("MAX_REPLAN_ATTEMPTS", "5"))
MAX_STEP_TIMEOUT_SECONDS = int(os.getenv("MAX_STEP_TIMEOUT_SECONDS", "15"))
MAX_CONTEXT_CHARS = int(os.getenv("MAX_CONTEXT_CHARS", "32000"))  # Hard cap before LLM reasoning call

# Phase 12 workflow safety limits
MAX_FILES_PER_PR = int(os.getenv("MAX_FILES_PER_PR", "10"))
MAX_PATCH_LINES = int(os.getenv("MAX_PATCH_LINES", "500"))
MAX_CI_RUNTIME_SECONDS = int(os.getenv("MAX_CI_RUNTIME_SECONDS", "600"))
