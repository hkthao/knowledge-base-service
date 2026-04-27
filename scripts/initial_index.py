import argparse

from kb_indexer.embedder import make_embedder
from kb_indexer.indexing import index_repo
from kb_indexer.log import configure_logging, get_logger
from kb_indexer.state import tracker


def main() -> None:
    parser = argparse.ArgumentParser(description="Initial full index of a TS/JS repo.")
    parser.add_argument("--repo", required=True, help="Logical repo name stored in payload.")
    parser.add_argument("--path", required=True, help="Filesystem path to the repo root.")
    parser.add_argument(
        "--embedder",
        default="auto",
        choices=("auto", "voyage", "ollama"),
        help="Dense embedder. auto = voyage if VOYAGE_API_KEY set, else ollama.",
    )
    args = parser.parse_args()

    configure_logging()
    log = get_logger(__name__)

    tracker.init_schema()
    embedder = make_embedder(args.embedder)
    log.info("starting_initial_index", repo=args.repo, path=args.path, embedder=type(embedder).__name__)

    summary = index_repo(repo=args.repo, repo_path=args.path, embedder=embedder)
    log.info("initial_index_done", **{k: v for k, v in summary.items() if k != "failures"})
    if summary["failures"]:
        log.warning("initial_index_failures", count=len(summary["failures"]))
        for failure in summary["failures"][:20]:
            log.warning("failure", detail=failure)


if __name__ == "__main__":
    main()
