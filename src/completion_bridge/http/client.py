"""httpx 위의 SSE 전송과 JSON 요청."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

import httpx

from completion_bridge.errors import MappingError, TransportError
from completion_bridge.http.sse import SseEvent, SseParser

__all__ = ["HttpClient", "AsyncHttpClient"]


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


class HttpClient:
    """HTTP 연결 풀과 JSON 요청·SSE 응답 수신을 관리하는 동기 클라이언트.

    내부 httpx 클라이언트는 최초 요청에 생성하고 재사용한다. 외부 클라이언트는
    소유권을 넘겨받지 않으므로 종료하지 않는다. 직접 생성한 클라이언트는 종료
    후 다음 요청에서 다시 생성할 수 있다.
    """

    def __init__(self, client: httpx.Client | None = None, *, timeout: float = 120.0) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    def _get_client(self) -> httpx.Client:
        """주입된 클라이언트를 반환하거나 요청 시점에 내부 연결 풀을 생성한다."""
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def post_json(
        self,
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
            return _json_object(self._get_client().post(url, json=json, headers=dict(headers)))
        except httpx.RequestError as exc:
            raise TransportError(f"HTTP request failed ({type(exc).__name__})") from exc

    def stream_sse(
        self,
        url: str,
        *,
        json: Any,
        headers: Mapping[str, str] | None = None,
        method: str = "POST",
    ) -> Iterator[SseEvent]:
        """HTTP 요청 후 수신한 SSE 이벤트를 순서대로 반환한다."""
        try:
            parser = SseParser()
            with self._get_client().stream(
                method, url, json=json, headers=dict(headers or {})
            ) as response:
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

    def close(self) -> None:
        """직접 생성한 httpx 클라이언트만 종료하고 참조를 해제한다."""
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None


class AsyncHttpClient:
    """HTTP 연결 풀과 JSON 요청·SSE 응답 수신을 관리하는 비동기 클라이언트.

    내부 httpx 클라이언트는 최초 요청에 생성하고 재사용한다. 외부 클라이언트는
    소유권을 넘겨받지 않으므로 종료하지 않는다. 직접 생성한 클라이언트는 종료
    후 다음 요청에서 다시 생성할 수 있다.
    """

    def __init__(self, client: httpx.AsyncClient | None = None, *, timeout: float = 120.0) -> None:
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    def _get_client(self) -> httpx.AsyncClient:
        """주입된 클라이언트를 반환하거나 요청 시점에 내부 연결 풀을 생성한다."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def post_json(
        self,
        url: str,
        *,
        json: Any,
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        """JSON body를 POST하고 응답 JSON 객체를 비동기로 반환한다."""
        try:
            return _json_object(
                await self._get_client().post(url, json=json, headers=dict(headers))
            )
        except httpx.RequestError as exc:
            raise TransportError(f"HTTP request failed ({type(exc).__name__})") from exc

    async def stream_sse(
        self,
        url: str,
        *,
        json: Any,
        headers: Mapping[str, str] | None = None,
        method: str = "POST",
    ) -> AsyncIterator[SseEvent]:
        """HTTP 요청 후 수신한 SSE 이벤트를 비동기로 반환한다."""
        try:
            parser = SseParser()
            async with self._get_client().stream(
                method, url, json=json, headers=dict(headers or {})
            ) as response:
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

    async def aclose(self) -> None:
        """직접 생성한 httpx 클라이언트만 종료하고 참조를 해제한다."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
