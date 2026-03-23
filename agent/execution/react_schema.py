"""ReAct tool schema: single source of truth and strict validation."""

ALLOWED_ACTIONS = {
    "search": ["query"],
    "open_file": ["path"],
    "edit": ["instruction", "path"],
    "run_tests": [],
    "finish": [],
}


def validate_action(action: str, args: dict | None) -> tuple[bool, str | None]:
    """Validate action and args against canonical schema. Returns (valid, error_message)."""
    if action not in ALLOWED_ACTIONS:
        return False, f"Invalid action: {action}"

    args = args if isinstance(args, dict) else {}

    required = ALLOWED_ACTIONS[action]

    for key in required:
        if key not in args or not args[key]:
            return False, f"Missing required field '{key}' for {action}"

    for key in args.keys():
        if key not in required:
            return False, f"Unexpected field '{key}' for {action}"

    return True, None
