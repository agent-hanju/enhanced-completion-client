"""httpx 위의 SSE 전송.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

import httpx

from ..errors import TransportError
from .sse import SseEvent, SseParser

__all__ = ["stream_sse", "astream_sse"]

def stream_sse(
    client: httpx.Client,
    url: str,
    *,
    json: Any,
    headers: Mapping[str, str] | None = None,
    method: str = "POST",
) -> Iterator[SseEvent]:
    """SSE 이벤트를 출력한다."""
    parser = SseParser()
    with client.stream(method, url, json=json, headers=dict(headers or {})) as response:
        if not response.is_success:
            raise TransportError(
                f"{response.request.method} {response.request.url} failed with {response.status_code}",
                status_code=response.status_code,
                detail=response.read().decode("utf-8", "replace")
            )
        for chunk in response.iter_text():
            for event in parser.feed(chunk):
                yield event
    for event in parser.flush():
        yield event


async def astream_sse(
    client: httpx.AsyncClient,
    url: str,
    *,
    json: Any,
    headers: Mapping[str, str] | None = None,
    method: str = "POST",
) -> AsyncIterator[SseEvent]:
    """SSE 이벤트를 비동기로 출력한다."""
    parser = SseParser()
    async with client.stream(method, url, json=json, headers=dict(headers or {})) as response:
        if not response.is_success:
            raise TransportError(
                f"{response.request.method} {response.request.url} failed with {response.status_code}",
                status_code=response.status_code,
                detail=(await response.aread()).decode("utf-8", "replace")
            )
        async for chunk in response.aiter_text():
            for event in parser.feed(chunk):
                yield event
    for event in parser.flush():
        yield event