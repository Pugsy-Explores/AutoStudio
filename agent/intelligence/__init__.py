"""Intelligence layer: solution memory, task embeddings, experience retrieval, developer model, repo learning."""

from agent.intelligence.developer_model import load_profile, save_profile, update_from_solution
from agent.intelligence.experience_retriever import ExperienceHints, retrieve
from agent.intelligence.repo_learning import load_knowledge, update_from_solution as update_repo_from_solution
from agent.intelligence.solution_memory import load_solution, list_solutions, mark_accepted, save_solution
from agent.intelligence.task_embeddings import index_solution, search_similar_solutions

__all__ = [
    "save_solution",
    "load_solution",
    "list_solutions",
    "mark_accepted",
    "index_solution",
    "search_similar_solutions",
    "ExperienceHints",
    "retrieve",
    "load_profile",
    "save_profile",
    "update_from_solution",
    "load_knowledge",
    "update_repo_from_solution",
]
