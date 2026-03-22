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

# Semantic retries: max per EDIT step before giving up
MAX_SEMANTIC_RETRIES = int(os.getenv("MAX_SEMANTIC_RETRIES", "2"))

# Reason/feedback truncation for failure reporting and trajectory
REASON_TRUNCATE_LEN = int(os.getenv("REASON_TRUNCATE_LEN", "500"))
TRAJECTORY_REASON_MAX = int(os.getenv("TRAJECTORY_REASON_MAX", "300"))

# Retry planner truncation limits
RETRY_QUERY_MAX_LEN = int(os.getenv("RETRY_QUERY_MAX_LEN", "500"))
RETRY_SUGGESTION_MAX_LEN = int(os.getenv("RETRY_SUGGESTION_MAX_LEN", "200"))

# Project root fallback (env only; context/project_root takes precedence at runtime)
SERENA_PROJECT_DIR = os.getenv("SERENA_PROJECT_DIR", "")

# Sandbox: run patch + tests in a copy of project (no host filesystem modification)
ENABLE_SANDBOX = _bool_env("ENABLE_SANDBOX", "0")

