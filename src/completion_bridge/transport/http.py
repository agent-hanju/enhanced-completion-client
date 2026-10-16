"""httpx 위의 SSE 전송과 JSON 요청."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

import httpx

from completion_bridge.errors import MappingError, TransportError
from completion_bridge.transport.sse import SseEvent, SseParser

__all__ = ["apost_json", "astream_sse", "post_json", "stream_sse"]


def _safe_target(request: httpx.Request) -> str:
    """인증 정보와 쿼리·fragment를 제외한 HTTP 요청 위치를 반환한다."""
    url = request.url.copy_with(username=None, password=None, query=None, fragment=None)
    return f"{request.method} {url}"


def _detail(body: bytes) -> str:
    """명시적 진단 조회를 위해 오류 본문 앞부분을 보존한다. 운영 로그에는 기록하지 않는다."""
    return body.decode("utf-8", "replace").strip()[:500]


def _json_object(response: httpx.Response) -> dict[str, Any]:
    """본문을 이미 읽은 응답에서 JSON 객체를 꺼낸다.

    Raises:
        TransportError: 응답 상태가 2xx가 아닐 때. 본문 앞부분을 ``detail``에 남긴다.
        MappingError: 본문이 JSON이 아니거나 JSON 객체가 아닐 때.
    """
    target = _safe_target(response.request)
    if not response.is_success:
        raise TransportError(
            f"{target} failed with {response.status_code}",
            status_code=response.status_code,
            detail=_detail(response.content),
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise MappingError(f"{target} returned a body that is not JSON") from exc
    if not isinstance(payload, dict):
        raise MappingError(f"{target} returned JSON that is not an object")
    return payload


def post_json(
    client: httpx.Client,
    url: str,
    *,
    json: Any,
    headers: Mapping[str, str],
) -> dict[str, Any]:
    """JSON body를 POST하고 응답 JSON 객체를 돌려준다.

    Raises:
        TransportError: 응답 상태가 2xx가 아닐 때.
        MappingError: 응답 본문이 JSON 객체가 아닐 때.
    """
    try:
        return _json_object(client.post(url, json=json, headers=dict(headers)))
    except httpx.RequestError as exc:
        raise TransportError(f"HTTP request failed ({type(exc).__name__})") from exc


async def apost_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    json: Any,
    headers: Mapping[str, str],
) -> dict[str, Any]:
    """:func:`post_json`의 비동기판."""
    try:
        return _json_object(await client.post(url, json=json, headers=dict(headers)))
    except httpx.RequestError as exc:
        raise TransportError(f"HTTP request failed ({type(exc).__name__})") from exc


def stream_sse(
    client: httpx.Client,
    url: str,
    *,
    json: Any,
    headers: Mapping[str, str] | None = None,
    method: str = "POST",
) -> Iterator[SseEvent]:
    """SSE 이벤트를 출력한다."""
    try:
        parser = SseParser()
        with client.stream(method, url, json=json, headers=dict(headers or {})) as response:
            if not response.is_success:
                raise TransportError(
                    f"{_safe_target(response.request)} failed with {response.status_code}",
                    status_code=response.status_code,
                    detail=_detail(response.read()),
                )
            for chunk in response.iter_text():
                for event in parser.feed(chunk):
                    yield event
        for event in parser.flush():
            yield event
    except httpx.RequestError as exc:
        raise TransportError(f"HTTP request failed ({type(exc).__name__})") from exc


async def astream_sse(
    client: httpx.AsyncClient,
    url: str,
    *,
    json: Any,
    headers: Mapping[str, str] | None = None,
    method: str = "POST",
) -> AsyncIterator[SseEvent]:
    """SSE 이벤트를 비동기로 출력한다."""
    try:
        parser = SseParser()
        async with client.stream(method, url, json=json, headers=dict(headers or {})) as response:
            if not response.is_success:
                raise TransportError(
                    f"{_safe_target(response.request)} failed with {response.status_code}",
                    status_code=response.status_code,
                    detail=_detail(await response.aread()),
                )
            async for chunk in response.aiter_text():
                for event in parser.feed(chunk):
                    yield event
        for event in parser.flush():
            yield event
    except httpx.RequestError as exc:
        raise TransportError(f"HTTP request failed ({type(exc).__name__})") from exc
