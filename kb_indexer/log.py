import logging
import sys

import structlog

from .settings import settings


def configure_logging() -> None:
    # Log ra stderr — stdout giữ sạch cho MCP stdio JSON-RPC. FastAPI / CLI
    # vẫn nhận log như bình thường vì terminal hiển thị cả stderr.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.log_level.upper(), logging.INFO),
        ),
        # PrintLogger mặc định ghi stdout — đẩy sang stderr để MCP stdio sạch
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
