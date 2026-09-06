"""인용 어휘. 올림, 내림, 그리고 둘의 왕복."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from completion_bridge import (
    Bridge,
    Citation,
    CitationBlock,
    CiteVocabulary,
    HubMessage,
    HubResponse,
    StreamMerger,
    SyncBridge,
    TextBlock,
    ThinkingBlock,
)
from completion_bridge.vendors import chat_completions

BASE = "http://llm.test"
URL = f"{BASE}/v1/chat/completions"


def sse(*texts: str) -> bytes:
    body = ""
    for text in texts:
        payload = {"id": "c1", "model": "m", "choices": [{"index": 0, "delta": {"content": text}}]}
        body += f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    return (body + "data: [DONE]\n\n").encode()


def bridge() -> Bridge:
    return Bridge(
        vendor=chat_completions,
        base_url=BASE,
        model="m",
        vocabularies=[CiteVocabulary()],
        http_client=httpx.AsyncClient(),
    )


def merge(*deltas: HubResponse) -> HubResponse:
    merger: StreamMerger[HubResponse] = StreamMerger(HubResponse)
    for delta in deltas:
        merger.apply(delta)
    return merger.build()


def cited_blocks(response: HubResponse) -> list[TextBlock]:
    return [
        block
        for block in response.content
        if isinstance(block, TextBlock) and block.citations
    ]


def chat_text(content: object) -> str:
    if isinstance(content, str):
        return content
    assert isinstance(content, list)
    return "".join(
        str(part["text"])
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


class TestLifting:
    @respx.mock
    async def test_citation_becomes_a_cited_text_block(self) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(
                200,
                content=sse('서울은 대한민국의 <cite id="d1">수도</cite>입니다.'),
            )
        )
        result = await bridge().complete(["수도?"])

        assert result.text == "서울은 대한민국의 수도입니다."
        blocks = cited_blocks(result)
        assert len(blocks) == 1
        assert blocks[0].text == "수도"
        assert [(c.source, c.id, c.cited_text) for c in blocks[0].citations] == [
            ("cite", "d1", None)
        ]

    @respx.mock
    async def test_cited_answer_span_is_its_own_text_block(self) -> None:
        """XML이 감싼 문구는 근거 원문이 아니라 인용 표시가 붙은 답변 구간이다."""
        respx.post(URL).mock(
            return_value=httpx.Response(
                200, content=sse('서울은 대한민국의 <cite id="d1">수도</cite>입니다.')
            )
        )
        result = await bridge().complete(["수도?"])
        cited = cited_blocks(result)[0]
        assert cited.text == "수도"
        assert cited.citations[0].cited_text is None

    @respx.mock
    async def test_tag_split_across_deltas(self) -> None:
        """토큰 단위로 오면 태그가 청크 경계에 걸린다."""
        respx.post(URL).mock(
            return_value=httpx.Response(
                200, content=sse("서울은 ", "<ci", 'te id="d', '1">수도', "</ci", "te>입니다.")
            )
        )
        result = await bridge().complete(["수도?"])
        assert result.text == "서울은 수도입니다."
        cited = cited_blocks(result)[0]
        assert cited.text == "수도"
        assert cited.citations[0].id == "d1"

    @respx.mock
    async def test_multiple_citations_keep_order_and_indices(self) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(
                200,
                content=sse('<cite id="a">서울</cite>과 <cite id="b">부산</cite>이다.'),
            )
        )
        result = await bridge().complete(["도시?"])
        cited = cited_blocks(result)
        assert [b.citations[0].id for b in cited] == ["a", "b"]
        assert result.text == "서울과 부산이다."
        assert [b.text for b in cited] == ["서울", "부산"]

    @respx.mock
    async def test_rag_alias_works(self) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(200, content=sse('<rag id="d1">본문</rag>'))
        )
        result = await bridge().complete(["x"])
        assert result.text == "본문"
        assert [b.citations[0].id for b in cited_blocks(result)] == ["d1"]

    @respx.mock
    async def test_text_without_citations_is_untouched(self) -> None:
        respx.post(URL).mock(return_value=httpx.Response(200, content=sse("인용 없는 답변")))
        result = await bridge().complete(["x"])
        assert result.text == "인용 없는 답변"
        assert [b.type for b in result.content] == ["text"]

    @respx.mock
    async def test_unclosed_citation_is_closed_on_flush(self) -> None:
        """스트림이 끝났는데 태그가 안 닫혔다. flush가 지금까지의 구간으로 확정한다."""
        respx.post(URL).mock(return_value=httpx.Response(200, content=sse('앞 <cite id="d1">잘린')))
        result = await bridge().complete(["x"])
        assert result.text == "앞 잘린"
        cited = cited_blocks(result)[0]
        assert cited.text == "잘린"
        assert cited.citations[0].id == "d1"

    @respx.mock
    async def test_thinking_blocks_pass_through(self) -> None:
        payload = (
            'data: {"choices":[{"index":0,"delta":{"reasoning":"생각"}}]}\n\n'
            'data: {"choices":[{"index":0,"delta":{"content":"<cite id=\\"d1\\">답</cite>"}}]}\n\n'
            "data: [DONE]\n\n"
        ).encode()
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        result = await bridge().complete(["x"])
        assert [b.type for b in result.content] == ["thinking", "text"]
        assert cited_blocks(result)[0].citations[0].id == "d1"

    @respx.mock
    async def test_streamed_text_matches_merged_text(self) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(
                200, content=sse("앞 ", '<cite id="d1">', "본문", "</cite>", " 뒤")
            )
        )
        stream = bridge().stream(["x"])
        deltas = [d async for d in stream]
        streamed = "".join(b.text for d in deltas for b in d.content if isinstance(b, TextBlock))
        assert streamed == stream.result.text == "앞 본문 뒤"


class TestLowering:
    def test_nested_citation_is_wrapped_in_place(self) -> None:
        message = HubMessage(
            role="assistant",
            content=[
                TextBlock(text="서울은 "),
                TextBlock(text="수도", citations=[Citation(source="cite", id="d1")]),
                TextBlock(text="입니다."),
            ],
        )
        content = self._request(message)["messages"][0]["content"]
        assert chat_text(content) == '서울은 <cite id="d1">수도</cite>입니다.'

    def test_native_anthropic_citation_is_not_guessed_as_xml(self) -> None:
        message = HubMessage(
            role="assistant",
            content=[
                TextBlock(
                    text="답",
                    citations=[
                        Citation(
                            source="messages",
                            type="char_location",
                            cited_text="근거 원문",
                            native={"type": "char_location", "cited_text": "근거 원문"},
                        )
                    ],
                )
            ],
        )
        assert self._request(message)["messages"][0]["content"] == "답"

    def test_citation_is_spliced_back_into_the_text(self) -> None:
        """인용 텍스트가 본문에도 있으므로 따로 이어붙이면 문장이 두 번 나간다."""
        message = HubMessage(
            role="assistant",
            content=[
                TextBlock(text="서울은 수도입니다."),
                CitationBlock(id="d1", text="수도", start_index=4, end_index=6),
            ],
        )
        body = SyncBridge(
            vendor=chat_completions,
            base_url=BASE,
            model="m",
            vocabularies=[CiteVocabulary()],
        ).build_request([message])
        assert body["messages"] == [
            {"role": "assistant", "content": '서울은 <cite id="d1">수도</cite>입니다.'}
        ]

    def test_multiple_citations_are_spliced_in_order(self) -> None:
        message = HubMessage(
            role="assistant",
            content=[
                TextBlock(text="서울과 부산이다."),
                CitationBlock(id="b", start_index=4, end_index=6),
                CitationBlock(id="a", start_index=0, end_index=2),
            ],
        )
        body = self._request(message)
        assert body["messages"][0]["content"] == (
            '<cite id="a">서울</cite>과 <cite id="b">부산</cite>이다.'
        )

    def test_out_of_range_indices_fall_back_to_text(self) -> None:
        message = HubMessage(
            role="assistant",
            content=[
                TextBlock(text="짧다"),
                CitationBlock(id="d1", text="원문", start_index=100, end_index=200),
            ],
        )
        body = self._request(message)
        assert body["messages"][0]["content"] == '<cite id="d1">원문</cite>짧다'

    def test_overlapping_citations_keep_the_first(self) -> None:
        """겹친 태그를 만들면 파서가 되읽을 수 없다."""
        message = HubMessage(
            role="assistant",
            content=[
                TextBlock(text="abcdef"),
                CitationBlock(id="a", start_index=0, end_index=4),
                CitationBlock(id="b", start_index=2, end_index=6),
            ],
        )
        body = self._request(message)
        assert body["messages"][0]["content"] == '<cite id="a">abcd</cite>ef'

    def test_thinking_is_still_dropped(self) -> None:
        message = HubMessage(
            role="assistant",
            content=[
                ThinkingBlock(thinking="비밀"),
                TextBlock(text="답"),
                CitationBlock(id="d1", start_index=0, end_index=1),
            ],
        )
        body = self._request(message)
        assert body["messages"][0]["content"] == '<cite id="d1">답</cite>'

    def test_message_without_citations_is_unchanged(self) -> None:
        message = HubMessage(role="user", content=[TextBlock(text="질문")])
        body = self._request(message)
        assert body["messages"] == [{"role": "user", "content": "질문"}]

    @staticmethod
    def _request(message: HubMessage) -> dict[str, object]:
        return SyncBridge(
            vendor=chat_completions,
            base_url=BASE,
            model="m",
            vocabularies=[CiteVocabulary()],
        ).build_request([message])


class TestRoundTrip:
    @respx.mock
    @pytest.mark.parametrize(
        "answer",
        [
            '서울은 대한민국의 <cite id="d1">수도</cite>입니다.',
            '<cite id="a">앞</cite> 사이 <cite id="b">뒤</cite>',
            '<cite id="d1">전체가 인용</cite>',
            "인용 없는 답",
        ],
    )
    async def test_lift_then_lower_reproduces_the_original(self, answer: str) -> None:
        """올림의 역이 내림이다. 대화를 이어가면 이 왕복이 실제로 일어난다."""
        respx.post(URL).mock(return_value=httpx.Response(200, content=sse(answer)))
        client = bridge()
        result = await client.complete(["질문"])

        body = client.build_request([HubMessage.of_response(result)])
        assert chat_text(body["messages"][0]["content"]) == answer

    @respx.mock
    async def test_round_trip_survives_token_level_splitting(self) -> None:
        answer = '서울은 <cite id="d1">수도</cite>입니다.'
        respx.post(URL).mock(return_value=httpx.Response(200, content=sse(*answer)))
        client = bridge()
        result = await client.complete(["질문"])
        body = client.build_request([HubMessage.of_response(result)])
        assert chat_text(body["messages"][0]["content"]) == answer


class TestVocabularyContract:
    def test_block_type_is_registered(self) -> None:
        from completion_bridge import registered_blocks

        CiteVocabulary().register()
        assert registered_blocks()["citation"] is CitationBlock

    def test_prompt_hint_mentions_the_configured_tag(self) -> None:
        """모델은 프롬프트가 지시하지 않으면 이 태그를 쓰지 않는다. 실측에서 확인했다."""
        hint = CiteVocabulary(tag="ref").prompt_hint()
        assert "<ref " in hint and "</ref>" in hint

    def test_custom_tag_name_keeps_the_same_path(self) -> None:
        vocabulary = CiteVocabulary(tag="ref", alias=())
        assert vocabulary.schema.paths_for("ref") == frozenset({"/cite"})

    @respx.mock
    async def test_custom_tag_lifts_and_lowers(self) -> None:
        respx.post(URL).mock(
            return_value=httpx.Response(200, content=sse('<ref id="d1">본문</ref>'))
        )
        client = Bridge(
            vendor=chat_completions,
            base_url=BASE,
            model="m",
            vocabularies=[CiteVocabulary(tag="ref", alias=())],
            http_client=httpx.AsyncClient(),
        )
        result = await client.complete(["x"])
        assert result.text == "본문"
        body = client.build_request([HubMessage.of_response(result)])
        assert chat_text(body["messages"][0]["content"]) == '<ref id="d1">본문</ref>'

    def test_mapper_is_a_fresh_instance_each_time(self) -> None:
        """상태 기계라 재사용하면 앞 스트림의 상태가 남는다."""
        vocabulary = CiteVocabulary()
        assert vocabulary.lift_mapper() is not vocabulary.lift_mapper()

    def test_merger_appends_citations_without_matching(self) -> None:
        """인용은 조각으로 오지 않으므로 index가 없고, 키 없는 원소는 덧붙는다."""
        result = merge(
            HubResponse(content=[CitationBlock(id="a", start_index=0, end_index=1)]),
            HubResponse(content=[CitationBlock(id="b", start_index=1, end_index=2)]),
        )
        assert [b.id for b in result.content if isinstance(b, CitationBlock)] == ["a", "b"]

    def test_merger_attaches_nested_citation_delta_to_text_slot(self) -> None:
        result = merge(
            HubResponse(content=[TextBlock(text="답", index=0)]),
            HubResponse(
                content=[
                    TextBlock(
                        index=0,
                        citations=[Citation(source="messages", id="d1")],
                    )
                ]
            ),
        )
        text = result.content[0]
        assert isinstance(text, TextBlock)
        assert text.text == "답"
        assert [(c.source, c.id) for c in text.citations] == [("messages", "d1")]
