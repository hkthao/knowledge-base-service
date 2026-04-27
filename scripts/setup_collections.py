from kb_indexer.log import configure_logging, get_logger
from kb_indexer.stores import qdrant_store
from kb_indexer.stores.qdrant_store import ALL_COLLECTIONS


def main() -> None:
    configure_logging()
    log = get_logger(__name__)
    qc = qdrant_store.client()
    for name in ALL_COLLECTIONS:
        qdrant_store.create_collection_if_not_exists(qc, name)
        log.info("collection_ready", name=name)


if __name__ == "__main__":
    main()
