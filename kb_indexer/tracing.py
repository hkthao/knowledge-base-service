"""Langfuse tracing — no-op when not configured.

Plan §3 lists Langfuse for observing every /search call. We use the SDK
in a fail-open way: if `langfuse_*` settings aren't populated we just
return a stub trace object so call sites don't have to branch.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from .log import get_logger
from .settings import settings

log = get_logger(__name__)

_client = None
_init_attempted = False


def _get_client():
    global _client, _init_attempted
    if _init_attempted:
        return _client
    _init_attempted = True

    if not (settings.langfuse_host and settings.langfuse_secret_key and settings.langfuse_public_key):
        return None
    try:
        from langfuse import Langfuse  # noqa: WPS433 (lazy)
        _client = Langfuse(
            host=settings.langfuse_host,
            secret_key=settings.langfuse_secret_key,
            public_key=settings.langfuse_public_key,
        )
    except Exception as exc:
        log.warning("langfuse_init_failed", error=str(exc))
        _client = None
    return _client


class _StubTrace:
    """Quack like a Langfuse trace; methods are no-ops."""

    def update(self, **_: Any) -> None: ...
    def end(self, **_: Any) -> None: ...
    def event(self, **_: Any) -> None: ...
    def span(self, **_: Any) -> "_StubTrace":  # pragma: no cover
        return self


@contextmanager
def trace_search(query: str, *, top_k: int, collections: list[str], filters: dict | None):
    """Context manager that yields a trace handle. The handle is either a
    real Langfuse trace or a stub — call sites use the same API either way."""
    client = _get_client()
    if client is None:
        yield _StubTrace()
        return

    trace = client.trace(
        name="search",
        input={"query": query, "top_k": top_k, "collections": collections, "filters": filters},
    )
    try:
        yield trace
    except Exception as exc:
        trace.update(level="ERROR", status_message=str(exc))
        raise
    finally:
        try:
            client.flush()
        except Exception:
            pass
