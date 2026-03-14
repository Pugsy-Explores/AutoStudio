"""Model endpoints and display names. All from models_config.json; env vars override."""

import json
import os
from pathlib import Path

# Path to config file (same directory as this module)
_CONFIG_DIR = Path(__file__).resolve().parent
_CONFIG_FILE = _CONFIG_DIR / "models_config.json"

_DEFAULT_SMALL_NAME = "Qwen 2B"
_DEFAULT_REASONING_NAME = "Qwen 9B"
_DEFAULT_SMALL_ENDPOINT = "http://localhost:8001/v1/chat/completions"
_DEFAULT_REASONING_ENDPOINT = "http://localhost:8002/v1/chat/completions"


_DEFAULT_TASK_MODELS = {
    "query rewriting": "SMALL",
    "validation": "SMALL",
    "EXPLAIN": "REASONING",
}


def _load_config() -> dict:
    """Load all model config from models_config.json. Missing keys use defaults."""
    defaults = {
        "small_model_name": _DEFAULT_SMALL_NAME,
        "reasoning_model_name": _DEFAULT_REASONING_NAME,
        "small_model_endpoint": _DEFAULT_SMALL_ENDPOINT,
        "reasoning_model_endpoint": _DEFAULT_REASONING_ENDPOINT,
        "task_models": _DEFAULT_TASK_MODELS.copy(),
    }
    if not _CONFIG_FILE.is_file():
        return defaults
    try:
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for key in ("small_model_name", "reasoning_model_name", "small_model_endpoint", "reasoning_model_endpoint"):
            if key in data and data[key]:
                defaults[key] = str(data[key]).strip()
        if "task_models" in data and isinstance(data["task_models"], dict):
            defaults["task_models"] = {str(k): str(v).strip().upper() for k, v in data["task_models"].items()}
        return defaults
    except (json.JSONDecodeError, OSError):
        return defaults


_loaded = _load_config()

# Endpoints: from config, overridable by env
SMALL_MODEL_ENDPOINT = os.environ.get("SMALL_MODEL_ENDPOINT", _loaded["small_model_endpoint"])
REASONING_MODEL_ENDPOINT = os.environ.get(
    "REASONING_MODEL_ENDPOINT", _loaded["reasoning_model_endpoint"]
)

# Display names: from config
SMALL_MODEL_NAME = _loaded["small_model_name"]
REASONING_MODEL_NAME = _loaded["reasoning_model_name"]

# Optional API key for endpoints that require it
MODEL_API_KEY = os.environ.get("MODEL_API_KEY", "none")

# Task/step -> model name (SMALL | REASONING); each workflow step reads this to choose which model to call
TASK_MODELS = _loaded.get("task_models", _DEFAULT_TASK_MODELS)
