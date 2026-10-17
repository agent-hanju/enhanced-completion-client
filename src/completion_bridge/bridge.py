"""진입점. 설정을 등록해 인스턴스를 만들고 그것으로 벤더 API와 도메인 모델을 잇는다."""

from __future__ import annotations

import copy
import html
import logging
import sys
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import asdict
from typing import Any, Protocol, cast, runtime_checkable
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel

from completion_bridge.errors import (
    ExecutionStateError,
    MappingError,
)
from completion_bridge.http.client import AsyncHttpClient, HttpClient
from completion_bridge.http.sse import SseEvent
from completion_bridge.logging import OperationLog, WireLog
from completion_bridge.models.blocks import (
    UNAVAILABLE,
    AnnotationBlock,
    AudioBlock,
    ContentBlock,
    DocumentBlock,
    GroundingBlock,
    ImageBlock,
    SearchResultBlock,
    ServerToolBlock,
    TextBlock,
    ToolResultBlock,
    VendorBlock,
)
from completion_bridge.models.hub import (
    HubMessage,
    HubRequest,
    HubResponse,
    TokenCount,
    ToolDefinition,
    finalize_response,
)
from completion_bridge.models.parameters import Hyperparameters
from completion_bridge.models.tools import ToolCallback
from completion_bridge.runtime.execution import RuntimeExecution
from completion_bridge.streaming.mapping import StreamMapper, Vocabulary, compose
from completion_bridge.streaming.merge import StreamMerger
from completion_bridge.vendors.base import NativeAccumulator, VendorAdapter
from completion_bridge.vendors.tool_adaptation import ToolAdaptation

__all__ = ["AsyncStream", "Bridge", "SyncBridge", "SyncStream"]

MessageInput = HubMessage | str | Mapping[str, Any]


def _attrs(**pairs: object) -> str:
    """빈 값을 뺀 XML-like 속성 문자열."""
    rendered = []
    for name in pairs:
        value = pairs[name]
        if value not in (None, ""):
            rendered.append(f' {name}="{html.escape(str(value), quote=True)}"')
    return "".join(rendered)


def _grounding_lines(block: GroundingBlock) -> list[str]:
    """구간-출처 그래프를 평탄화하지 않고 두 종류의 줄로 내린다."""
    lines = []
    for source in block.sources:
        lines.append(f"<source{_attrs(index=source.index, uri=source.uri, title=source.title)}/>")
    for support in block.supports:
        source_references = []
        for i in support.source_indices:
            source_references.append(str(i))
        refs = ",".join(source_references)
        attrs = _attrs(sources=refs, start=support.start_index, end=support.end_index)
        lines.append(f"<support{attrs}>{html.escape(support.text or '')}</support>")
    for query in block.search_queries:
        lines.append(f"<query>{html.escape(query)}</query>")
    return lines


class _Lowerer:
    """등록된 어휘를 차례로 적용해 블록 리스트를 요청 text로 되쓴다.

    각 어휘가 자기 블록을 text로 접어 넣고, 남은 본문 블록만 wire에 실린다. 아무 어휘도
    가져가지 않은 블록은 생략된다. 다른 벤더로 옮길 수 없는 추론 블록이 이 경로로 조용히 빠진다.
    """

    def __init__(
        self,
        vocabularies: Sequence[Vocabulary],
        vendor_name: str,
    ) -> None:
        """블록 변환에 사용할 어휘와 벤더 이름을 보관한다."""
        self._vocabularies = vocabularies
        self._vendor = vendor_name

    def lower_text(self, blocks: Any) -> str:
        """블록별 텍스트 변환 결과를 하나의 문자열로 결합한다."""
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
        parts = []
        for candidate_block in current:
            if isinstance(candidate_block, TextBlock) and candidate_block.text:
                parts.append(self._lower_citations(candidate_block))
        documents = self._lower_documents(current)
        attachments = self._lower_attachments(current)
        references = self._lower_references(current)
        if attachments:
            documents = f"{documents}\n{attachments}" if documents else attachments
        if documents:
            if parts:
                parts[0] = f"{documents}\n\n{parts[0]}"
            else:
                parts = [documents]
        if references:
            # 근거 목록은 그것이 가리키는 본문 뒤에 온다.
            if parts:
                parts[-1] = f"{parts[-1]}\n\n{references}"
            else:
                parts.append(references)
        return parts

    @staticmethod
    def _lower_citations(block: TextBlock) -> str:
        """네이티브 인용 채널이 없는 대상에서 인용 구간을 태그로 감싼다.

        어휘가 가져가지 않고 남은 인용만 여기 온다. ``CiteVocabulary``는 자기 문법의 인용을
        이미 처리하고 ``citations``를 비우므로, 남는 것은 다른 벤더의 native 인용이다. 그것을
        조용히 버리면 답변만 남고 근거가 사라진다.
        """
        if not block.citations:
            return block.text
        first = block.citations[0]
        marker = first.id or first.document_title or first.source or "citation"
        return f'<cite id="{html.escape(str(marker), quote=True)}">{block.text}</cite>'

    @staticmethod
    def _lower_references(blocks: list[ContentBlock]) -> str:
        """annotation과 grounding을 본문 뒤 참고 목록으로 내린다.

        둘은 본문 구간을 감쌀 수 없다. annotation은 대상 text보다 늦게 도착할 수 있어 독립
        블록으로 두고, grounding은 한 구간이 여러 출처에 걸리는 그래프라 단일 인용 목록으로
        평탄화하면 관계가 사라진다. 그래서 감싸지 않고 뒤에 붙인다.
        """
        lines: list[str] = []
        for block in blocks:
            if isinstance(block, AnnotationBlock):
                ident = block.id if block.id and block.id != block.uri else None
                attrs = _attrs(id=ident, kind=block.kind, uri=block.uri, title=block.title)
                if block.text:
                    lines.append(f"<reference{attrs}>{html.escape(block.text)}</reference>")
                else:
                    lines.append(f"<reference{attrs}/>")
            elif isinstance(block, GroundingBlock):
                lines.extend(_grounding_lines(block))
        if not lines:
            return ""
        body = "\n".join(lines)
        return f"<references>\n{body}\n</references>"

    def _lower_attachments(self, blocks: list[ContentBlock]) -> str:
        """네이티브 채널이 없어 못 내려간 이미지·음성을 태그로 남긴다.

        지금까지 이것들은 조용히 사라졌다. 호출자는 보냈다고 믿고 모델은 받지 못한다.
        원본을 전달할 수 없는 미디어에는 전달 불가 표시를 남긴다.
        """
        lines: list[str] = []
        for block in blocks:
            if isinstance(block, ImageBlock):
                tag, media = "image", block.media_type
            elif isinstance(block, AudioBlock):
                tag, media = (
                    "audio",
                    block.media_type or (f"audio/{block.format}" if block.format else None),
                )
            else:
                continue
            attrs = _attrs(**{"media-type": media})
            lines.append(f'<{tag}{attrs} unavailable="true">{UNAVAILABLE}</{tag}>')
        if not lines:
            return ""
        body = "\n".join(lines)
        return f"<attachments>\n{body}\n</attachments>"

    def _lower_documents(self, blocks: list[ContentBlock]) -> str:
        """네이티브 문서 채널이 없는 벤더에서 문서를 본문에 실는다.

        허브 수준 기본 동작이다. 어휘에 맡기지 않는 이유는 사용자가 준 근거가 조용히 사라지는
        것이 최악의 결과이기 때문이다. 아무도 가져가지 않은 문서가 버려지면 모델은 근거 없이
        답하고, 호출자는 문서를 보냈다고 믿는다.

        네이티브 채널이 있는 어댑터는 ``build_body``에서 문서를 먼저 빼내므로 여기까지 오지
        않는다. 중복되지 않는다.

        문서를 본문보다 앞에 둔다. 네이티브 채널도 그 순서이고, 모델이 근거를 먼저 읽는다.

        :class:`SearchResultBlock`도 같은 ``<documents>`` 묶음에 블록 순서대로 들어간다.
        """
        documents = []
        for candidate_block in blocks:
            if isinstance(candidate_block, DocumentBlock | SearchResultBlock):
                documents.append(candidate_block)
        if not documents:
            return ""
        document_texts = []
        for document in documents:
            document_texts.append(document.to_prompt())
        rendered = "\n".join(document_texts)
        return f"<documents>\n{rendered}\n</documents>"


class _Pipeline:
    """벤더 어댑터와 어휘 올림 매퍼를 이어 만든 변환 파이프라인.

    상태를 가진 매퍼가 섞여 있으므로 스트림마다 새로 만든다.
    """

    def __init__(self, vendor: VendorAdapter, vocabularies: Sequence[Vocabulary]) -> None:
        """벤더 매퍼와 활성화된 어휘 매퍼를 스트림별 변환기로 구성한다."""
        stages: list[StreamMapper[Any, Any]] = [vendor.to_hub()]
        for vocabulary in vocabularies:
            lift = vocabulary.lift_mapper()
            if lift is not None:
                stages.append(lift)
        self._mapper = compose(*stages)

    def map(self, chunk: Any) -> list[HubResponse]:
        """벤더 이벤트를 등록된 변환 단계에 적용하고 허브 델타 목록을 반환한다."""
        return list(self._mapper.map(chunk))

    def flush(self) -> list[HubResponse]:
        """각 변환 단계의 잔여 출력을 순서대로 처리한다."""
        return list(self._mapper.flush())


class _StreamBase:
    """모델 스트림의 델타 전달과 최종 응답 누적을 함께 관리한다.

    호출자에게 전달하는 각 델타를 병합기에도 적용한다. 상태를 공유하므로 하나의
    스트림을 여러 소비자가 동시에 순회하는 사용은 지원하지 않는다.
    """

    def __init__(self, vendor: VendorAdapter, vocabularies: Sequence[Vocabulary]) -> None:
        """스트림별 변환기·병합기와 원본 응답·로깅 상태를 초기화한다."""
        self._vendor = vendor
        self._pipeline = _Pipeline(vendor, vocabularies)
        self._merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
        self._closed = False
        self._wire: WireLog | None = None
        self._native: NativeAccumulator[BaseModel] | None = None
        self._native_failure: Exception | None = None
        self._operation = OperationLog("completion")
        self._claimed = False

    @property
    def result(self) -> HubResponse | None:
        """마지막 확정 응답. 첫 델타 성공 전에는 None이며 종료 상태와 무관하게 조회한다."""
        response = self._merger.result
        return finalize_response(response) if response is not None else None

    def _process(self, frame: SseEvent) -> tuple[list[HubResponse], bool]:
        """프레임 하나를 델타 리스트와 종료 여부로 바꾼다.

        종료 프레임도 먼저 해석한다. 마지막 프레임이 알맹이를 싣는 벤더가 있다. OpenAI
        Responses의 ``response.completed``에 ``stop_reason``과 ``usage``가 들어 있고,
        종료라고 바로 버리면 그 둘을 잃는다. ``[DONE]``처럼 알맹이가 없는 표지는 ``decode``가
        ``None``을 돌려주므로 그냥 지나간다.
        """
        if self._wire is not None:
            self._wire.sequence += 1
            self._wire.emit("sse", lambda: asdict(frame))
        if frame.is_comment_only:
            # 하트비트다. 흘려보낸다.
            return [], False
        terminal = self._vendor.is_terminal(frame)
        chunk = self._vendor.decode(frame)
        if chunk is None:
            return [], terminal
        self._accumulate(chunk)
        return self._pipeline.map(chunk), terminal

    def _accumulate(self, chunk: Any) -> None:
        """원본 응답 누적기에 chunk의 깊은 복사본을 넘긴다.

        허브 매퍼가 chunk의 하위 객체를 참조한 채 보관하므로 누적기에는 복사본을 준다. 누적이
        실패하면 예외를 보관하고 이후 chunk를 넘기지 않는다. 그 예외는 스트림 소비로 전파하지
        않고 :meth:`_log_end`의 ``merge_error`` 기록에만 쓴다.
        """
        if self._native is None or self._native_failure is not None:
            return
        try:
            self._native.apply(copy.deepcopy(chunk))
        except Exception as exc:
            self._native_failure = exc

    def _log_start(self) -> None:
        """요청을 기록하고, 요청·응답 로그가 켜져 있으면 원본 응답 누적기를 만든다."""
        if self._wire is not None:
            self._wire.start()
        self._operation.start(vendor=self._vendor.name)
        if self._wire is not None:
            if self._native is None and self._wire.calls_enabled():
                try:
                    self._native = self._vendor.to_native()
                except Exception as exc:
                    self._native_failure = exc

    def _log_end(self, event: str, error: BaseException | None = None) -> None:
        """종료 기록을 남긴다. 호출당 첫 기록만 남는다.

        정상 종료(``response``)의 payload는 원본 응답 누적기가 만든 객체다. 누적기가 없으면
        소비 시작 시점에 요청·응답 로그가 꺼져 있었으므로 ``None``이다. 누적이나 객체 완성이
        실패하면 ``response`` 대신 ``merge_error``를 예외 클래스 이름과 메시지로 기록한다.
        ``error``와 ``cancelled``의 payload는 예외 클래스 이름 또는 빈 객체다.
        """
        level = logging.ERROR if event == "error" else logging.INFO
        self._operation.finish(
            event,
            level=level,
            error_type=type(error).__name__ if error is not None else None,
        )
        if self._wire is None:
            return
        if event != "response":
            self._wire.finish(
                event,
                lambda: {"error_type": type(error).__name__} if error is not None else {},
            )
            return

        # 정상 종료: 누적한 원본 응답 객체를 완성한다.
        merged: BaseModel | None = None
        if self._native is not None and self._native_failure is None:
            try:
                merged = self._native.build()
            except Exception as exc:
                self._native_failure = exc

        failure = self._native_failure
        if failure is not None:
            self._operation.emit(
                logging.WARNING,
                "native_merge_failed",
                error_type=type(failure).__name__,
                recovery="hub_response_preserved",
            )
            self._wire.finish(
                "merge_error",
                lambda: {"error_type": type(failure).__name__, "message": str(failure)},
            )
        else:
            self._wire.finish(
                "response",
                lambda: (
                    merged.model_dump(mode="json", by_alias=True, exclude_unset=True)
                    if merged is not None
                    else None
                ),
            )

    def _accept(self, delta: HubResponse) -> None:
        """결과 정렬에 필요한 좌표를 확인한 뒤 델타를 검증·확정한다."""
        for block in delta.content:
            if block.seq is None:
                raise MappingError(f"content block {block.type!r} has no seq")
        self._merger.apply(delta)

    def _finish(self) -> list[HubResponse]:
        """변환기에 남은 델타를 반환한다. 호출자가 각 델타를 확정한 직후 전달한다."""
        return self._pipeline.flush()


class AsyncStream(_StreamBase):
    """``async for``로 델타를 소비하는 스트림."""

    def __init__(
        self,
        vendor: VendorAdapter,
        vocabularies: Sequence[Vocabulary],
        frames: AsyncIterator[SseEvent],
    ) -> None:
        super().__init__(vendor, vocabularies)
        self._frames = frames

    def __aiter__(self) -> AsyncIterator[HubResponse]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[HubResponse]:
        """SSE 프레임을 허브 델타로 전달하며 종료·실패 시 모델 연결을 정리한다."""
        if self._claimed or self._closed:
            raise ExecutionStateError("A model stream can only be consumed once")
        self._claimed = True
        self._log_start()
        try:
            async for frame in self._frames:
                deltas, terminal = self._process(frame)
                for delta in deltas:
                    self._accept(delta)
                    yield delta
                if terminal:
                    break
            for delta in self._finish():
                self._accept(delta)
                yield delta
            self._log_end("response")
        except BaseException as exc:
            self._log_end("error" if isinstance(exc, Exception) else "cancelled", exc)
            raise
        finally:
            await self._release(sys.exception())

    async def aclose(self) -> None:
        """스트림을 끊는다. 매퍼를 flush해 부분 결과를 :attr:`result`에 남긴다."""
        if self._closed:
            return
        try:
            for delta in self._finish():
                self._accept(delta)
        except Exception as exc:
            self._log_end("error", exc)
            raise
        finally:
            await self._release(sys.exception())

    async def _release(self, error: BaseException | None = None) -> None:
        """연결을 한 번만 정리하고 프레임 공급기의 비동기 종료를 수행한다."""
        if self._closed:
            return
        self._closed = True
        self._log_end("cancelled")
        try:
            aclose = getattr(self._frames, "aclose", None)
            if aclose is not None:
                await aclose()
        except Exception as cleanup_error:
            self._operation.emit(
                logging.WARNING if error is not None else logging.ERROR,
                "cleanup_failed",
                error_type=type(cleanup_error).__name__,
                recovery="original_error_preserved" if error is not None else "caller_notified",
            )
            if error is None:
                raise


class SyncStream(_StreamBase):
    """``for``로 델타를 소비하는 스트림."""

    def __init__(
        self,
        vendor: VendorAdapter,
        vocabularies: Sequence[Vocabulary],
        frames: Iterator[SseEvent],
    ) -> None:
        super().__init__(vendor, vocabularies)
        self._frames = frames

    def __iter__(self) -> Iterator[HubResponse]:
        if self._claimed or self._closed:
            raise ExecutionStateError("A model stream can only be consumed once")
        self._claimed = True
        self._log_start()
        try:
            for frame in self._frames:
                deltas, terminal = self._process(frame)
                for delta in deltas:
                    self._accept(delta)
                    yield delta
                if terminal:
                    break
            for delta in self._finish():
                self._accept(delta)
                yield delta
            self._log_end("response")
        except BaseException as exc:
            self._log_end("error" if isinstance(exc, Exception) else "cancelled", exc)
            raise
        finally:
            self._release(sys.exception())

    def close(self) -> None:
        """스트림을 끊는다."""
        if self._closed:
            return
        try:
            for delta in self._finish():
                self._accept(delta)
        except Exception as exc:
            self._log_end("error", exc)
            raise
        finally:
            self._release(sys.exception())

    def _release(self, error: BaseException | None = None) -> None:
        """연결을 한 번만 정리하고 프레임 공급기의 동기 종료를 수행한다."""
        if self._closed:
            return
        self._closed = True
        self._log_end("cancelled")
        try:
            close = getattr(self._frames, "close", None)
            if close is not None:
                close()
        except Exception as cleanup_error:
            self._operation.emit(
                logging.WARNING if error is not None else logging.ERROR,
                "cleanup_failed",
                error_type=type(cleanup_error).__name__,
                recovery="original_error_preserved" if error is not None else "caller_notified",
            )
            if error is None:
                raise


@runtime_checkable
class _ModelBindable(Protocol):
    """모델별 경로가 필요한 어댑터의 선택적 계약. 원본을 변경하지 않는다."""

    def for_model(self, model: str) -> VendorAdapter:
        """지정한 모델을 대상으로 하는 어댑터를 반환한다."""
        ...


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
        """벤더·모델·인증·기본 설정을 보관하고 어휘의 블록 타입을 등록한다."""
        self._vendor = (
            vendor.for_model(model) if model and isinstance(vendor, _ModelBindable) else vendor
        )
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
        """기본 URL과 현재 벤더 경로로 요청 URL을 만든다."""
        return f"{self._base_url}{self._vendor.path}"

    def _request_vendor(self, model: str | None) -> VendorAdapter:
        """호출별 모델 설정이 있으면 해당 모델에 맞춘 어댑터를 반환한다."""
        if model and isinstance(self._vendor, _ModelBindable):
            return self._vendor.for_model(model)
        return self._vendor

    def _request_url(self, model: str | None) -> str:
        """호출별 모델을 반영한 벤더 경로로 요청 URL을 만든다."""
        return f"{self._base_url}{self._request_vendor(model).path}"

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
        normalized_messages = []
        for input_message in messages:
            normalized_messages.append(_coerce_message(input_message))
        _reject_remote_content_references(normalized_messages, self._vendor.name)
        converted_normalized_messages = []
        for message in normalized_messages:
            converted_normalized_messages.append(
                message.model_copy(
                    update={"content": ToolAdaptation.lower(message.content, self._vendor.name)}
                )
            )
        normalized_messages = converted_normalized_messages
        request = HubRequest(
            model=model or self._model,
            messages=normalized_messages,
            tools=list(tools),
            hyperparameters=self._hyperparameters.merged(hyperparameters),
            params=dict(params),
        )
        return self._vendor.build_body(request, self._lowerer)

    def _request_headers(self, *, accept: str = "text/event-stream") -> dict[str, str]:
        """전송 헤더를 조립한다.

        우선순위가 셋이다. 기본값, 어댑터가 요구하는 것, 호출자가 준 것 순으로 덮인다.
        Anthropic의 ``anthropic-version``처럼 벤더가 요구하는 헤더가 있고, 사내 게이트웨이의
        CSRF 토큰처럼 호출자만 아는 것이 있다.

        ``accept``는 ``Accept``의 기본값이다. 스트리밍 요청은 ``text/event-stream``, 토큰 수
        측정 요청은 ``application/json``을 쓴다.
        """
        headers = httpx.Headers(
            {
                "Accept": accept,
                "Content-Type": "application/json",
            }
        )
        vendor_headers = getattr(self._vendor, "request_headers", None)
        if callable(vendor_headers):
            headers.update(cast(Mapping[str, str], vendor_headers()))
        if self._api_key:
            api_key_header = getattr(self._vendor, "api_key_header", "Authorization")
            if str(api_key_header).lower() == "authorization":
                headers["Authorization"] = f"Bearer {self._api_key}"
            else:
                headers[str(api_key_header)] = self._api_key
        headers.update(self._headers)
        return dict(headers)


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
        """비동기 브리지를 구성하고 주입한 HTTP 클라이언트의 소유권을 구분한다."""
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
        self._client = AsyncHttpClient(http_client, timeout=timeout)

    def stream(
        self,
        messages: Sequence[MessageInput],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        request_id: str | None = None,
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
        frames = self._client.stream_sse(
            self._request_url(model), json=body, headers=self._request_headers()
        )
        stream = AsyncStream(self._vendor, self._vocabularies, frames)
        stream._wire = WireLog(
            request_id,
            self._vendor.name,
            model or self._model,
            self._request_url(model),
            body,
        )
        stream._operation = OperationLog("completion", request_id=stream._wire.request_id)
        return stream

    def run(
        self,
        request: HubRequest,
        *,
        execute_tool: ToolCallback,
        max_tool_calls: int = 32,
    ) -> RuntimeExecution:
        """콜백의 도구 결과로 모델 요청을 반복하며 델타와 실행 기록을 제공한다.

        입력을 즉시 복사하고 첫 비동기 소비 또는 await 때 실행한다.
        콜백이 모델용 결과를 제공하지 않으면 HITL 기록을 반환하고 종료한다.
        정책·도구 연결·백그라운드 작업 생성은 콜백이 담당한다.
        """
        return RuntimeExecution(self.stream, request, execute_tool, max_tool_calls)

    async def complete(
        self,
        messages: Sequence[MessageInput],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        request_id: str | None = None,
        **params: Any,
    ) -> HubResponse | None:
        """스트림을 끝까지 돌려 병합 결과만 돌려준다."""
        stream = self.stream(
            messages,
            request_id=request_id,
            tools=tools,
            model=model,
            hyperparameters=hyperparameters,
            **params,
        )
        async for _ in stream:
            pass
        return stream.result

    async def count_tokens(
        self,
        messages: Sequence[MessageInput],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        **params: Any,
    ) -> TokenCount:
        """생성 요청과 같은 입력으로 입력 토큰 수를 잰다. 텍스트는 생성하지 않는다.

        :meth:`build_request`로 만든 생성 body를 어댑터의 ``token_count_calls``에 넘기고,
        어댑터가 내놓는 요청을 순서대로 보낸다. ``chat_completions``(vLLM)는 chat template
        적용 결과를 ``tokenized``에 채우고, 다른 어댑터는 ``None``으로 둔다.

        Raises:
            TransportError: 토큰 수 엔드포인트가 2xx가 아닌 상태를 돌려줄 때.
            MappingError: 응답이 JSON 객체가 아니거나 어댑터가 기대한 필드가 없을 때.
        """
        operation = OperationLog("count_tokens")
        operation.start(vendor=self._vendor.name)
        outcome = "completed"
        level = logging.INFO
        error_type = None
        try:
            body = self.build_request(
                messages,
                tools=tools,
                model=model,
                hyperparameters=hyperparameters,
                **params,
            )
            headers = self._request_headers(accept="application/json")
            calls = self._request_vendor(model).token_count_calls(body)
            try:
                call = next(calls)
                while True:
                    payload = await self._client.post_json(
                        f"{self._base_url}{call.path}", json=call.body, headers=headers
                    )
                    call = calls.send(payload)
            except StopIteration as finished:
                result: TokenCount = finished.value
                return result
        except BaseException as exc:
            outcome = "error" if isinstance(exc, Exception) else "cancelled"
            level = logging.ERROR if isinstance(exc, Exception) else logging.INFO
            error_type = type(exc).__name__
            raise
        finally:
            operation.finish(outcome, level=level, error_type=error_type)

    async def aclose(self) -> None:
        """직접 만든 클라이언트만 닫는다. 주입받은 것은 호출자 소관이다."""
        await self._client.aclose()

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
        """동기 브리지를 구성하고 주입한 HTTP 클라이언트의 소유권을 구분한다."""
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
        self._client = HttpClient(http_client, timeout=timeout)

    def stream(
        self,
        messages: Sequence[MessageInput],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        request_id: str | None = None,
        **params: Any,
    ) -> SyncStream:
        """입력으로 요청을 구성하고 소비 시 전송을 시작하는 동기 스트림을 반환한다."""
        body = self.build_request(
            messages,
            tools=tools,
            model=model,
            hyperparameters=hyperparameters,
            **params,
        )
        frames = self._client.stream_sse(
            self._request_url(model), json=body, headers=self._request_headers()
        )
        stream = SyncStream(self._vendor, self._vocabularies, frames)
        stream._wire = WireLog(
            request_id,
            self._vendor.name,
            model or self._model,
            self._request_url(model),
            body,
        )
        stream._operation = OperationLog("completion", request_id=stream._wire.request_id)
        return stream

    def complete(
        self,
        messages: Sequence[MessageInput],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        request_id: str | None = None,
        **params: Any,
    ) -> HubResponse | None:
        """동기 스트림을 끝까지 소비하고 병합된 최종 허브 응답을 반환한다."""
        stream = self.stream(
            messages,
            request_id=request_id,
            tools=tools,
            model=model,
            hyperparameters=hyperparameters,
            **params,
        )
        for _ in stream:
            pass
        return stream.result

    def count_tokens(
        self,
        messages: Sequence[MessageInput],
        *,
        tools: Sequence[ToolDefinition] = (),
        model: str | None = None,
        hyperparameters: Hyperparameters | Mapping[str, Any] | None = None,
        **params: Any,
    ) -> TokenCount:
        """:meth:`Bridge.count_tokens`의 동기판."""
        operation = OperationLog("count_tokens")
        operation.start(vendor=self._vendor.name)
        outcome = "completed"
        level = logging.INFO
        error_type = None
        try:
            body = self.build_request(
                messages,
                tools=tools,
                model=model,
                hyperparameters=hyperparameters,
                **params,
            )
            headers = self._request_headers(accept="application/json")
            calls = self._request_vendor(model).token_count_calls(body)
            try:
                call = next(calls)
                while True:
                    payload = self._client.post_json(
                        f"{self._base_url}{call.path}", json=call.body, headers=headers
                    )
                    call = calls.send(payload)
            except StopIteration as finished:
                result: TokenCount = finished.value
                return result
        except BaseException as exc:
            outcome = "error" if isinstance(exc, Exception) else "cancelled"
            level = logging.ERROR if isinstance(exc, Exception) else logging.INFO
            error_type = type(exc).__name__
            raise
        finally:
            operation.finish(outcome, level=level, error_type=error_type)

    def close(self) -> None:
        """브리지가 직접 생성한 동기 HTTP 클라이언트만 종료한다."""
        self._client.close()

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
        """중첩된 매핑과 목록에 외부 저장 참조 식별자가 있는지 확인한다."""
        if isinstance(value, Mapping):
            for key in value:
                nested = value[key]
                if key in {"file_id", "container_id"} and nested:
                    return True
                if has_remote_key(nested):
                    return True
        elif isinstance(value, list | tuple):
            for item in value:
                if has_remote_key(item):
                    return True
            return False
        return False

    def check(block: ContentBlock) -> None:
        """다른 벤더에서 재사용할 수 없는 저장 참조를 가진 블록이면 예외를 발생시킨다."""
        same_or_unscoped = block.vendor is None or block.vendor == target
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
            and block.vendor == target
            and (has_remote_key(block.raw) or has_remote_key(block.native))
        ):
            raise MappingError(
                "remote file/container references cannot be replayed; materialize them as URL "
                "or inline data"
            )

    for message in messages:
        for block in message.content:
            check(block)
