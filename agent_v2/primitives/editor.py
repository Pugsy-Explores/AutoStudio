"""Editor primitive wrapping filesystem and patch execution."""

from agent.tools.filesystem_adapter import read_file, write_file
from editing.patch_executor import execute_patch


class Editor:
    """Editing primitive for file and patch operations."""

    def read(self, path: str) -> str:
        return read_file(path)

    def write(self, path: str, content: str) -> dict:
        write_file(path, content)
        return {"success": True, "path": path}

    def apply_patch(self, patch, project_root: str | None = None) -> dict:
        return execute_patch(patch, project_root)
