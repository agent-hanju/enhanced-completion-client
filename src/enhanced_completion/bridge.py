"""진입점. 설정을 등록해 인스턴스를 만들고 그것으로 벤더 API와 도메인 모델을 잇는다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, Sequence
from typing import Any

import httpx

from .blocks import ContentBlock, TextBlock
from .errors import StreamNotFinished
from .hub import HubMessage, HubRequest, HubResponse, ToolDefinition
from .mapper import StreamMapper, compose
from .merge import StreamMerger
from .transport.http import astream_sse, stream_sse
from .transport.sse import SseFrame
from .vendors.base import VendorAdapter
from .vocabulary import Vocabulary

__all__ = ["AsyncStream", "Bridge", "SyncBridge", "SyncStream"]

MessageInput = HubMessage | str | Mapping[str, Any]


class _Lowerer:
    """등록된 어휘를 차례로 적용해 블록 리스트를 요청 text로 되쓴다.

    각 어휘가 자기 블록을 text로 접어 넣고, 남은 본문 블록만 wire에 실린다. 아무 어휘도
    가져가지 않은 블록은 생략된다. 다른 벤더로 옮길 수 없는 추론 블록이 이 경로로 조용히 빠진다.
    """

    def __init__(self, vocabularies: Sequence[Vocabulary], vendor_name: str) -> None:
        self._vocabularies = vocabularies
        self._vendor = vendor_name

    def lower_text(self, blocks: Any) -> str:
        current: list[ContentBlock] = list(blocks)
        for vocabulary in self._vocabularies:
            replaced = vocabulary.lower(current)
            if replaced is not None:
                current = list(replaced)
        return "".join(b.text for b in current if isinstance(b, TextBlock))


class _Pipeline:
    """벤더 어댑터와 어휘 올림 매퍼를 이어 만든 변환 파이프라인.

    상태를 가진 매퍼가 섞여 있으므로 스트림마다 새로 만든다.
    """

    def __init__(self, vendor: VendorAdapter, vocabularies: Sequence[Vocabulary]) -> None:
        stages: list[StreamMapper[Any, Any]] = [vendor.to_hub()]
        for vocabulary in vocabularies:
            lift = vocabulary.lift_mapper()
            if lift is not None:
                stages.append(lift)
        self._mapper = compose(*stages)

    def map(self, chunk: Any) -> list[HubResponse]:
        return list(self._mapper.map(chunk))

    def flush(self) -> list[HubResponse]:
        return list(self._mapper.flush())


class _StreamBase:
    """델타를 흘리면서 같은 델타를 병합해 최종 결과를 만든다.

    하나의 스트림이 두 갈래로 소비된다. 소비자는 조각을 받고, 병합기는 같은 조각을 접는다.
    Java ``StreamHandle``의 핵심 성질이고 여기서도 유지한다.
    """

    def __init__(self, vendor: VendorAdapter, vocabularies: Sequence[Vocabulary]) -> None:
        self._vendor = vendor
        self._pipeline = _Pipeline(vendor, vocabularies)
        self._merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
        self._finished = False
        self._closed = False

    @property
    def result(self) -> HubResponse:
        """병합된 최종 결과. 스트림이 끝나기 전에 읽으면 예외."""
        if not self._finished:
            raise StreamNotFinished("stream is still running; iterate it to completion first")
        return self._merger.build()

    @property
    def partial(self) -> HubResponse:
        """지금까지 접힌 부분 결과. 취소 후 남은 것을 읽는 통로."""
        return self._merger.build()

    def _process(self, frame: SseFrame) -> tuple[list[HubResponse], bool]:
        """프레임 하나를 델타 리스트와 종료 여부로 바꾼다."""
        if self._vendor.is_terminal(frame):
            return [], True
        if frame.is_comment_only:
            # 하트비트다. 흘려보낸다.
            return [], False
        chunk = self._vendor.decode(frame)
        if chunk is None:
            return [], False
        return self._emit(self._pipeline.map(chunk)), False

    def _finish(self) -> list[HubResponse]:
        return self._emit(self._pipeline.flush())

    def _emit(self, deltas: Iterable[HubResponse]) -> list[HubResponse]:
        out: list[HubResponse] = []
        for delta in deltas:
            self._merger.apply(delta)
            out.append(delta)
        return out


class AsyncStream(_StreamBase):
    """``async for``로 델타를 소비하는 스트림."""

    def __init__(
        self,
        vendor: VendorAdapter,
        vocabularies: Sequence[Vocabulary],
        frames: AsyncIterator[SseFrame],
    ) -> None:
        super().__init__(vendor, vocabularies)
        self._frames = frames

    def __aiter__(self) -> AsyncIterator[HubResponse]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[HubResponse]:
        try:
            async for frame in self._frames:
                deltas, terminal = self._process(frame)
                for delta in deltas:
                    yield delta
                if terminal:
                    break
            for delta in self._finish():
                yield delta
            self._finished = True
        finally:
            await self._release()

    async def aclose(self) -> None:
        """스트림을 끊는다. 매퍼를 flush해 부분 결과를 :attr:`partial`에 남긴다."""
        if self._closed:
            return
        self._finish()
        await self._release()

    async def _release(self) -> None:
        if self._closed:
            return
        self._closed = True
        aclose = getattr(self._frames, "aclose", None)
        if aclose is not None:
            await aclose()


class SyncStream(_StreamBase):
    """``for``로 델타를 소비하는 스트림."""

    def __init__(
        self,
        vendor: VendorAdapter,
        vocabularies: Sequence[Vocabulary],
        frames: Iterator[SseFrame],
    ) -> None:
        super().__init__(vendor, vocabularies)
        self._frames = frames

    def __iter__(self) -> Iterator[HubResponse]:
        try:
            for frame in self._frames:
                deltas, terminal = self._process(frame)
                yield from deltas
                if terminal:
                    break
            yield from self._finish()
            self._finished = True
        finally:
            self._release()

    def close(self) -> None:
        """스트림을 끊는다."""
        if self._closed:
            return
        self._finish()
        self._release()

    def _release(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self._frames, "close", None)
        if close is not None:
            close()


class _BridgeBase:
    """요청 조립과 어휘 등록. 전송 방식만 하위 클래스가 정한다."""

    def __init__(
        self,
        *,
        vendor: VendorAdapter,
        base_url: str,
        model: str | None = None,
        api_key: str | None = None,
        vocabularies: Sequence[Vocabulary] = (),
        headers: Mapping[str, str] | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._vendor = vendor
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._vocabularies = tuple(vocabularies)
        self._headers = dict(headers or {})
        self._timeout = timeout

        for vocabulary in self._vocabularies:
            vocabulary.register()

        self._lowerer = _Lowerer(self._vocabularies, vendor.name)

    @property
    def url(self) -> str:
        return f"{self._base_url}{self._vendor.path}"

    def build_request(
        self,
        messages: Sequence[MessageInput],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        """전송할 wire body를 만들어 돌려준다. 진단과 통과 경로에 쓴다."""
        request = HubRequest(
            model=model or self._model,
            messages=[_coerce_message(m) for m in messages],
            tools=list(tools),
            params=dict(params),
        )
        return self._vendor.build_body(request, self._lowerer)

    def _request_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            **self._headers,
        }
        if self._api_key:
            headers.setdefault("Authorization", f"Bearer {self._api_key}")
        return headers


class Bridge(_BridgeBase):
    """비동기 브리지.

    ``http_client``를 주입하면 재시도, 커넥션 풀, 프록시, TLS가 전부 그 클라이언트의 정책을
    따른다. 브리지는 그것을 다시 정의하지 않는다. 이 라이브러리는 소비 앱에서 provider 자리에
    놓이고, 그 자리는 전송 정책을 소유하지 않는다.

    ``httpx.AsyncHTTPTransport(retries=1)``이 연결 수립 실패만 재시도하고 헤더가 도착한 뒤에는
    아무것도 하지 않는데, 스트리밍 POST에 필요한 경계가 정확히 그것이다.
    """

    def __init__(
        self,
        *,
        vendor: VendorAdapter,
        base_url: str,
        model: str | None = None,
        api_key: str | None = None,
        vocabularies: Sequence[Vocabulary] = (),
        headers: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(
            vendor=vendor,
            base_url=base_url,
            model=model,
            api_key=api_key,
            vocabularies=vocabularies,
            headers=headers,
            timeout=timeout,
        )
        self._client = http_client
        self._owns_client = http_client is None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def stream(
        self,
        messages: Sequence[MessageInput],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        **params: Any,
    ) -> AsyncStream:
        """스트리밍 요청을 시작한다. 반환값을 ``async for``로 돌린다."""
        body = self.build_request(messages, tools=tools, model=model, **params)
        frames = astream_sse(
            self._get_client(), self.url, json=body, headers=self._request_headers()
        )
        return AsyncStream(self._vendor, self._vocabularies, frames)

    async def complete(
        self,
        messages: Sequence[MessageInput],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        **params: Any,
    ) -> HubResponse:
        """스트림을 끝까지 돌려 병합 결과만 돌려준다."""
        stream = self.stream(messages, tools=tools, model=model, **params)
        async for _ in stream:
            pass
        return stream.result

    async def aclose(self) -> None:
        """직접 만든 클라이언트만 닫는다. 주입받은 것은 호출자 소관이다."""
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Bridge:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()


class SyncBridge(_BridgeBase):
    """동기 브리지. :class:`Bridge`와 같은 모양이다.

    파서, 병합기, 어휘, 벤더 어댑터를 그대로 공유한다. 갈라지는 것은 전송 계층뿐이다.
    """

    def __init__(
        self,
        *,
        vendor: VendorAdapter,
        base_url: str,
        model: str | None = None,
        api_key: str | None = None,
        vocabularies: Sequence[Vocabulary] = (),
        headers: Mapping[str, str] | None = None,
        http_client: httpx.Client | None = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(
            vendor=vendor,
            base_url=base_url,
            model=model,
            api_key=api_key,
            vocabularies=vocabularies,
            headers=headers,
            timeout=timeout,
        )
        self._client = http_client
        self._owns_client = http_client is None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def stream(
        self,
        messages: Sequence[MessageInput],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        **params: Any,
    ) -> SyncStream:
        body = self.build_request(messages, tools=tools, model=model, **params)
        frames = stream_sse(
            self._get_client(), self.url, json=body, headers=self._request_headers()
        )
        return SyncStream(self._vendor, self._vocabularies, frames)

    def complete(
        self,
        messages: Sequence[MessageInput],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        **params: Any,
    ) -> HubResponse:
        stream = self.stream(messages, tools=tools, model=model, **params)
        for _ in stream:
            pass
        return stream.result

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> SyncBridge:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _coerce_message(value: MessageInput) -> HubMessage:
    """편의 입력을 허브 메시지로 바꾼다.

    문자열은 user 턴으로 본다. 가장 흔한 호출 모양이라 매번 감싸게 하지 않는다.
    """
    if isinstance(value, HubMessage):
        return value
    if isinstance(value, str):
        return HubMessage.user(value)
    return HubMessage.model_validate(dict(value))
