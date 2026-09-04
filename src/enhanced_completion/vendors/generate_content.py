"""Gemini GenerateContent 어댑터.

**이 벤더는 content part에 판별자가 없다.** ``Part``에 ``text``, ``functionCall``,
``executableCode``가 나란히 놓이고 채워진 필드로 종류를 알아낸다. 다른 세 벤더가 모두
``type`` 태그를 쓰는데 여기만 다르다. 그래서 판정 순서가 계약이 된다.

추론도 별도 종류가 아니라 ``thought`` 불리언이다. ``text``에 ``thought: true``가 붙으면
추론이고 아니면 본문이다. 같은 필드가 두 채널을 나른다.

이름 붙은 SSE 이벤트를 쓰지 않는다. 종료 표지도 없다. ``finishReason``이 실린 프레임이
마지막이다.

경로에 모델명과 API key가 들어가므로 :attr:`path`가 요청마다 달라진다. 브리지가 고정 경로를
쓰므로 어댑터를 모델별로 만든다.
"""

from __future__ import annotations

import json
from typing import Any

from ..blocks import (
    CitationBlock,
    ContentBlock,
    ServerToolBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
)
from ..errors import MappingError
from ..hub import HubRequest, HubResponse, Usage
from ..mapper import StreamMapper
from ..transport.sse import SseFrame
from .base import Lowerer
from .parts import as_gemini_part

__all__ = ["GenerateContentAdapter", "generate_content"]

SOURCE = "generate_content"

# 허브에 대응물이 있는 Part 필드. 판정 순서가 계약이다.
_TEXT = "text"
_FUNCTION_CALL = "functionCall"
_FUNCTION_RESPONSE = "functionResponse"

# 서버가 실행한 코드. 클라이언트가 결과를 되보내지 않는다.
_SERVER_TOOL_FIELDS = ("executableCode", "codeExecutionResult")

# 허브에 대응물이 없어 원본을 보존하는 Part 필드.
_VENDOR_FIELDS = ("inlineData", "fileData", "videoMetadata")

_STOP_REASONS = {"STOP": "stop", "MAX_TOKENS": "length"}


def _parse_args(raw: str) -> dict[str, Any]:
    """도구 인수를 객체로. 이 API는 문자열이 아니라 객체를 요구한다."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


class _ToHub:
    """GenerateContent 응답을 허브 델타로 바꾼다.

    본문과 추론에 고정 인덱스를 주고 도구 호출은 도착 순서대로 자리를 잡는다. 이 API는
    part 인덱스를 주지 않으므로 어댑터가 만들어야 한다.
    """

    TEXT_INDEX = 0
    THINKING_INDEX = -1
    TOOL_INDEX_BASE = 1

    def __init__(self) -> None:
        self._tool_index = self.TOOL_INDEX_BASE

    def map(self, chunk: dict[str, Any]) -> list[HubResponse]:
        candidates = chunk.get("candidates") or []
        head: dict[str, Any] = candidates[0] if candidates else {}
        content = head.get("content") or {}

        blocks: list[ContentBlock] = []
        for part in content.get("parts") or []:
            if isinstance(part, dict):
                block = self._part(part)
                if block is not None:
                    blocks.append(block)

        blocks.extend(self._citations(head.get("citationMetadata")))
        blocks.extend(self._candidate_meta(head))

        fields: dict[str, Any] = {}
        if isinstance(chunk.get("modelVersion"), str):
            fields["model"] = chunk["modelVersion"]
        if isinstance(chunk.get("responseId"), str):
            fields["id"] = chunk["responseId"]
        role = content.get("role")
        if isinstance(role, str) and role:
            # Gemini는 assistant를 model이라 부른다. 허브 어휘로 맞춘다.
            fields["role"] = "assistant" if role == "model" else role
        reason = head.get("finishReason")
        if isinstance(reason, str) and reason:
            fields["stop_reason"] = _STOP_REASONS.get(reason, reason.lower())
        usage = self._usage(chunk.get("usageMetadata"))
        if usage is not None:
            fields["usage"] = usage

        if not blocks and not fields:
            return []
        return [HubResponse(content=blocks, **fields)]

    def flush(self) -> list[HubResponse]:
        return []

    def _citations(self, metadata: Any) -> list[ContentBlock]:
        """``citationMetadata``를 허브 :class:`CitationBlock`으로.

        이 벤더의 ``citationSources``는 ``startIndex``/``endIndex``가 **답변 문자열 안의
        위치**다. Anthropic이 원문 좌표를 주는 것과 축이 반대이므로 이쪽은 답변 좌표를 채운다.

        인용이 part가 아니라 candidate 메타데이터에 실린다. part만 훑으면 통째로 놓친다.
        """
        if not isinstance(metadata, dict):
            return []
        out: list[ContentBlock] = []
        for source in metadata.get("citationSources") or []:
            if not isinstance(source, dict):
                continue
            fields: dict[str, Any] = {"source": SOURCE, "source_kind": "citation_source"}
            uri = source.get("uri")
            if isinstance(uri, str) and uri:
                fields["id"] = uri
            for key, dst in (("startIndex", "start_index"), ("endIndex", "end_index")):
                value = source.get(key)
                if isinstance(value, int):
                    fields[dst] = value
            out.append(CitationBlock(**fields))
        return out

    def _candidate_meta(self, candidate: dict[str, Any]) -> list[ContentBlock]:
        """허브에 대응물이 없는 candidate 메타데이터를 보존한다.

        ``groundingMetadata``는 검색 근거, ``safetyRatings``는 안전 등급,
        ``urlContextMetadata``는 URL 조회 결과다. 셋 다 이 벤더 전용이라 다른 벤더로 옮길 수
        없지만, 같은 벤더로 되돌릴 때는 무손실이어야 한다.
        """
        out: list[ContentBlock] = []
        for key in ("groundingMetadata", "urlContextMetadata"):
            value = candidate.get(key)
            if isinstance(value, dict):
                # 서버가 검색을 돌린 결과다. 다른 벤더의 서버 도구와 같은 자리다.
                out.append(ServerToolBlock(name=key, raw=value, source=SOURCE))
        ratings = candidate.get("safetyRatings")
        if isinstance(ratings, list) and ratings:
            out.append(VendorBlock(type="safetyRatings", raw={"ratings": ratings}, source=SOURCE))
        return out

    def _part(self, part: dict[str, Any]) -> ContentBlock | None:
        """채워진 필드로 종류를 알아낸다. 판별자가 없어 순서가 계약이다."""
        text = part.get(_TEXT)
        if isinstance(text, str) and text:
            # 같은 필드가 두 채널을 나른다. thought 불리언이 갈림길이다.
            if part.get("thought"):
                fields: dict[str, Any] = {"thinking": text, "index": self.THINKING_INDEX}
                signature = part.get("thoughtSignature")
                if isinstance(signature, str) and signature:
                    fields["signature"] = signature
                return ThinkingBlock(source=SOURCE, **fields)
            return TextBlock(text=text, index=self.TEXT_INDEX, source=SOURCE)

        call = part.get(_FUNCTION_CALL)
        if isinstance(call, dict):
            return self._call(call)

        response = part.get(_FUNCTION_RESPONSE)
        if isinstance(response, dict):
            # 도구 결과는 요청 쪽 어휘다. 응답에서 오면 원본을 보존한다.
            return VendorBlock(type="function_response", raw=response, source=SOURCE)

        for field in _SERVER_TOOL_FIELDS:
            value = part.get(field)
            if isinstance(value, dict):
                return ServerToolBlock(
                    name=field,
                    input_json=json.dumps(value, ensure_ascii=False)
                    if field == "executableCode"
                    else "",
                    output=str(value.get("output", "")) if field == "codeExecutionResult" else "",
                    raw=value,
                    source=SOURCE,
                )
        for field in _VENDOR_FIELDS:
            value = part.get(field)
            if isinstance(value, dict):
                return VendorBlock(type=field, raw=value, source=SOURCE)
        return None

    def _call(self, call: dict[str, Any]) -> ToolUseBlock:
        index = self._tool_index
        self._tool_index += 1
        fields: dict[str, Any] = {"index": index, "source": SOURCE}
        if call.get("id"):
            fields["id"] = call["id"]
        if call.get("name"):
            fields["name"] = call["name"]
        args = call.get("args")
        if isinstance(args, (dict, list)):
            # 이 API는 인수를 조각으로 쪼개지 않고 완성된 객체로 준다.
            fields["input_json"] = json.dumps(args, ensure_ascii=False)
        return ToolUseBlock(**fields)

    @staticmethod
    def _usage(raw: Any) -> Usage | None:
        if not isinstance(raw, dict):
            return None
        fields: dict[str, Any] = {}
        if isinstance(raw.get("promptTokenCount"), int):
            fields["input_tokens"] = raw["promptTokenCount"]
        if isinstance(raw.get("candidatesTokenCount"), int):
            fields["output_tokens"] = raw["candidatesTokenCount"]
        return Usage(**fields) if fields else None


class GenerateContentAdapter:
    """``POST {base_url}/v1beta/models/{model}:streamGenerateContent?alt=sse``.

    경로에 모델명이 들어가므로 모델별로 어댑터를 만든다. ``for_model``이 그 일을 한다.

    API key는 ``x-goog-api-key`` 헤더로 보낸다. ``Bridge(api_key=...)``는
    ``Authorization: Bearer``를 쓰므로 이 어댑터는 자기 헤더를 따로 요구한다.
    """

    name = SOURCE

    def __init__(self, *, model: str = "", api_key: str = "", version: str = "v1beta") -> None:
        self.model = model
        self.api_key = api_key
        self.version = version

    @property
    def path(self) -> str:
        model = self.model or "{model}"
        return f"/{self.version}/models/{model}:streamGenerateContent?alt=sse"

    def for_model(self, model: str, *, api_key: str = "") -> GenerateContentAdapter:
        return GenerateContentAdapter(
            model=model,
            api_key=api_key or self.api_key,
            version=self.version,
        )

    def request_headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key} if self.api_key else {}

    def build_body(self, request: HubRequest, lowerer: Lowerer) -> dict[str, Any]:
        """허브 요청을 GenerateContent body로.

        이 API는 ``messages``가 아니라 ``contents``를 받고 role ``assistant``를 ``model``이라
        부른다. system은 ``systemInstruction``으로 따로 나간다.
        """
        system: list[str] = []
        contents: list[dict[str, Any]] = []
        for message in request.messages:
            # ``parts``는 리스트다. 텍스트 하나로 누르면 inlineData와 fileData를 실을 수 없다.
            parts: list[dict[str, Any]] = []
            plain: list[ContentBlock] = []
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    # 도구 결과는 별개 Part다. 이름을 잃지 않으려면 호출 식별자를 쓴다.
                    parts.append(
                        {
                            "functionResponse": {
                                "name": block.tool_use_id,
                                "response": {"result": block.content},
                            }
                        }
                    )
                    continue
                if isinstance(block, ToolUseBlock):
                    parts.append(
                        {
                            "functionCall": {
                                "name": block.name,
                                "args": _parse_args(block.input_json),
                            }
                        }
                    )
                    continue
                part = as_gemini_part(block)
                if part is not None:
                    parts.append(part)
                else:
                    plain.append(block)

            text = lowerer.lower_text(plain)
            if message.role == "system":
                if text:
                    system.append(text)
                continue
            if text:
                parts.append({"text": text})
            if not parts:
                continue
            role = "model" if message.role == "assistant" else message.role
            contents.append({"role": role, "parts": parts})

        params = dict(request.params)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
        if request.tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.input_schema,
                        }
                        for t in request.tools
                    ]
                }
            ]

        # 생성 파라미터가 generationConfig 안에 들어간다. 다른 셋은 최상위다.
        config = dict(params.pop("generationConfig", {}) or {})
        for key in ("temperature", "topP", "topK", "maxOutputTokens", "stopSequences", "seed"):
            if key in params:
                config[key] = params.pop(key)
        # 허브 어휘를 이 API 이름으로 옮긴다.
        if "max_tokens" in params:
            config["maxOutputTokens"] = params.pop("max_tokens")
        if "top_p" in params:
            config["topP"] = params.pop("top_p")
        if config:
            body["generationConfig"] = config

        for key, value in params.items():
            if key not in ("contents", "tools", "systemInstruction"):
                body[key] = value
        return body

    def is_terminal(self, frame: SseFrame) -> bool:
        """종료 표지가 없다. 스트림이 끊기면 끝이다."""
        _ = frame
        return False

    def decode(self, frame: SseFrame) -> dict[str, Any] | None:
        payload = frame.data.strip()
        if not payload or payload == "[DONE]":
            return None
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise MappingError("generate_content chunk is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise MappingError("generate_content chunk must be a JSON object")
        if "error" in parsed:
            detail = json.dumps(parsed["error"], ensure_ascii=False)[:200]
            raise MappingError(f"generate_content stream reported an error: {detail}")
        return parsed

    def to_hub(self) -> StreamMapper[Any, Any]:
        return _ToHub()


generate_content = GenerateContentAdapter()
"""기본 인스턴스. ``for_model(name, api_key=...)``로 대상을 지정한다."""
