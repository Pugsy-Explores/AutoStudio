"""Log failures to dev/failure_logs/{prompt_name}/{date}.jsonl."""

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

_LOGS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "dev" / "failure_logs"


@dataclass
class FailureRecord:
    """One failure record."""

    prompt_name: str
    version: str
    model: str
    context: str
    response: str
    error_type: str  # bad_retrieval | invalid_json | wrong_tool | bad_patch
    timestamp: str
    # Phase 14 token budgeting
    prompt_tokens: int = 0
    context_tokens: int = 0
    pruning_triggered: bool = False
    compression_triggered: bool = False


def log_failure(record: FailureRecord) -> Path:
    """Append failure to dev/failure_logs/{prompt_name}/{date}.jsonl. Returns path written."""
    date_str = datetime.now().strftime("%Y-%m-%d")
    dir_path = _LOGS_DIR / record.prompt_name
    dir_path.mkdir(parents=True, exist_ok=True)
    file_path = dir_path / f"{date_str}.jsonl"
    with open(file_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record), default=str) + "\n")
    return file_path
