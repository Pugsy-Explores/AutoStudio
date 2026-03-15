"""Experience retriever: orchestrates pre-task hint retrieval for planner adaptation."""

from dataclasses import dataclass
import logging
from pathlib import Path

from agent.intelligence.developer_model import load_profile
from agent.intelligence.repo_learning import load_knowledge
from agent.intelligence.solution_memory import load_solution
from agent.intelligence.task_embeddings import search_similar_solutions
from agent.observability.trace_logger import log_event

logger = logging.getLogger(__name__)


@dataclass
class ExperienceHints:
    """Hints from past experience to adapt planning strategy."""

    similar_solutions: list[dict]
    developer_profile: dict
    repo_knowledge: dict
    suggested_files: list[str]

    def to_dict(self) -> dict:
        """Serialize for state.context injection."""
        return {
            "similar_solutions": self.similar_solutions,
            "developer_profile": self.developer_profile,
            "repo_knowledge": self.repo_knowledge,
            "suggested_files": self.suggested_files,
        }


def retrieve(
    goal: str,
    project_root: str | Path | None = None,
    trace_id: str | None = None,
    top_k: int = 3,
) -> ExperienceHints:
    """
    Retrieve experience hints before solving a task.
    Returns ExperienceHints for planner adaptation.
    All decisions logged via log_event (Rule 10).
    """
    root = Path(project_root or ".").resolve()
    tid = trace_id or ""

    similar = search_similar_solutions(goal, str(root), top_k=top_k)
    profile = load_profile(str(root))
    knowledge = load_knowledge(str(root))

    suggested_files: list[str] = []
    for sol in similar:
        task_id = sol.get("task_id")
        if task_id:
            full = load_solution(task_id, str(root))
            if full and full.get("files_modified"):
                suggested_files.extend(full["files_modified"])
    suggested_files = list(dict.fromkeys(suggested_files))[:20]

    log_event(
        tid,
        "experience_retriever",
        {
            "decision": "retrieve",
            "similar_count": len(similar),
            "suggested_files_count": len(suggested_files),
        },
    )

    return ExperienceHints(
        similar_solutions=similar,
        developer_profile=profile,
        repo_knowledge=knowledge,
        suggested_files=suggested_files,
    )
