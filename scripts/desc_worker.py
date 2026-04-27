"""Drain the Vietnamese description-job queue.

Run as a long-lived process alongside the API. The pipeline stays decoupled:
indexing writes structural code chunks immediately; this worker fills in
descriptions when it can. If the LLM is unavailable the queue just backs
up — code search still works.

Usage:
  python -m scripts.desc_worker             # run forever
  python -m scripts.desc_worker --once      # process one batch, then exit
"""

import argparse

from kb_indexer.description_worker import process_batch, run_forever
from kb_indexer.log import configure_logging, get_logger
from kb_indexer.state import tracker


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Process one batch and exit.")
    args = parser.parse_args()

    configure_logging()
    log = get_logger(__name__)
    tracker.init_schema()

    if args.once:
        result = process_batch()
        log.info("desc_worker_once_done", **result)
        return

    log.info("desc_worker_starting")
    run_forever()


if __name__ == "__main__":
    main()
