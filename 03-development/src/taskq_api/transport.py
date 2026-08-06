"""Sync ASGI transport bridge for httpx 0.28.

[FR-01]
Citations: SPEC.md lines 79-91.

httpx 0.28 ships only the async ``handle_async_request`` for ASGITransport,
so the synchronous acceptance harness cannot reuse the standard sync client
without a thin adapter that delegates to ``anyio.run``. This module is the
adapter; it is invoked once at app import time to monkey-patch the missing
sync entry points onto ``httpx.ASGITransport``.

Isolating the patch in its own module keeps the application factory focused
on request/response wiring and makes the dependency on the httpx monkey
patch explicit at the import site.
"""

from __future__ import annotations

import anyio
import httpx


def install_sync_asgi_transport() -> None:
    """Bridge httpx 0.28's async-only ASGITransport to a sync context manager.

    ``httpx.Client(transport=ASGITransport(app))`` requires both a synchronous
    ``handle_request`` and ``__enter__``/``__exit__`` on the transport. httpx
    0.28 only ships the async variants, so the sync acceptance harness needs
    this thin shim that delegates to ``handle_async_request`` via ``anyio.run``.
    """
    transport_cls = httpx.ASGITransport
    if hasattr(transport_cls, "handle_request"):
        return

    def _handle_request(
        transport: httpx.ASGITransport, request: httpx.Request
    ) -> httpx.Response:
        request_body = request.read()

        async def _send() -> tuple[
            int, httpx.Headers, dict[str, object], bytes
        ]:
            async_request = httpx.Request(
                request.method,
                request.url,
                headers=request.headers,
                content=request_body,
                extensions=request.extensions,
            )
            response = await transport.handle_async_request(async_request)
            body = await response.aread()
            return (
                response.status_code,
                response.headers,
                response.extensions,
                body,
            )

        status_code, headers, extensions, body = anyio.run(_send)
        return httpx.Response(
            status_code,
            headers=headers,
            content=body,
            extensions=extensions,
            request=request,
        )

    def _enter(transport: httpx.ASGITransport) -> httpx.ASGITransport:
        return transport

    def _exit(
        _transport: httpx.ASGITransport,
        _exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        return None

    transport_cls.handle_request = _handle_request  # type: ignore[attr-defined]
    transport_cls.__enter__ = _enter  # type: ignore[attr-defined]
    transport_cls.__exit__ = _exit  # type: ignore[attr-defined]