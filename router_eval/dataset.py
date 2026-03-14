"""
Router evaluation dataset and category definitions.
Fixed after Phase 0 — do not modify when changing routers.
"""

from pathlib import Path
from typing import TypedDict

# Categories used by all routers; must match eval dataset labels.
CATEGORIES = ("EDIT", "SEARCH", "DOCS", "GENERAL", "INFRA")


class DatasetItem(TypedDict):
    instruction: str
    expected_category: str


def load_dataset(path: str | Path | None = None) -> list[DatasetItem]:
    """
    Load evaluation dataset: list of {instruction, expected_category}.
    If path is None, returns the built-in in-code dataset.
    """
    if path is not None:
        return _load_from_file(Path(path))
    return _builtin_dataset()


def _load_from_file(path: Path) -> list[DatasetItem]:
    """Load dataset from JSON or JSONL file."""
    import json

    data = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        items = [json.loads(line) for line in data.strip().splitlines() if line.strip()]
    else:
        items = json.loads(data)
    out: list[DatasetItem] = []
    for item in items:
        if isinstance(item, dict) and "instruction" in item and "expected_category" in item:
            out.append(
                {"instruction": item["instruction"], "expected_category": item["expected_category"]}
            )
    return out


def _builtin_dataset() -> list[DatasetItem]:
    """Hard real-world router evaluation dataset."""

    return [

        # ---------------- EDIT ----------------

        {
            "instruction": "Update the FastAPI login endpoint so it verifies JWT expiration before returning the user profile.",
            "expected_category": "EDIT",
        },
        {
            "instruction": "Refactor the order processing function to remove duplicated validation logic.",
            "expected_category": "EDIT",
        },
        {
            "instruction": "Modify the React LoginForm component so validation errors are displayed under each field.",
            "expected_category": "EDIT",
        },
        {
            "instruction": "Add bcrypt password hashing to the Node authentication module.",
            "expected_category": "EDIT",
        },
        {
            "instruction": "Rewrite the Rust worker loop so failed jobs are retried with exponential backoff.",
            "expected_category": "EDIT",
        },
        {
            "instruction": "Change the SQL query so soft-deleted users are filtered out by default.",
            "expected_category": "EDIT",
        },


        # ---------------- SEARCH ----------------

        {
            "instruction": "Locate where password hashing is implemented in this repository.",
            "expected_category": "SEARCH",
        },
        {
            "instruction": "Search the codebase for the function that generates JWT tokens.",
            "expected_category": "SEARCH",
        },
        {
            "instruction": "Find every place where Redis caching is used.",
            "expected_category": "SEARCH",
        },
        {
            "instruction": "Identify the file where the background job worker starts consuming the queue.",
            "expected_category": "SEARCH",
        },
        {
            "instruction": "Search the repo for usages of verify_password.",
            "expected_category": "SEARCH",
        },
        {
            "instruction": "Where is the Postgres connection pool initialized?",
            "expected_category": "SEARCH",
        },


        # ---------------- DOCS ----------------

        {
            "instruction": "What functions does the auth service expose to other modules?",
            "expected_category": "DOCS",
        },
        {
            "instruction": "Explain what parameters the createOrder API expects.",
            "expected_category": "DOCS",
        },
        {
            "instruction": "What does the storage client return when an upload succeeds?",
            "expected_category": "DOCS",
        },
        {
            "instruction": "List the public methods available in the payment service client.",
            "expected_category": "DOCS",
        },
        {
            "instruction": "What environment variables are required by the worker service?",
            "expected_category": "DOCS",
        },


        # ---------------- GENERAL ----------------

        {
            "instruction": "Explain how Redis eviction policies work.",
            "expected_category": "GENERAL",
        },
        {
            "instruction": "What is the difference between optimistic and pessimistic locking in databases?",
            "expected_category": "GENERAL",
        },
        {
            "instruction": "How does Kubernetes route traffic from a Service to Pods?",
            "expected_category": "GENERAL",
        },
        {
            "instruction": "Describe how JWT authentication works conceptually.",
            "expected_category": "GENERAL",
        },
        {
            "instruction": "Why might a database query become slow as a table grows?",
            "expected_category": "GENERAL",
        },


        # ---------------- INFRA ----------------

        {
            "instruction": "Create a Dockerfile for the FastAPI backend service.",
            "expected_category": "INFRA",
        },
        {
            "instruction": "Add Redis to docker-compose so the app can run locally.",
            "expected_category": "INFRA",
        },
        {
            "instruction": "Provision a Postgres database using Terraform.",
            "expected_category": "INFRA",
        },
        {
            "instruction": "Update the Kubernetes deployment to include Redis credentials as environment variables.",
            "expected_category": "INFRA",
        },
        {
            "instruction": "Add a CI pipeline step that runs pytest on every commit.",
            "expected_category": "INFRA",
        },


        # ---------------- HARD MIXED CASES ----------------

        # first action matters

        {
            "instruction": "Before modifying the authentication logic, locate where password validation currently happens.",
            "expected_category": "SEARCH",
        },
        {
            "instruction": "Investigate why login requests sometimes fail, then update the handler to log more context.",
            "expected_category": "SEARCH",
        },
        {
            "instruction": "Explain how the caching layer works before we refactor it.",
            "expected_category": "GENERAL",
        },
        {
            "instruction": "Find the module responsible for sending emails and then add retry logic.",
            "expected_category": "SEARCH",
        },
        {
            "instruction": "Check where the queue consumer is implemented and modify it to handle retries.",
            "expected_category": "SEARCH",
        },
    ]
