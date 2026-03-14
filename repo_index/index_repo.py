"""CLI entry point: python -m repo_index.index_repo <repo_path>"""

import logging
import sys

from repo_index.indexer import index_repo


def main():
    logging.basicConfig(level=logging.INFO)
    repo_path = sys.argv[1] if len(sys.argv) > 1 else "."
    symbols, path = index_repo(repo_path)
    print(f"Indexed {len(symbols)} symbols -> {path}")


if __name__ == "__main__":
    main()
