"""Compute file-level CO_CHANGED edges from git history.

Run after the repo has been indexed at least once so the Module nodes
exist in Neo4j; this script only adds edges, it doesn't create new nodes.
"""

import argparse

from kb_indexer.extractors import co_change_builder
from kb_indexer.log import configure_logging, get_logger
from kb_indexer.stores import neo4j_store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True, help="Filesystem path to the repo root.")
    parser.add_argument("--lookback", type=int, default=co_change_builder.DEFAULT_LOOKBACK)
    parser.add_argument("--min-count", type=int, default=co_change_builder.DEFAULT_MIN_COUNT)
    parser.add_argument(
        "--max-files-per-commit",
        type=int,
        default=co_change_builder.DEFAULT_MAX_FILES_PER_COMMIT,
        help="Drop commits touching more files than this (mass-format / mass-rename).",
    )
    args = parser.parse_args()

    configure_logging()
    log = get_logger(__name__)

    pairs = co_change_builder.build_pairs(
        args.path,
        lookback=args.lookback,
        min_count=args.min_count,
        max_files_per_commit=args.max_files_per_commit,
    )
    log.info("co_change_pairs_built", pairs=len(pairs))

    drv = neo4j_store.driver()
    try:
        written = co_change_builder.write_to_neo4j(drv, pairs, repo_path=args.path)
        log.info("co_change_written", edges=written)
    finally:
        drv.close()


if __name__ == "__main__":
    main()
