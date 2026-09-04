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

from enhanced_completion import (
    Bridge,
    CitationBlock,
    CiteVocabulary,
    HubMessage,
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
