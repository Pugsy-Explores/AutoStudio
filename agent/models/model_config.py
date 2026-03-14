"""Model endpoints and display names. All from models_config.json; env vars override."""

import json
import os
from pathlib import Path

# Path to config file (same directory as this module)
_CONFIG_DIR = Path(__file__).resolve().parent
_CONFIG_FILE = _CONFIG_DIR / "models_config.json"

_DEFAULT_SMALL_NAME = "Qwen 2B"
_DEFAULT_REASONING_NAME = "Qwen 9B"
_DEFAULT_REASONING_V2_NAME = "Qwen 14B"
_DEFAULT_SMALL_ENDPOINT = "http://localhost:8001/v1/chat/completions"
_DEFAULT_REASONING_ENDPOINT = "http://localhost:8002/v1/chat/completions"
_DEFAULT_REASONING_V2_ENDPOINT = "http://localhost:8003/v1/chat/completions"

_DEFAULT_MODELS = {
    "SMALL": {"name": _DEFAULT_SMALL_NAME, "endpoint": _DEFAULT_SMALL_ENDPOINT},
    "REASONING": {"name": _DEFAULT_REASONING_NAME, "endpoint": _DEFAULT_REASONING_ENDPOINT},
    "REASONING_V2": {"name": _DEFAULT_REASONING_V2_NAME, "endpoint": _DEFAULT_REASONING_V2_ENDPOINT},
}

_DEFAULT_TASK_MODELS = {
    "query rewriting": "SMALL",
    "validation": "SMALL",
    "EXPLAIN": "REASONING",
    "routing": "SMALL",
    "planner": "REASONING",
    "context_ranking": "REASONING",
}

_DEFAULT_API_KEY = "none"
_DEFAULT_REQUEST_TIMEOUT = 120
_DEFAULT_TEMPERATURE = 0.0


def _parse_max_tokens(v) -> int | None:
    """Return int max_tokens or None (no limit)."""
    if v is None:
        return None
    if isinstance(v, int) and v >= 0:
        return v
    s = str(v)
    return int(s) if s.isdigit() else None


def _parse_timeout(v) -> int:
    """Return positive int timeout in seconds."""
    if v is None:
        return _DEFAULT_REQUEST_TIMEOUT
    try:
        n = int(v)
        return max(1, n) if n > 0 else _DEFAULT_REQUEST_TIMEOUT
    except (TypeError, ValueError):
        return _DEFAULT_REQUEST_TIMEOUT


def _parse_temperature(v) -> float:
    """Return float temperature in [0, 2]."""
    if v is None:
        return _DEFAULT_TEMPERATURE
    try:
        t = float(v)
        return max(0.0, min(2.0, t))
    except (TypeError, ValueError):
        return _DEFAULT_TEMPERATURE


def _get_model_call(data: dict) -> dict:
    """Merge model_call block with top-level fallbacks for backward compatibility."""
    out = {
        "temperature": _DEFAULT_TEMPERATURE,
        "max_tokens": None,
        "request_timeout_seconds": _DEFAULT_REQUEST_TIMEOUT,
    }
    # Top-level (legacy) first
    if "temperature" in data:
        out["temperature"] = _parse_temperature(data["temperature"])
    if "max_tokens" in data:
        out["max_tokens"] = _parse_max_tokens(data["max_tokens"])
    if "request_timeout_seconds" in data and data["request_timeout_seconds"] is not None:
        out["request_timeout_seconds"] = _parse_timeout(data["request_timeout_seconds"])
    # model_call block overrides
    mc = data.get("model_call")
    if isinstance(mc, dict):
        if "temperature" in mc:
            out["temperature"] = _parse_temperature(mc["temperature"])
        if "max_tokens" in mc:
            out["max_tokens"] = _parse_max_tokens(mc["max_tokens"])
        if "request_timeout_seconds" in mc and mc["request_timeout_seconds"] is not None:
            out["request_timeout_seconds"] = _parse_timeout(mc["request_timeout_seconds"])
    return out


def _load_config() -> dict:
    """Load all model config from models_config.json. Missing keys use defaults."""
    defaults = {
        "small_model_name": _DEFAULT_SMALL_NAME,
        "reasoning_model_name": _DEFAULT_REASONING_NAME,
        "small_model_endpoint": _DEFAULT_SMALL_ENDPOINT,
        "reasoning_model_endpoint": _DEFAULT_REASONING_ENDPOINT,
        "api_key": _DEFAULT_API_KEY,
        "models": dict(_DEFAULT_MODELS),
        "model_call": {
            "temperature": _DEFAULT_TEMPERATURE,
            "max_tokens": None,
            "request_timeout_seconds": _DEFAULT_REQUEST_TIMEOUT,
        },
        "task_models": _DEFAULT_TASK_MODELS.copy(),
        "task_params": {},
    }
    if not _CONFIG_FILE.is_file():
        return defaults
    try:
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for key in ("small_model_name", "reasoning_model_name", "small_model_endpoint", "reasoning_model_endpoint", "api_key"):
            if key in data and data[key] is not None and str(data[key]).strip():
                defaults[key] = str(data[key]).strip()
        if "models" in data and isinstance(data["models"], dict):
            for k, v in data["models"].items():
                if isinstance(v, dict) and v.get("endpoint"):
                    key_upper = str(k).strip().upper()
                    defaults["models"][key_upper] = {
                        "name": str(v.get("name", "")).strip() or defaults["models"].get(key_upper, {}).get("name", ""),
                        "endpoint": str(v.get("endpoint", "")).strip(),
                    }
        defaults["model_call"] = _get_model_call(data)
        if "task_models" in data and isinstance(data["task_models"], dict):
            defaults["task_models"] = {str(k): str(v).strip().upper() for k, v in data["task_models"].items()}
        if "task_params" in data and isinstance(data["task_params"], dict):
            out_params = {}
            for k, v in data["task_params"].items():
                if isinstance(v, dict):
                    out_params[str(k).strip()] = {
                        "temperature": _parse_temperature(v.get("temperature")) if "temperature" in v else defaults["model_call"]["temperature"],
                        "max_tokens": _parse_max_tokens(v.get("max_tokens")) if "max_tokens" in v else defaults["model_call"]["max_tokens"],
                        "request_timeout_seconds": _parse_timeout(v.get("request_timeout_seconds")) if "request_timeout_seconds" in v else defaults["model_call"]["request_timeout_seconds"],
                    }
            defaults["task_params"] = out_params
        else:
            defaults["task_params"] = {}
        return defaults
    except (json.JSONDecodeError, OSError):
        return defaults


_loaded = _load_config()

# Default model_call for when no task is specified
_default_mc = _loaded.get("model_call") or {}
_task_params_loaded = _loaded.get("task_params") or {}


def get_model_call_params(task_name: str | None) -> dict:
    """
    Return model call params (temperature, max_tokens, request_timeout_seconds) for a task.
    Uses task_params[task_name] merged over model_call defaults. If task_name is None or
    not in task_params, returns model_call defaults.
    """
    base = {
        "temperature": _parse_temperature(_default_mc.get("temperature")),
        "max_tokens": _default_mc.get("max_tokens"),
        "request_timeout_seconds": _parse_timeout(_default_mc.get("request_timeout_seconds")),
    }
    if not task_name or task_name not in _task_params_loaded:
        return base.copy()
    over = _task_params_loaded[task_name]
    return {
        "temperature": over.get("temperature", base["temperature"]),
        "max_tokens": over.get("max_tokens") if "max_tokens" in over else base["max_tokens"],
        "request_timeout_seconds": over.get("request_timeout_seconds", base["request_timeout_seconds"]),
    }

# Models registry: model_key -> {name, endpoint}
_MODELS_REGISTRY = _loaded.get("models", _DEFAULT_MODELS)


def get_endpoint_for_model(model_key: str) -> str:
    """Return endpoint URL for model key (SMALL, REASONING, REASONING_V2, etc.)."""
    key = (model_key or "REASONING").upper()
    env_map = {
        "SMALL": "SMALL_MODEL_ENDPOINT",
        "REASONING": "REASONING_MODEL_ENDPOINT",
        "REASONING_V2": "REASONING_V2_MODEL_ENDPOINT",
    }
    env_var = env_map.get(key)
    if env_var and os.environ.get(env_var):
        return os.environ.get(env_var)
    entry = _MODELS_REGISTRY.get(key) or _MODELS_REGISTRY.get("REASONING")
    return (entry or {}).get("endpoint", _DEFAULT_REASONING_ENDPOINT)


def get_model_name(model_key: str) -> str:
    """Return display name for model key."""
    key = (model_key or "REASONING").upper()
    entry = _MODELS_REGISTRY.get(key) or {}
    return entry.get("name", "Unknown")

# Endpoints: from config, overridable by env (backward compat)
SMALL_MODEL_ENDPOINT = os.environ.get("SMALL_MODEL_ENDPOINT", get_endpoint_for_model("SMALL"))
REASONING_MODEL_ENDPOINT = os.environ.get("REASONING_MODEL_ENDPOINT", get_endpoint_for_model("REASONING"))
REASONING_V2_MODEL_ENDPOINT = os.environ.get("REASONING_V2_MODEL_ENDPOINT", get_endpoint_for_model("REASONING_V2"))

# Display names: from config
SMALL_MODEL_NAME = get_model_name("SMALL")
REASONING_MODEL_NAME = get_model_name("REASONING")
REASONING_V2_MODEL_NAME = get_model_name("REASONING_V2")

# API key: from config, overridable by env
MODEL_API_KEY = os.environ.get("MODEL_API_KEY", _loaded["api_key"])

# model_call params (temperature, max_tokens, request_timeout): from config, overridable by env
_mc = _loaded.get("model_call") or {}
_env_t = os.environ.get("MODEL_TEMPERATURE")
MODEL_TEMPERATURE = _parse_temperature(_env_t) if _env_t not in (None, "") else _parse_temperature(_mc.get("temperature"))
_env_m = os.environ.get("MODEL_MAX_TOKENS")
MODEL_MAX_TOKENS = _parse_max_tokens(_env_m) if _env_m not in (None, "") else _mc.get("max_tokens")
_env_to = os.environ.get("MODEL_REQUEST_TIMEOUT")
MODEL_REQUEST_TIMEOUT = _parse_timeout(_env_to) if _env_to not in (None, "") else _parse_timeout(_mc.get("request_timeout_seconds"))

# Task/step -> model name (SMALL | REASONING); each workflow step reads this to choose which model to call
TASK_MODELS = _loaded.get("task_models", _DEFAULT_TASK_MODELS)
