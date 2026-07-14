"""Shared outbound HTTP client for F.R.E.J.A.

Historically almost every outbound call built a fresh ``httpx.AsyncClient()`` per request,
which tears down the connection pool every time — no keep-alive reuse and an extra TLS
handshake on each call (see Issue #24). This module owns a single process-wide
``httpx.AsyncClient`` with sensible timeouts and connection limits, reused across all
callers, and closed cleanly on application shutdown.

Usage keeps the familiar ``async with`` shape at every call site::

    from backend.services.http_client import shared_client

    async with shared_client() as client:
        res = await client.get(url, timeout=8.0)

``shared_client()`` yields the shared client but does NOT close it on exit — the pool lives
for the whole process and is disposed once via ``close_shared_client()`` from the FastAPI
lifespan shutdown. Per-request ``timeout=`` overrides still work as before.
"""

from contextlib import asynccontextmanager

import httpx

# Module-level singleton. Created lazily on first use so importing this module never opens
# sockets, and re-created transparently if it was closed (e.g. after a shutdown in tests).
_shared_client: "httpx.AsyncClient | None" = None

# Defaults: a generous total timeout with a tighter connect timeout, plus a bounded pool so a
# burst of concurrent calls can't open unlimited sockets. Individual calls may still pass their
# own ``timeout=`` to override the total for that request.
_DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_DEFAULT_LIMITS = httpx.Limits(max_connections=100, max_keepalive_connections=20)


def get_shared_client() -> httpx.AsyncClient:
    """Returns the process-wide shared client, creating it on first use."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, limits=_DEFAULT_LIMITS)
    return _shared_client


@asynccontextmanager
async def shared_client():
    """Async-context wrapper that yields the shared client without closing it on exit.

    This lets call sites keep their existing ``async with ... as client:`` structure while
    reusing one connection pool across the whole process. The underlying client is disposed
    only by ``close_shared_client()`` at shutdown, never when a single ``async with`` block
    ends."""
    yield get_shared_client()


async def close_shared_client() -> None:
    """Closes the shared client and its connection pool. Called from the app's shutdown."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None
