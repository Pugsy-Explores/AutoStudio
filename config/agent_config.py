"""Agent loop and controller configuration."""

import os


MAX_TASK_RUNTIME_SECONDS = int(os.getenv("MAX_TASK_RUNTIME_SECONDS", str(15 * 60)))
MAX_REPLAN_ATTEMPTS = int(os.getenv("MAX_REPLAN_ATTEMPTS", "5"))
