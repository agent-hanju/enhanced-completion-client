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

from enhanced_completion import (
    Bridge,
    CitationBlock,
    CiteVocabulary,
    HubMessage,
    HubResponse,
    StreamMerger,
    SyncBridge,
    TextBlock,
    ThinkingBlock,
)
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


def _relift(vocabulary: CiteVocabulary, text: str) -> list[CitationBlock]:
    """되쓴 문자열을 다시 올려서 인용 블록을 꺼낸다.

    태그 문자열이 들어 있는지만 보면 태그는 맞는데 위치가 틀린 경우를 놓친다. 다시 올려
    같은 블록이 나오는 것이 "제대로 직렬화됐다"의 가장 강한 형태다.
    """
    mapper = vocabulary.lift_mapper()
    deltas = [*mapper.map(HubResponse(content=[TextBlock(text=text, index=0)])), *mapper.flush()]
    merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
    for delta in deltas:
        merger.apply(delta)
    return [b for b in merger.build().content if isinstance(b, CitationBlock)]


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
        assert result.stop_reason == "max_tokens"
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

        assert result.stop_reason == "end_turn", "예산이 부족하면 max_tokens가 된다. 늘려라"

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

    async def test_citation_round_trip_through_a_second_turn(self) -> None:
        """인용이 실린 응답을 다음 요청의 이력으로 되쓰고, 그 요청이 실제로 통하는지.

        모델은 프롬프트가 지시하지 않으면 이 태그를 쓰지 않는다. 실측에서 입력 문서의 태그를
        흉내내는 것을 확인했다. 그래서 어휘가 :meth:`prompt_hint`를 들고 그것을 프롬프트에 넣는다.

        확인하려는 것이 다섯이다.

        1. 태그가 청크에 쪼개져 와도 인덱스가 본문 위치를 정확히 가리킨다
        2. 인용 텍스트가 본문에도 남는다. 빼면 답변이 끊긴다
        3. 되쓴 wire body에 태그가 복원된다
        4. 되쓴 문자열을 다시 올리면 같은 블록이 나온다. 올림과 내림이 서로의 역이다
        5. 그 이력을 실은 두 번째 요청이 서버에 실제로 받아들여진다
        """
        vocabulary = CiteVocabulary()
        prompt = (
            vocabulary.prompt_hint() + "\n\n"
            '<documents><document id="d1">서울은 대한민국의 수도다.</document>'
            '<document id="d2">부산은 제2의 도시다.</document></documents>\n'
            "질문: 대한민국의 수도와 제2도시는? 한 문장으로."
        )
        async with httpx.AsyncClient(timeout=300.0) as client:
            bridge = Bridge(
                vendor=chat_completions,
                base_url=BASE_URL,
                model=MODEL,
                api_key=API_KEY,
                vocabularies=[vocabulary],
                http_client=client,
            )
            first = await bridge.complete([prompt], max_tokens=160, temperature=0.0, **NO_THINKING)

            history = [prompt, HubMessage.of_response(first), "방금 답을 한 단어로 줄이면?"]
            body = bridge.build_request(history)

            # 5. 되쓴 이력이 실린 요청이 실제로 통한다. 400이면 여기서 터진다.
            second = await bridge.complete(
                history, max_tokens=SHORT, temperature=0.0, **NO_THINKING
            )

        cites = [b for b in first.content if isinstance(b, CitationBlock)]
        assistant = body["messages"][1]
        assert isinstance(assistant, dict)
        lowered = assistant["content"]
        assert isinstance(lowered, str)

        print(f"\n[live] first={first.text!r}")
        print(f"[live] blocks={[b.type for b in first.content]}")
        for cite in cites:
            print(f"[live] cite id={cite.id!r} [{cite.start_index}:{cite.end_index}]")
        print(f"[live] lowered={lowered!r}")
        print(f"[live] second={second.text!r}")

        assert second.text.strip(), "되쓴 이력을 실은 두 번째 턴이 빈 답을 냈다"
        assert assistant["role"] == "assistant"

        if not cites:
            pytest.skip("모델이 인용 태그를 쓰지 않았다. 프롬프트 준수 문제이며 파서 문제가 아니다")

        # 1. 인덱스가 본문 위치를 가리킨다. 두 경로가 같은 커서를 공유한다는 증거다.
        for cite in cites:
            assert first.text[cite.start_index : cite.end_index] == cite.text

        # 2, 3.
        for cite in cites:
            assert cite.text in first.text
            assert f'<cite id="{cite.id}">{cite.text}</cite>' in lowered

        # 4. 되쓴 문자열을 다시 올리면 같은 블록이 나온다.
        relifted = _relift(vocabulary, lowered)
        assert [(c.id, c.text) for c in relifted] == [(c.id, c.text) for c in cites]
        print(f"[live] relifted={[(c.id, c.text) for c in relifted]}")

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
