"""Terminal adapter using subprocess."""

import subprocess


def run_command(command: str) -> dict:
    """Run shell command; return stdout, stderr, returncode."""
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
    )
    return {
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
        "returncode": result.returncode,
    }
