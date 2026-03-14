"""
Router v2 evaluation dataset. 4-category taxonomy: EDIT, SEARCH, EXPLAIN, INFRA.
"""

from pathlib import Path
from typing import TypedDict

CATEGORIES_V2 = ("EDIT", "SEARCH", "EXPLAIN", "INFRA")

# Path to the golden evaluation dataset (JSON file next to this module).
GOLDEN_DATASET_PATH = Path(__file__).parent / "golden_dataset_v2.json"
# Path to the adversarial (edge-case) evaluation dataset.
ADVERSARIAL_DATASET_PATH = Path(__file__).parent / "adversarial_dataset_v2.json"


class DatasetItemV2(TypedDict):
    instruction: str
    expected_category: str


def load_dataset_v2(
    path: str | Path | None = None,
    use_golden: bool = False,
    use_adversarial: bool = False,
) -> list[DatasetItemV2]:
    """
    Load v2 evaluation dataset: list of {instruction, expected_category}.

    - use_adversarial=True: load from the adversarial dataset file (adversarial_dataset_v2.json).
    - use_golden=True: load from the golden dataset file (golden_dataset_v2.json).
    - path given (and neither adversarial nor golden): load from that file path.
    - otherwise: return the built-in in-code (normal) dataset.

    Default is the normal built-in dataset.
    """
    if use_adversarial:
        return _load_from_file(ADVERSARIAL_DATASET_PATH)
    if use_golden:
        return _load_from_file(GOLDEN_DATASET_PATH)
    if path is not None:
        return _load_from_file(Path(path))
    return _builtin_dataset_v2()


def _load_from_file(path: Path) -> list[DatasetItemV2]:
    """Load dataset from JSON or JSONL file."""
    import json

    data = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        items = [json.loads(line) for line in data.strip().splitlines() if line.strip()]
    else:
        items = json.loads(data)
    out: list[DatasetItemV2] = []
    for item in items:
        if isinstance(item, dict) and "instruction" in item and "expected_category" in item:
            out.append(
                {"instruction": item["instruction"], "expected_category": item["expected_category"]}
            )
    return out

def _builtin_dataset_v2() -> list[DatasetItemV2]:
    """
    Harder router evaluation dataset.
    Instructions mimic realistic upstream model outputs with mixed intent.
    """

    return [

    # ---------------- EDIT ----------------

    {
        "instruction": "Update the FastAPI login handler so expired JWTs return a 401 instead of silently refreshing.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Modify the Redis caching wrapper so keys automatically expire after 10 minutes.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Refactor the payment service so the retry logic is centralized instead of duplicated in each endpoint.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Add request validation to the createUser endpoint to reject malformed email addresses.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Rewrite the worker loop so failed jobs are retried using exponential backoff.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Fix the memory leak in the websocket connection manager.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Extend the TypeScript User interface to include a lastLogin timestamp.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Modify the SQL query so archived records are excluded unless explicitly requested.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Update the Go HTTP client so timeouts are configurable via environment variables.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Add structured logging to the order processing pipeline.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Change the retry strategy so transient network failures retry up to three times.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Update the file upload handler to reject files larger than 10MB.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Improve the error handling in the database transaction helper.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Modify the authentication middleware so it supports both API keys and JWT tokens.",
        "expected_category": "EDIT",
    },
    {
        "instruction": "Rewrite the background job scheduler so tasks are persisted across restarts.",
        "expected_category": "EDIT",
    },

    # ---------------- SEARCH ----------------

    {
        "instruction": "Locate where JWT tokens are generated in the backend.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Search the repository for where Redis clients are instantiated.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Find the module responsible for sending transactional emails.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Identify where the HTTP routes are registered in the Go server.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Before modifying the auth flow locate the function that validates tokens.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Find every place where the verify_password helper is used.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Search the codebase for usages of the orderRepository interface.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Where does the application initialize the Postgres connection pool?",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Find the entrypoint where the background worker begins consuming jobs.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Look for any middleware that performs request authentication.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Locate the part of the code responsible for uploading files to S3.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Find where request IDs are generated for incoming API calls.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Search the repo for code that handles websocket connections.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Check which module is responsible for metrics collection.",
        "expected_category": "SEARCH",
    },
    {
        "instruction": "Identify where the job queue consumer is implemented.",
        "expected_category": "SEARCH",
    },

    # ---------------- EXPLAIN ----------------

    {
        "instruction": "Explain how Redis caching reduces database load.",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "Describe how JWT authentication works conceptually.",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "What does the createOrder API expect in its request body?",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "Explain the purpose of the repository pattern used in this project.",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "How does Kubernetes route traffic from a Service to Pods?",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "What happens internally when a FastAPI dependency is injected?",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "Describe how optimistic locking works in relational databases.",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "Explain why database indexes improve query performance.",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "What information does the authentication service expose to other modules?",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "Describe the lifecycle of a request in this backend system.",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "Explain the difference between synchronous and asynchronous job queues.",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "Why might a database query become slow as a table grows?",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "Explain how rate limiting middleware typically works.",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "What does the storage client return when an upload succeeds?",
        "expected_category": "EXPLAIN",
    },
    {
        "instruction": "Describe how distributed locks work using Redis.",
        "expected_category": "EXPLAIN",
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
        "instruction": "Update the Kubernetes deployment to include Redis credentials.",
        "expected_category": "INFRA",
    },
    {
        "instruction": "Add a CI step that runs pytest on every commit.",
        "expected_category": "INFRA",
    },
    {
        "instruction": "Configure environment variables required by the worker service.",
        "expected_category": "INFRA",
    },
    {
        "instruction": "Create a docker-compose override file for local development.",
        "expected_category": "INFRA",
    },
    {
        "instruction": "Add a GitHub Actions workflow that builds and pushes the Docker image.",
        "expected_category": "INFRA",
    },
    {
        "instruction": "Configure the Terraform backend to use an S3 bucket for state.",
        "expected_category": "INFRA",
    },
    {
        "instruction": "Set up monitoring for the API using Prometheus and Grafana.",
        "expected_category": "INFRA",
    },
    {
        "instruction": "Add health check endpoints so Kubernetes can detect unhealthy pods.",
        "expected_category": "INFRA",
    },
    {
        "instruction": "Configure log aggregation using Loki.",
        "expected_category": "INFRA",
    },
    {
        "instruction": "Set up automatic database migrations during deployment.",
        "expected_category": "INFRA",
    },
    {
        "instruction": "Add secrets management for API keys using Kubernetes Secrets.",
        "expected_category": "INFRA",
    },
    {
        "instruction": "Configure staging and production environments in the CI pipeline.",
        "expected_category": "INFRA",
    },

    ]