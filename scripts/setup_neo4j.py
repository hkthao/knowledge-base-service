from kb_indexer.log import configure_logging, get_logger
from kb_indexer.state import tracker
from kb_indexer.stores import neo4j_store


def main() -> None:
    configure_logging()
    log = get_logger(__name__)

    tracker.init_schema()
    log.info("sqlite_state_ready")

    drv = neo4j_store.driver()
    try:
        neo4j_store.ensure_constraints(drv)
        log.info("neo4j_constraints_ready")
    finally:
        drv.close()


if __name__ == "__main__":
    main()
