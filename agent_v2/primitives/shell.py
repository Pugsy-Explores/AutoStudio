"""Shell primitive wrapping terminal adapter execution."""

from agent.tools.terminal_adapter import run_command


class Shell:
    """Execution primitive for shell command invocation."""

    def run(self, command: str) -> dict:
        """Run command and normalize to primitive contract."""
        result = run_command(command)
        return {
            "success": result.get("returncode", 1) == 0,
            "output": result.get("stdout", "") or "",
            "error": (result.get("stderr", "") or None) if result.get("returncode", 1) != 0 else None,
            "returncode": result.get("returncode", 1),
            "stdout": result.get("stdout", "") or "",
            "stderr": result.get("stderr", "") or "",
        }
