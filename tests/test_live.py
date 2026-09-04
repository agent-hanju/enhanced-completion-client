"""실제 엔드포인트 대상 시험.

환경변수가 없으면 건너뛴다. 엔드포인트와 모델명을 코드에 넣지 않는다. 지금 Java E2E 시험에
사내 주소와 모델명이 박혀 있고 비활성화 어노테이션이 주석 처리되어 CI에서 돌면 실패하는데,
그것을 반복하지 않는다.

    ECC_LIVE_BASE_URL=http://host:port ECC_LIVE_MODEL=luxia-3.5 \
        uv run pytest -m live -s

모델이 느리므로 ``max_tokens``를 작게 고정한다. 확인하려는 것은 답변 품질이 아니라 프레임
해석, 델타 흐름, 병합 결과의 일치다.

이 시험은 확인용 출력을 남긴다(``-s``). 아직 확정되지 않은 두 가지를 눈으로 보기 위해서다.
추론 필드 이름이 ``reasoning``인지 ``reasoning_content``인지, 인용 태그가 중첩(``<cite><id>``)
인지 속성(``<cite id=>``)인지다.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

from enhanced_completion import Bridge, SyncBridge, TextBlock, ThinkingBlock
from enhanced_completion.vendors import chat_completions

BASE_URL = os.getenv("ECC_LIVE_BASE_URL", "")
MODEL = os.getenv("ECC_LIVE_MODEL", "")
API_KEY = os.getenv("ECC_LIVE_API_KEY") or None

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not BASE_URL or not MODEL,
        reason="set ECC_LIVE_BASE_URL and ECC_LIVE_MODEL to run live tests",
    ),
]

# 느린 모델이므로 짧게 끊는다.
SHORT = 24

# thinking 모델에서 추론을 끈다. 켜두면 짧은 예산을 추론이 다 써서 content가 비고
# stop_reason이 length가 된다. 실제로 qwen3-8b에서 그렇다.
#
# 이것은 vLLM 전용 확장이다. 같은 필드를 OpenAI에 보내면 요청이 통째로 400으로 거절된다.
# common-hitl-chat이 LlmDialect라는 seam을 따로 둔 이유가 이것이고, 그 저장소 주석에 실제
# 사고 기록이 있다. 여기서는 벤더가 하나라 호출 인자로 넘긴다.
NO_THINKING: dict[str, object] = {"chat_template_kwargs": {"enable_thinking": False}}

# 추론을 끝내고 본문까지 받으려면 예산이 필요하다. 실측에서 qwen3-8b가 "1+1은?"에도 추론에
# 900자 남짓을 쓴다. 600 토큰이면 stop_reason이 stop으로 끝난다.
REASONING_BUDGET = 600


def _bridge(client: httpx.AsyncClient) -> Bridge:
    return Bridge(
        vendor=chat_completions,
        base_url=BASE_URL,
        model=MODEL,
        api_key=API_KEY,
        http_client=client,
    )


class TestLiveStreaming:
    async def test_short_answer_streams_and_merges(self) -> None:
        async with httpx.AsyncClient(timeout=300.0) as client:
            bridge = _bridge(client)
            stream = bridge.stream(
                ["한 단어로만 답하세요: 대한민국의 수도"],
                max_tokens=SHORT,
                temperature=0.0,
                **NO_THINKING,
            )
            deltas = [d async for d in stream]
            result = stream.result

        assert deltas, "no deltas arrived"
        streamed = "".join(d.text for d in deltas)
        assert streamed == result.text, "델타를 이어붙인 것과 병합 결과가 달라졌다"
        assert result.text.strip(), "empty answer"

        print(f"\n[live] deltas={len(deltas)} stop_reason={result.stop_reason}")
        print(f"[live] text={result.text!r}")
        print(f"[live] blocks={[b.type for b in result.content]}")
        if result.usage is not None:
            print(f"[live] usage in={result.usage.input_tokens} out={result.usage.output_tokens}")

    async def test_complete_matches_stream(self) -> None:
        async with httpx.AsyncClient(timeout=300.0) as client:
            result = await _bridge(client).complete(
                ["1+1은? 숫자만"], max_tokens=SHORT, temperature=0.0, **NO_THINKING
            )
        assert result.text.strip()
        print(f"\n[live] complete={result.text!r}")

    async def test_reasoning_arrives_as_thinking_block(self) -> None:
        """추론이 별도 블록으로 갈리는지 확인한다.

        vLLM 0.28은 필드 이름을 ``reasoning``으로 보낸다. ``reasoning_content``가 아니다.
        어댑터가 둘 다 보지만 실제로 오는 것은 앞쪽이다.

        추론 예산을 짧게 주면 ``content``가 비고 ``stop_reason``이 ``length``가 된다. 그것이
        정상 동작이므로 여기서는 본문을 요구하지 않는다. 둘 다 나오는 경우는
        :meth:`test_reasoning_then_content_in_one_stream`이 본다.
        """
        async with httpx.AsyncClient(timeout=300.0) as client:
            result = await _bridge(client).complete(
                ["하늘은 왜 파란가?"], max_tokens=SHORT, temperature=0.0
            )
        thinking = [b for b in result.content if isinstance(b, ThinkingBlock)]
        assert thinking, "추론 블록이 없다. 필드 이름이 바뀌었는지 확인하라"
        assert thinking[0].thinking.strip()
        assert result.stop_reason == "length"
        print(f"\n[live] stop_reason={result.stop_reason}")
        print(f"[live] thinking={thinking[0].thinking[:160]!r}")
        print(f"[live] text={result.text!r}")

    @pytest.mark.slow
    async def test_reasoning_then_content_in_one_stream(self) -> None:
        """추론이 끝나고 본문이 이어지는 온전한 스트림.

        예산을 넉넉히 줘야 관측된다. 실측에서 qwen3-8b가 사소한 질문에도 추론에 900자 남짓을
        쓰고 43초가 걸린다. 그래서 ``slow`` 마커로 갈라둔다.

        확인하려는 것이 셋이다. 두 채널이 섞이지 않고 갈리는지, 227개 델타가 인덱스로 접혀
        블록 두 개가 되는지, 델타를 이어붙인 것과 병합 결과가 같은지다.

        어댑터가 추론에 ``index=-1``, 본문에 ``index=0``을 준다. 인덱스가 없으면 블록이 델타
        수만큼 흩어진다.
        """
        async with httpx.AsyncClient(timeout=900.0) as client:
            stream = _bridge(client).stream(
                ["1+1은? 숫자만 답하세요."], max_tokens=REASONING_BUDGET, temperature=0.0
            )
            deltas = [d async for d in stream]
            result = stream.result

        assert result.stop_reason == "stop", "예산이 부족하면 length가 된다. 늘려라"

        # 최종 블록은 추론 하나와 본문 하나다.
        assert [b.type for b in result.content] == ["thinking", "text"]
        thinking, text = result.content[0], result.content[1]
        assert isinstance(thinking, ThinkingBlock)
        assert isinstance(text, TextBlock)
        assert thinking.thinking.strip()
        assert text.text.strip()

        # 델타를 이어붙인 것과 병합 결과가 같아야 한다. 두 채널 각각에 대해 확인한다.
        streamed_text = "".join(
            b.text for d in deltas for b in d.content if isinstance(b, TextBlock)
        )
        streamed_thinking = "".join(
            b.thinking for d in deltas for b in d.content if isinstance(b, ThinkingBlock)
        )
        assert streamed_text == text.text
        assert streamed_thinking == thinking.thinking

        # 추론이 본문보다 먼저 끝난다. 두 채널이 섞이지 않는다.
        first_text = next(
            i for i, d in enumerate(deltas) for b in d.content if isinstance(b, TextBlock)
        )
        last_thinking = max(
            i for i, d in enumerate(deltas) for b in d.content if isinstance(b, ThinkingBlock)
        )
        assert last_thinking < first_text, "추론과 본문이 섞여서 도착했다"

        print(f"\n[live] deltas={len(deltas)} stop_reason={result.stop_reason}")
        print(f"[live] blocks={[b.type for b in result.content]}")
        print(f"[live] thinking {len(thinking.thinking)}자={thinking.thinking[:120]!r}")
        print(f"[live] text={text.text!r}")

    async def test_citation_tag_shape_if_documents_given(self) -> None:
        """인용 태그 문법이 중첩인지 속성인지 확인한다.

        어휘 구현 전이라 태그를 파싱하지 않는다. 본문 원문을 그대로 보고 어느 모양으로 오는지
        눈으로 정한다.
        """
        prompt = (
            "다음 문서를 근거로 한 문장만 답하고, 근거 부분을 인용 태그로 감싸세요.\n"
            '<documents><document id="d1">서울은 대한민국의 수도다.</document></documents>\n'
            "질문: 대한민국의 수도는?"
        )
        async with httpx.AsyncClient(timeout=300.0) as client:
            result = await _bridge(client).complete(
                [prompt], max_tokens=96, temperature=0.0, **NO_THINKING
            )
        print(f"\n[live] raw answer={result.text!r}")

    async def test_tool_call_shape(self) -> None:
        from enhanced_completion import ToolDefinition, ToolUseBlock

        tool = ToolDefinition(
            name="get_weather",
            description="도시의 현재 날씨를 조회한다",
            input_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        )
        async with httpx.AsyncClient(timeout=300.0) as client:
            result = await _bridge(client).complete(
                ["서울 날씨 알려줘"],
                tools=[tool],
                max_tokens=96,
                temperature=0.0,
                **NO_THINKING,
            )
        calls = [b for b in result.content if isinstance(b, ToolUseBlock)]
        print(f"\n[live] tool calls={len(calls)} stop_reason={result.stop_reason}")
        for call in calls:
            print(f"[live] call {call.name} args={call.input_json!r}")
            if call.input_json:
                json.loads(call.input_json)  # 조립이 유효한 JSON인지 확인

    def test_sync_bridge_against_live_endpoint(self) -> None:
        with httpx.Client(timeout=300.0) as client:
            bridge = SyncBridge(
                vendor=chat_completions,
                base_url=BASE_URL,
                model=MODEL,
                api_key=API_KEY,
                http_client=client,
            )
            stream = bridge.stream(
                ["한 단어로만 답하세요: 대한민국의 수도"], max_tokens=SHORT, **NO_THINKING
            )
            deltas = list(stream)
            result = stream.result
        assert deltas
        assert result.text.strip()
        assert all(isinstance(b, TextBlock) for b in result.content if b.type == "text")
        print(f"\n[live] sync text={result.text!r}")
