"""Verify patches before writing them to disk."""

import logging
import tempfile
from pathlib import Path

from repo_index.parser import parse_file

logger = logging.getLogger(__name__)


def validate_patch(file_path: str, new_code: str) -> dict:
    """
    Verify that new_code is valid Python before writing.
    Returns {valid: bool, errors: list[str]}.
    """
    errors: list[str] = []

    # 1. Syntax check via compile
    try:
        compile(new_code, file_path, "exec")
    except SyntaxError as e:
        errors.append(f"Syntax error: {e}")
        return {"valid": False, "errors": errors}

    # 2. AST integrity: re-parse with Tree-sitter
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(new_code)
            temp_path = f.name
        try:
            tree = parse_file(temp_path)
            if tree is None:
                errors.append("AST re-parse failed: tree-sitter returned None")
        finally:
            Path(temp_path).unlink(missing_ok=True)
    except Exception as e:
        errors.append(f"AST re-parse error: {e}")
        return {"valid": False, "errors": errors}

    if tree is None:
        return {"valid": False, "errors": errors}

    # 3. Optional lint (non-blocking)
    lint_errors: list[str] = []
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(new_code)
            temp_path = f.name
        try:
            import subprocess

            result = subprocess.run(
                ["flake8", temp_path, "--max-line-length=120"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0 and result.stdout:
                lint_errors.append(f"flake8: {result.stdout.strip()}")
        except FileNotFoundError:
            pass
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logger.debug("[patch_validator] lint check skipped: %s", e)
        finally:
            Path(temp_path).unlink(missing_ok=True)
    except Exception as e:
        logger.debug("[patch_validator] lint temp file error: %s", e)

    if lint_errors:
        errors.extend(lint_errors)
        # Lint errors are non-blocking by default; don't fail validation
        # Uncomment to make lint block: return {"valid": False, "errors": errors}

    return {"valid": True, "errors": errors}
