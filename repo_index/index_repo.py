"""CLI entry point: python -m repo_index.index_repo <repo_path> [--verbose]"""

import argparse
import logging
import sys

from repo_index.indexer import index_repo


def main():
    parser = argparse.ArgumentParser(
        description="Index repository for symbol graph and embeddings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example: python -m repo_index.index_repo . --verbose",
    )
    parser.add_argument(
        "repo_path",
        nargs="?",
        default=".",
        help="Path to repository root (default: .)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log each file indexed or skipped.",
    )
    parser.add_argument(
        "--no-gitignore",
        action="store_true",
        help="Do not exclude paths matching .gitignore.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s" if args.verbose else "%(levelname)s: %(message)s",
    )
    symbols, path = index_repo(
        args.repo_path,
        ignore_gitignore=not args.no_gitignore,
        verbose=args.verbose,
        print_summary=True,
    )
    print(f"\nIndexed {len(symbols)} symbols → {path}")


if __name__ == "__main__":
    main()
