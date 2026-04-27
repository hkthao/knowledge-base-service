"""One-shot repair pass. Run via cron every 15 minutes."""

import argparse

from kb_indexer.log import configure_logging, get_logger
from kb_indexer.repair import run_repair_pass
from kb_indexer.state import tracker


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", default=None, help="Required if .cs files appear in failed/dirty queue.")
    parser.add_argument("--failed-limit", type=int, default=100)
    parser.add_argument("--dirty-limit", type=int, default=200)
    parser.add_argument("--sample-fraction", type=float, default=0.01)
    args = parser.parse_args()

    configure_logging()
    log = get_logger(__name__)
    tracker.init_schema()

    summary = run_repair_pass(
        repo_path=args.repo_path,
        failed_limit=args.failed_limit,
        dirty_limit=args.dirty_limit,
        sample_fraction=args.sample_fraction,
    )
    log.info("repair_done", **{k: v for k, v in summary.items() if k != "errors"})


if __name__ == "__main__":
    main()
