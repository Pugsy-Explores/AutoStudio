"""ReAct tool schema: validation via central registry."""

from agent.tools.react_registry import get_all_tools, get_tool_by_name, initialize_tool_registry

# Explicit initialization (no lazy init).
initialize_tool_registry()


def _get_allowed_actions() -> dict[str, list[str]]:
    """Build ALLOWED_ACTIONS from registry. Lazy to handle init order."""
    return {t.name: t.required_args for t in get_all_tools()}


# Backward compatibility: ALLOWED_ACTIONS as property-like access
class _AllowedActionsProxy:
    """Proxy for ALLOWED_ACTIONS that delegates to registry."""

    def __getitem__(self, key: str) -> list[str]:
        return _get_allowed_actions()[key]

    def __contains__(self, key: str) -> bool:
        return key in _get_allowed_actions()

    def keys(self):
        return _get_allowed_actions().keys()

    def __iter__(self):
        return iter(_get_allowed_actions())

    def __len__(self):
        return len(_get_allowed_actions())

    def get(self, key: str, default=None):
        return _get_allowed_actions().get(key, default)


ALLOWED_ACTIONS = _AllowedActionsProxy()


def validate_action(action: str, args: dict | None) -> tuple[bool, str | None]:
    """Validate action and args against canonical schema. Returns (valid, error_message)."""
    tool = get_tool_by_name(action)
    if tool is None:
        return False, f"Invalid action: {action}"

    args = args if isinstance(args, dict) else {}
    required = tool.required_args

    for key in required:
        if key not in args:
            return False, f"Missing required field '{key}' for {action}"
        value = args.get(key)
        if value is None:
            return False, f"Missing required field '{key}' for {action}"
        if isinstance(value, str) and not value.strip():
            return False, f"Missing required field '{key}' for {action}"

    for key in args.keys():
        if key not in required:
            return False, f"Unexpected field '{key}' for {action}"

    return True, None
