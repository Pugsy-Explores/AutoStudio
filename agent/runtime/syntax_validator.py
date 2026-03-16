"""
Project-level syntax validation before running tests. Detects language from manifest
and runs the appropriate check (e.g. py_compile for Python, go build for Go).
"""

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Binary extensions to skip for "all .py" style validation
BINARY_EXTENSIONS = {".pyc", ".pyd", ".so", ".dll", ".dylib", ".bin", ".zip", ".egg", ".whl"}


def _is_binary_path(path: Path) -> bool:
    """Return True if path looks like a binary file (by extension)."""
    return path.suffix.lower() in BINARY_EXTENSIONS


def validate_project(
    project_root: str,
    modified_files: list[str] | None = None,
) -> dict:
    """
    Validate project syntax after patch application.
    Returns {valid: bool, error: str}. error is empty when valid is True.
    """
    root = Path(project_root).resolve()
    if not root.exists() or not root.is_dir():
        return {"valid": False, "error": "project_root does not exist or is not a directory"}

    # Manifest-based language detection
    if (root / "pyproject.toml").exists() or (root / "setup.py").exists() or list(root.rglob("*.py")):
        return _validate_python(root, modified_files)
    if (root / "package.json").exists():
        return _validate_node(root)
    if (root / "go.mod").exists():
        return _validate_go(root)
    if (root / "Cargo.toml").exists():
        return _validate_cargo(root)

    # Default: try Python
    return _validate_python(root, modified_files)


def _validate_python(root: Path, modified_files: list[str] | None) -> dict:
    """Run py_compile on modified files or compileall on project."""
    if modified_files:
        paths = []
        for f in modified_files:
            p = (root / f).resolve() if not Path(f).is_absolute() else Path(f)
            if p.suffix.lower() == ".py" and p.exists() and not _is_binary_path(p):
                try:
                    p.relative_to(root)
                    paths.append(str(p))
                except ValueError:
                    pass
        if not paths:
            return {"valid": True, "error": ""}
        try:
            result = subprocess.run(
                [sys.executable, "-m", "py_compile"] + paths,
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                msg = (result.stderr or result.stdout or "py_compile failed").strip()[:500]
                return {"valid": False, "error": msg}
        except subprocess.TimeoutExpired:
            return {"valid": False, "error": "syntax check timed out"}
        except Exception as e:
            return {"valid": False, "error": str(e)[:500]}
    else:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "compileall", "-q", "."],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                msg = (result.stderr or result.stdout or "compileall failed").strip()[:500]
                return {"valid": False, "error": msg}
        except subprocess.TimeoutExpired:
            return {"valid": False, "error": "syntax check timed out"}
        except Exception as e:
            return {"valid": False, "error": str(e)[:500]}
    return {"valid": True, "error": ""}


def _validate_node(root: Path) -> dict:
    """Run npm run build if present, else skip (no generic node --check without entry file)."""
    import json
    pkg = root / "package.json"
    if not pkg.exists():
        return {"valid": True, "error": ""}
    try:
        data = json.loads(pkg.read_text())
        scripts = data.get("scripts") or {}
        if "build" in scripts:
            result = subprocess.run(
                ["npm", "run", "build"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                return {"valid": True, "error": ""}
            return {"valid": False, "error": (result.stderr or result.stdout or "build failed").strip()[:500]}
        return {"valid": True, "error": ""}
    except subprocess.TimeoutExpired:
        return {"valid": False, "error": "node build timed out"}
    except FileNotFoundError:
        return {"valid": True, "error": ""}
    except Exception as e:
        return {"valid": False, "error": str(e)[:500]}


def _validate_go(root: Path) -> dict:
    """Run go build."""
    try:
        result = subprocess.run(
            ["go", "build", "./..."],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            return {"valid": True, "error": ""}
        return {"valid": False, "error": (result.stderr or result.stdout or "go build failed").strip()[:500]}
    except subprocess.TimeoutExpired:
        return {"valid": False, "error": "go build timed out"}
    except FileNotFoundError:
        return {"valid": True, "error": ""}
    except Exception as e:
        return {"valid": False, "error": str(e)[:500]}


def _validate_cargo(root: Path) -> dict:
    """Run cargo check."""
    try:
        result = subprocess.run(
            ["cargo", "check"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode == 0:
            return {"valid": True, "error": ""}
        return {"valid": False, "error": (result.stderr or result.stdout or "cargo check failed").strip()[:500]}
    except subprocess.TimeoutExpired:
        return {"valid": False, "error": "cargo check timed out"}
    except FileNotFoundError:
        return {"valid": True, "error": ""}
    except Exception as e:
        return {"valid": False, "error": str(e)[:500]}
