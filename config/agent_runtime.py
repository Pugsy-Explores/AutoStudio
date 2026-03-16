"""
Execution-loop configuration: edit/test/fix attempts, patch limits, same-error guard,
optional sandbox. Used by agent/runtime/execution_loop.py. See Docs/CONFIGURATION.md.
"""

import os


def _bool_env(name: str, default: str) -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes")


MAX_EDIT_ATTEMPTS = int(os.getenv("MAX_EDIT_ATTEMPTS", "3"))
LOCALIZATION_TOP_K = int(os.getenv("LOCALIZATION_TOP_K", "10"))
MAX_STRATEGIES = int(os.getenv("MAX_STRATEGIES", "3"))
TEST_TIMEOUT = int(os.getenv("TEST_TIMEOUT", "120"))
TRAJECTORY_STORE_ENABLED = _bool_env("TRAJECTORY_STORE_ENABLED", "1")
TRAJECTORY_STORE_DIR = os.getenv("TRAJECTORY_STORE_DIR", "data/trajectories")

# Patch limits (Improvement 2)
MAX_PATCH_LINES = int(os.getenv("MAX_PATCH_LINES", "300"))
MAX_PATCH_FILES = int(os.getenv("MAX_PATCH_FILES", "5"))

# Execution loop guard (Improvement 5)
MAX_SAME_ERROR_RETRIES = int(os.getenv("MAX_SAME_ERROR_RETRIES", "2"))

# Sandbox: run patch + tests in a copy of project (no host filesystem modification)
ENABLE_SANDBOX = _bool_env("ENABLE_SANDBOX", "0")

