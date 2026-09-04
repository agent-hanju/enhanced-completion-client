"""httpx 위의 SSE 전송.

동기와 비동기가 :class:`~enhanced_completion.transport.sse.SseParser`를 공유한다. 갈라지는
것은 스트림을 읽는 방식뿐이다.

재시도와 커넥션 정책은 여기에 없다. 호출자가 구성한 httpx 클라이언트가 갖는다.
``AsyncHTTPTransport(retries=N)``은 연결 수립 실패만 재시도하고 헤더가 도착한 뒤에는 아무것도
하지 않는데, 스트리밍 POST에 필요한 경계가 정확히 그것이다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

import httpx

from ..errors import TransportError
from .sse import SseFrame, SseParser

__all__ = ["astream_sse", "stream_sse"]


def _check(response: httpx.Response, body: str) -> None:
    if response.is_success:
        return
    # 본문에 프롬프트나 인증정보가 실려 올 수 있으므로 앞부분만 남긴다.
    detail = body.strip()[:500]
    raise TransportError(
        f"{response.request.method} {response.request.url} failed with {response.status_code}",
        status_code=response.status_code,
        detail=detail,
    )


async def astream_sse(
    client: httpx.AsyncClient,
    url: str,
    *,
    json: Any,
    headers: Mapping[str, str] | None = None,
    method: str = "POST",
) -> AsyncIterator[SseFrame]:
    """SSE 프레임을 비동기로 흘린다."""
    parser = SseParser()
    async with client.stream(method, url, json=json, headers=dict(headers or {})) as response:
        if not response.is_success:
            _check(response, (await response.aread()).decode("utf-8", "replace"))
        async for chunk in response.aiter_text():
            for frame in parser.feed(chunk):
                yield frame
    for frame in parser.flush():
        yield frame


def stream_sse(
    client: httpx.Client,
    url: str,
    *,
    json: Any,
    headers: Mapping[str, str] | None = None,
    method: str = "POST",
) -> Iterator[SseFrame]:
    """SSE 프레임을 동기로 흘린다."""
    parser = SseParser()
    with client.stream(method, url, json=json, headers=dict(headers or {})) as response:
        if not response.is_success:
            _check(response, response.read().decode("utf-8", "replace"))
        for chunk in response.iter_text():
            for frame in parser.feed(chunk):
                yield frame
    for frame in parser.flush():
        yield frame
