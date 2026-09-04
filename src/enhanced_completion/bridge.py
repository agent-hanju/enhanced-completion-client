"""진입점. 설정을 등록해 인스턴스를 만들고 그것으로 벤더 API와 도메인 모델을 잇는다."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

import httpx

from .blocks import (
    AudioBlock,
    ContentBlock,
    DocumentBlock,
    ImageBlock,
    ServerToolBlock,
    TextBlock,
    ToolResultBlock,
    VendorBlock,
)
from .errors import MappingError, StreamNotFinished
from .hub import HubMessage, HubRequest, HubResponse, ToolDefinition
from .mapper import StreamMapper, compose
from .merge import StreamMerger
from .parameters import Hyperparameters
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
        return "".join(self.lower_text_parts(blocks))

    def lower_text_parts(self, blocks: Any) -> list[str]:
        """어휘 적용 뒤에도 서로 다른 text block의 경계를 유지한다.

        어휘 자체가 여러 블록을 하나로 치환하는 경우만 그 어휘의 명시적 정책에 따라 합쳐진다.
        예를 들어 ``CiteVocabulary``는 인용 좌표를 전체 본문에 적용하므로 하나의 text를 만든다.
        """
        current: list[ContentBlock] = list(blocks)
        for vocabulary in self._vocabularies:
            replaced = vocabulary.lower(current)
            if replaced is not None:
                current = list(replaced)
        parts = [b.text for b in current if isinstance(b, TextBlock) and b.text]
        documents = self._lower_documents(current)
        if not documents:
            return parts
        if parts:
            parts[0] = f"{documents}\n\n{parts[0]}"
            return parts
        return [documents]

    @staticmethod
    def _lower_documents(blocks: list[ContentBlock]) -> str:
        """네이티브 문서 채널이 없는 벤더에서 문서를 본문에 실는다.

        허브 수준 기본 동작이다. 어휘에 맡기지 않는 이유는 사용자가 준 근거가 조용히 사라지는
        것이 최악의 결과이기 때문이다. 아무도 가져가지 않은 문서가 버려지면 모델은 근거 없이
        답하고, 호출자는 문서를 보냈다고 믿는다.

        네이티브 채널이 있는 어댑터는 ``build_body``에서 문서를 먼저 빼내므로 여기까지 오지
        않는다. 중복되지 않는다.

        문서를 본문보다 앞에 둔다. 네이티브 채널도 그 순서이고, 모델이 근거를 먼저 읽는다.
        """
        documents = [b for b in blocks if isinstance(b, DocumentBlock)]
        if not documents:
            return ""
        rendered = "\n".join(d.to_prompt() for d in documents)
        return f"<documents>\n{rendered}\n</documents>"


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
        """프레임 하나를 델타 리스트와 종료 여부로 바꾼다.

        종료 프레임도 먼저 해석한다. 마지막 프레임이 알맹이를 싣는 벤더가 있다. OpenAI
        Responses의 ``response.completed``에 ``stop_reason``과 ``usage``가 들어 있고,
        종료라고 바로 버리면 그 둘을 잃는다. ``[DONE]``처럼 알맹이가 없는 표지는 ``decode``가
        ``None``을 돌려주므로 그냥 지나간다.
        """
        if frame.is_comment_only:
            # 하트비트다. 흘려보낸다.
            return [], False
        terminal = self._vendor.is_terminal(frame)
        chunk = self._vendor.decode(frame)
        if chunk is None:
            return [], terminal
        return self._emit(self._pipeline.map(chunk)), terminal

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
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        vocabularies: Sequence[Vocabulary] = (),
        headers: Mapping[str, str] | None = None,
        timeout: float = 120.0,
    ) -> None:
        self._vendor = vendor
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._hyperparameters = Hyperparameters.coerce(hyperparameters)
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
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        """전송할 wire body를 만들어 돌려준다. 진단과 통과 경로에 쓴다."""
        normalized_messages = [_coerce_message(m) for m in messages]
        _reject_remote_content_references(normalized_messages, self._vendor.name)
        request = HubRequest(
            model=model or self._model,
            messages=normalized_messages,
            tools=list(tools),
            hyperparameters=self._hyperparameters.merged(hyperparameters),
            params=dict(params),
        )
        return self._vendor.build_body(request, self._lowerer)

    def _request_headers(self) -> dict[str, str]:
        """전송 헤더를 조립한다.

        우선순위가 셋이다. 기본값, 어댑터가 요구하는 것, 호출자가 준 것 순으로 덮인다.
        Anthropic의 ``anthropic-version``처럼 벤더가 요구하는 헤더가 있고, 사내 게이트웨이의
        CSRF 토큰처럼 호출자만 아는 것이 있다.
        """
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        vendor_headers = getattr(self._vendor, "request_headers", None)
        if callable(vendor_headers):
            headers.update(vendor_headers())
        headers.update(self._headers)
        if self._api_key:
            api_key_header = getattr(self._vendor, "api_key_header", "Authorization")
            if str(api_key_header).lower() == "authorization":
                headers.setdefault("Authorization", f"Bearer {self._api_key}")
            else:
                headers.setdefault(str(api_key_header), self._api_key)
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
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
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
            hyperparameters=hyperparameters,
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
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        **params: Any,
    ) -> AsyncStream:
        """스트리밍 요청을 시작한다. 반환값을 ``async for``로 돌린다."""
        body = self.build_request(
            messages,
            tools=tools,
            model=model,
            hyperparameters=hyperparameters,
            **params,
        )
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
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        **params: Any,
    ) -> HubResponse:
        """스트림을 끝까지 돌려 병합 결과만 돌려준다."""
        stream = self.stream(
            messages,
            tools=tools,
            model=model,
            hyperparameters=hyperparameters,
            **params,
        )
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
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
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
            hyperparameters=hyperparameters,
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
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        **params: Any,
    ) -> SyncStream:
        body = self.build_request(
            messages,
            tools=tools,
            model=model,
            hyperparameters=hyperparameters,
            **params,
        )
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
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        **params: Any,
    ) -> HubResponse:
        stream = self.stream(
            messages,
            tools=tools,
            model=model,
            hyperparameters=hyperparameters,
            **params,
        )
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


def _reject_remote_content_references(messages: Sequence[HubMessage], target: str) -> None:
    """공통 요청에서 벤더가 발급한 원격 파일/container 참조를 거부한다.

    참조의 발급 서버, 권한, 목적, 처리 상태와 만료를 이 패키지는 검증할 수 없다. 지원되는
    첨부는 inline data 또는 URL뿐이다. 응답 객체에는 진단을 위해 참조를 보존할 수 있지만,
    그것을 다음 요청으로 만드는 순간 명시적으로 실패시킨다.
    """

    def has_remote_key(value: Any) -> bool:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if key in {"file_id", "container_id"} and nested:
                    return True
                if has_remote_key(nested):
                    return True
        elif isinstance(value, list | tuple):
            return any(has_remote_key(item) for item in value)
        return False

    def check(block: ContentBlock) -> None:
        same_or_unscoped = block.source is None or block.source == target
        if (
            same_or_unscoped
            and isinstance(block, ImageBlock | AudioBlock | DocumentBlock)
            and block.file_id
        ):
            raise MappingError(
                "file_id based content is not supported; use a URL or inline base64 data"
            )
        reference = None
        if isinstance(block, ImageBlock):
            reference = block.url
        elif isinstance(block, AudioBlock | DocumentBlock):
            reference = block.uri
        if (
            same_or_unscoped
            and reference
            and urlsplit(reference).scheme.lower() not in {"http", "https", "data"}
        ):
            raise MappingError(
                "vendor file URIs are not supported; use an HTTP(S) URL or inline base64 data"
            )
        if isinstance(block, ToolResultBlock):
            for nested in block.blocks:
                if isinstance(nested, ContentBlock):
                    check(nested)
        if (
            isinstance(block, ServerToolBlock | VendorBlock)
            and block.source == target
            and (has_remote_key(block.raw) or has_remote_key(block.native))
        ):
            raise MappingError(
                "remote file/container references cannot be replayed; materialize them as URL "
                "or inline data"
            )

    for message in messages:
        for block in message.content:
            check(block)
