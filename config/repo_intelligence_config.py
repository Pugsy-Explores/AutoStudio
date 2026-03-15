"""Repository intelligence layer configuration (Phase 10)."""

import os

MAX_REPO_SCAN_FILES = int(os.getenv("MAX_REPO_SCAN_FILES", "200"))
MAX_ARCHITECTURE_NODES = int(os.getenv("MAX_ARCHITECTURE_NODES", "500"))
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "8192"))
MAX_IMPACT_DEPTH = int(os.getenv("MAX_IMPACT_DEPTH", "3"))
