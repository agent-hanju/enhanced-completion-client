"""세 외부 벤더 대상 라이브 시험.

``.env``에 키를 채운 벤더만 돈다. 하나만 채워도 그 벤더는 검증된다.

    cp .env.example .env      # 값을 채운다
    uv run pytest -m live -s

느린 모델을 빼려면 ``-m "live and not slow"``를 쓴다.

벤더마다 확인하는 것이 같다. 델타를 이어붙인 것과 병합 결과가 일치하는지, 응답을 다음 요청의
이력으로 되쓸 수 있는지다. 어댑터가 다르고 허브가 같다는 것이 이 시험의 요점이다.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest
from _dense import DENSE_TOOL, dense_history

from enhanced_completion import (
    Bridge,
    CitationBlock,
    CiteVocabulary,
    DocumentBlock,
    HubMessage,
    HubResponse,
    TextBlock,
    ToolDefinition,
    ToolUseBlock,
)
from enhanced_completion.vendors import generate_content, messages, responses

pytestmark = pytest.mark.live

SHORT = 32
TIMEOUT = 180.0


def _env(prefix: str) -> tuple[str, str, str] | None:
    """``BASE_URL``, ``MODEL``, ``API_KEY`` 셋이 모두 있으면 돌려준다."""
    base = os.getenv(f"ECC_{prefix}_BASE_URL", "")
    model = os.getenv(f"ECC_{prefix}_MODEL", "")
    key = os.getenv(f"ECC_{prefix}_API_KEY", "")
    if base and model and key:
        return base, model, key
    return None


ANTHROPIC = _env("ANTHROPIC")
RESPONSES = _env("RESPONSES")
GEMINI = _env("GEMINI")

skip_anthropic = pytest.mark.skipif(ANTHROPIC is None, reason="set ECC_ANTHROPIC_* in .env to run")
skip_responses = pytest.mark.skipif(RESPONSES is None, reason="set ECC_RESPONSES_* in .env to run")
skip_gemini = pytest.mark.skipif(GEMINI is None, reason="set ECC_GEMINI_* in .env to run")

CITE_PROMPT_DOCS = (
    '<documents><document id="d1">서울은 대한민국의 수도다.</document></documents>\n'
    "질문: 대한민국의 수도는? 한 문장으로."
)

WEATHER_TOOL = ToolDefinition(
    name="get_weather",
    description="도시의 현재 날씨를 조회한다",
    input_schema={
        "type": "object",
        "properties": {"city": {"type": "string"}},
        "required": ["city"],
    },
)


async def _check_stream(bridge: Bridge, label: str, **params: object) -> None:
    """공통 검증. 델타와 병합 결과가 일치하고 되쓸 수 있어야 한다."""
    stream = bridge.stream(["한 단어로만 답하세요: 대한민국의 수도"], **params)
    deltas = [d async for d in stream]
    result = stream.result

    assert deltas, f"[{label}] no deltas arrived"
    streamed = "".join(b.text for d in deltas for b in d.content if isinstance(b, TextBlock))
    assert streamed == result.text, f"[{label}] 델타와 병합 결과가 달라졌다"
    assert result.text.strip(), f"[{label}] empty answer"

    lowered = bridge.build_request(["질문", HubMessage.of_response(result)])
    print(f"\n[{label}] deltas={len(deltas)} stop={result.stop_reason} text={result.text!r}")
    print(f"[{label}] blocks={[b.type for b in result.content]}")
    if result.usage is not None:
        print(f"[{label}] usage in={result.usage.input_tokens} out={result.usage.output_tokens}")
    print(f"[{label}] lowered keys={sorted(lowered)}")


def _report_dense(label: str, result: HubResponse) -> None:
    """복합 입력 결과를 보고한다. 본문이든 도구 호출이든 무언가는 와야 한다."""
    kinds = [b.type for b in result.content]
    calls = [b for b in result.content if isinstance(b, ToolUseBlock)]
    print(f"[dense/{label}] stop={result.stop_reason} text={result.text!r}")
    print(f"[dense/{label}] blocks={kinds}")
    assert result.text.strip() or calls, "복합 이력에 아무 응답도 오지 않았다"


class TestAnthropicMessages:
    @skip_anthropic
    async def test_stream_and_round_trip(self) -> None:
        assert ANTHROPIC is not None
        base, model, key = ANTHROPIC
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=messages,
                base_url=base,
                model=model,
                http_client=client,
                headers={"x-api-key": key},
            )
            await _check_stream(bridge, "messages", max_tokens=SHORT)

    @skip_anthropic
    async def test_tool_call(self) -> None:
        assert ANTHROPIC is not None
        base, model, key = ANTHROPIC
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=messages,
                base_url=base,
                model=model,
                http_client=client,
                headers={"x-api-key": key},
            )
            result = await bridge.complete(
                ["서울 날씨 알려줘"], tools=[WEATHER_TOOL], max_tokens=256
            )
        calls = [b for b in result.content if isinstance(b, ToolUseBlock)]
        print(f"\n[messages] calls={[(c.name, c.input_json) for c in calls]}")
        assert calls, "도구 호출이 없다"
        assert json.loads(calls[0].input_json)

    @skip_anthropic
    async def test_native_citations_need_no_beta_header(self) -> None:
        """이 벤더는 인용을 본문 태그가 아니라 구조 채널로 준다.

        문서 블록에 ``citations``를 켜면 응답의 text 블록이 인용을 들고 온다. beta 헤더는
        필요 없다. 예전 ``citations-2025-01-31``은 GA가 되어 사라졌다.

        어휘를 등록하지 않는다. 태그 올림을 거치지 않고 어댑터가 바로 허브 블록을 만드는
        경로를 확인하는 것이 요점이다. 도착지는 태그 경로와 같은 :class:`CitationBlock`이다.
        """
        assert ANTHROPIC is not None
        base, model, key = ANTHROPIC
        message = HubMessage(
            role="user",
            content=[
                DocumentBlock(
                    id="d1",
                    title="한국 지리",
                    text="서울은 대한민국의 수도다. 부산은 제2의 도시다.",
                ),
                TextBlock(text="대한민국의 수도와 제2도시는? 문서를 근거로 한 문장으로."),
            ],
        )
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=messages,
                base_url=base,
                model=model,
                http_client=client,
                headers={"x-api-key": key},
            )
            body = bridge.build_request([message])
            result = await bridge.complete([message], max_tokens=300)

        # 요청: 문서가 네이티브 채널로 나가고 인용이 켜진다.
        wire = body["messages"][0]
        assert isinstance(wire, dict)
        blocks = wire["content"]
        assert isinstance(blocks, list)
        assert blocks[0]["type"] == "document"
        assert blocks[0]["citations"] == {"enabled": True}
        assert blocks[0]["title"] == "한국 지리"
        assert blocks[1]["type"] == "text"

        cites = [b for b in result.content if isinstance(b, CitationBlock)]
        print(f"\n[messages] text={result.text!r}")
        print(f"[messages] blocks={[b.type for b in result.content]}")
        for cite in cites:
            print(
                f"[messages] cite id={cite.id!r} kind={cite.source_kind!r} "
                f"src=[{cite.source_start}:{cite.source_end}] text={cite.text!r}"
            )

        assert result.text.strip()
        assert cites, "네이티브 인용이 오지 않았다"
        for cite in cites:
            # 원문 좌표를 채운다. 답변 좌표는 두 축이 달라 어댑터가 채우지 않는다.
            assert cite.source_kind
            assert cite.source_start is not None
            assert cite.text, "cited_text가 비었다"

    @skip_anthropic
    async def test_citation_vocabulary(self) -> None:
        assert ANTHROPIC is not None
        base, model, key = ANTHROPIC
        vocabulary = CiteVocabulary()
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=messages,
                base_url=base,
                model=model,
                vocabularies=[vocabulary],
                http_client=client,
                headers={"x-api-key": key},
            )
            result = await bridge.complete(
                [vocabulary.prompt_hint() + "\n\n" + CITE_PROMPT_DOCS], max_tokens=256
            )
        cites = [b for b in result.content if isinstance(b, CitationBlock)]
        print(f"\n[messages] text={result.text!r}")
        print(f"[messages] cites={[(c.id, c.text) for c in cites]}")
        if not cites:
            pytest.skip("모델이 인용 태그를 쓰지 않았다. 프롬프트 준수 문제다")
        for cite in cites:
            assert result.text[cite.start_index : cite.end_index] == cite.text


class TestOpenAiResponses:
    @skip_responses
    async def test_stream_and_round_trip(self) -> None:
        assert RESPONSES is not None
        base, model, key = RESPONSES
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=responses, base_url=base, model=model, api_key=key, http_client=client
            )
            await _check_stream(bridge, "responses", max_output_tokens=SHORT)

    @skip_responses
    async def test_terminal_frame_carries_usage(self) -> None:
        """``response.completed``에 ``stop_reason``과 ``usage``가 실린다.

        종료 프레임을 해석하지 않고 버리면 둘을 잃는다. 이 벤더를 붙이면서 브리지의 실제
        버그가 드러난 자리다.
        """
        assert RESPONSES is not None
        base, model, key = RESPONSES
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=responses, base_url=base, model=model, api_key=key, http_client=client
            )
            result = await bridge.complete(["1+1은? 숫자만"], max_output_tokens=SHORT)
        print(f"\n[responses] stop={result.stop_reason} usage={result.usage}")
        assert result.stop_reason, "종료 프레임의 stop_reason을 잃었다"
        assert result.usage is not None, "종료 프레임의 usage를 잃었다"

    @skip_responses
    async def test_tool_call(self) -> None:
        assert RESPONSES is not None
        base, model, key = RESPONSES
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=responses, base_url=base, model=model, api_key=key, http_client=client
            )
            result = await bridge.complete(["서울 날씨 알려줘"], tools=[WEATHER_TOOL])
        calls = [b for b in result.content if isinstance(b, ToolUseBlock)]
        print(f"\n[responses] calls={[(c.name, c.input_json) for c in calls]}")
        assert calls, "도구 호출이 없다"
        assert json.loads(calls[0].input_json)


class TestGemini:
    @skip_gemini
    async def test_stream_and_round_trip(self) -> None:
        assert GEMINI is not None
        base, model, key = GEMINI
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=generate_content.for_model(model, api_key=key),
                base_url=base,
                model=model,
                http_client=client,
            )
            await _check_stream(bridge, "gemini", max_tokens=SHORT)

    @skip_gemini
    async def test_tool_call_arrives_complete(self) -> None:
        """이 API는 인수를 조각으로 쪼개지 않고 완성된 객체로 준다."""
        assert GEMINI is not None
        base, model, key = GEMINI
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=generate_content.for_model(model, api_key=key),
                base_url=base,
                model=model,
                http_client=client,
            )
            result = await bridge.complete(["서울 날씨 알려줘"], tools=[WEATHER_TOOL])
        calls = [b for b in result.content if isinstance(b, ToolUseBlock)]
        print(f"\n[gemini] calls={[(c.name, c.input_json) for c in calls]}")
        assert calls, "도구 호출이 없다"
        assert json.loads(calls[0].input_json)


class TestCrossVendor:
    @pytest.mark.skipif(
        ANTHROPIC is None or GEMINI is None,
        reason="set both ECC_ANTHROPIC_* and ECC_GEMINI_* to run",
    )
    async def test_answer_from_one_vendor_feeds_another(self) -> None:
        """한 벤더의 응답을 다른 벤더의 이력으로 넣는다. 허브가 있는 이유가 이것이다.

        공통 코어(텍스트)는 무손실이고 추론 블록은 발급 벤더로만 되돌릴 수 있으므로 생략된다.
        """
        assert ANTHROPIC is not None and GEMINI is not None
        a_base, a_model, a_key = ANTHROPIC
        g_base, g_model, g_key = GEMINI

        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            anthropic_bridge = Bridge(
                vendor=messages,
                base_url=a_base,
                model=a_model,
                http_client=client,
                headers={"x-api-key": a_key},
            )
            first = await anthropic_bridge.complete(
                ["한 단어로만 답하세요: 대한민국의 수도"], max_tokens=SHORT
            )

            gemini_bridge = Bridge(
                vendor=generate_content.for_model(g_model, api_key=g_key),
                base_url=g_base,
                model=g_model,
                http_client=client,
            )
            history = [
                "한 단어로만 답하세요: 대한민국의 수도",
                HubMessage.of_response(first),
                "방금 답한 도시의 인구는 대략? 숫자만",
            ]
            body = gemini_bridge.build_request(history)
            second = await gemini_bridge.complete(history, max_tokens=SHORT)

        print(f"\n[cross] anthropic={first.text!r}")
        print(f"[cross] gemini contents={body['contents']}")
        print(f"[cross] gemini={second.text!r}")

        assert body["contents"][1]["role"] == "model"
        assert body["contents"][1]["parts"][0]["text"] == first.text
        assert second.text.strip(), "교차 이력을 실은 요청이 빈 답을 냈다"


# =============================================================================
# 복합 입력. 토큰은 아끼고 블록은 많이 건드린다.
# =============================================================================


class TestDenseInput:
    """복합 이력이 실제 서버에 받아들여지는지.

    벤더마다 문서와 도구 결과가 실리는 자리가 다르다. 그 차이를 어댑터가 흡수하고 서버가
    이력을 거절하지 않으면 성공이다. ``max_tokens``를 작게 둬서 생성 비용을 아낀다.

    wire body 자체의 모양은 ``test_vendors.py``가 네트워크 없이 검증한다.
    """

    @skip_anthropic
    async def test_dense_input_is_accepted_by_anthropic(self) -> None:
        assert ANTHROPIC is not None
        base, model, key = ANTHROPIC
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=messages,
                base_url=base,
                model=model,
                vocabularies=[CiteVocabulary()],
                http_client=client,
                headers={"x-api-key": key},
            )
            result = await bridge.complete(dense_history(), tools=[DENSE_TOOL], max_tokens=SHORT)
        _report_dense("messages", result)

    @skip_gemini
    async def test_dense_input_is_accepted_by_gemini(self) -> None:
        assert GEMINI is not None
        base, model, key = GEMINI
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=generate_content.for_model(model, api_key=key),
                base_url=base,
                model=model,
                vocabularies=[CiteVocabulary()],
                http_client=client,
            )
            result = await bridge.complete(dense_history(), tools=[DENSE_TOOL], max_tokens=SHORT)
        _report_dense("gemini", result)

    @skip_responses
    async def test_dense_input_is_accepted_by_responses(self) -> None:
        assert RESPONSES is not None
        base, model, key = RESPONSES
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            bridge = Bridge(
                vendor=responses,
                base_url=base,
                model=model,
                api_key=key,
                vocabularies=[CiteVocabulary()],
                http_client=client,
            )
            result = await bridge.complete(
                dense_history(), tools=[DENSE_TOOL], max_output_tokens=SHORT
            )
        _report_dense("responses", result)
