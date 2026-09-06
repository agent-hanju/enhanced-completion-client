"""확립된 변환 규칙. ``docs/Support-Matrix.md``의 표를 회귀 테스트로 고정한다.

세 매퍼가 값의 어휘까지 Anthropic으로 모은다. 타입만 맞추고 값을 벤더별로 흘려보내면 소비 앱이
``stop_reason``을 읽으려고 어느 벤더에서 왔는지 알아야 한다. 그러면 허브가 아니다.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from completion_bridge import (
    AnnotationBlock,
    AudioBlock,
    Bridge,
    Citation,
    DocumentBlock,
    ExtractionError,
    GroundingBlock,
    HubMessage,
    ImageBlock,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    VendorBlock,
)
from completion_bridge.vendors import chat_completions, generate_content, messages, responses
from completion_bridge.vendors.normalize import (
    REFUSAL_PREFIX,
    normalize_role,
    stop_reason_from_chat,
    stop_reason_from_gemini,
    stop_reason_from_responses,
)

BASE = "http://norm.test"
GEMINI = generate_content.for_model("m")


def sse(*frames: tuple[str | None, object]) -> bytes:
    out = ""
    for name, payload in frames:
        head = f"event: {name}\n" if name else ""
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        out += f"{head}data: {body}\n\n"
    return out.encode()


def make(adapter: object, **kwargs: object) -> Bridge:
    return Bridge(
        vendor=adapter,  # type: ignore[arg-type]
        base_url=BASE,
        model="m",
        http_client=httpx.AsyncClient(),
        **kwargs,  # type: ignore[arg-type]
    )


class TestStopReasonVocabulary:
    """허브 어휘는 Anthropic이다."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("stop", "end_turn"),
            ("length", "max_tokens"),
            ("tool_calls", "tool_use"),
            ("function_call", "tool_use"),
            ("content_filter", "content_filter"),
            ("something_new", "something_new"),
            (None, None),
        ],
    )
    def test_chat_completions(self, raw: str | None, expected: str | None) -> None:
        assert stop_reason_from_chat(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # 허브 어휘에 1:1 대응물이 있는 셋만 옮긴다.
            ("STOP", "end_turn"),
            ("MAX_TOKENS", "max_tokens"),
            ("SAFETY", "content_filter"),
            # 나머지는 원문 그대로다. 케이스만 바꾸면 허브 어휘도 원문도 아니게 된다.
            ("RECITATION", "RECITATION"),
            ("OTHER", "OTHER"),
            ("BLOCKLIST", "BLOCKLIST"),
            ("SPII", "SPII"),
            ("MALFORMED_FUNCTION_CALL", "MALFORMED_FUNCTION_CALL"),
            ("TOO_MANY_TOOL_CALLS", "TOO_MANY_TOOL_CALLS"),
            (None, None),
        ],
    )
    def test_gemini(self, raw: str | None, expected: str | None) -> None:
        assert stop_reason_from_gemini(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("completed", "end_turn"),
            ("failed", "error"),
            ("incomplete", "max_tokens"),
            ("cancelled", "cancelled"),
            ("queued", "queued"),
            (None, None),
        ],
    )
    def test_responses(self, raw: str | None, expected: str | None) -> None:
        assert stop_reason_from_responses(raw) == expected

    @respx.mock
    async def test_three_vendors_report_the_same_word(self) -> None:
        """같은 정상 종료를 세 벤더가 다르게 부르지만 허브에서는 하나다."""
        respx.post(f"{BASE}/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    (None, {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}),
                    (None, "[DONE]"),
                ),
            )
        )
        respx.post(f"{BASE}/v1/messages").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    (
                        "message_delta",
                        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
                    ),
                    ("message_stop", {"type": "message_stop"}),
                ),
            )
        )
        respx.post(f"{BASE}{GEMINI.path}").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    (None, {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]})
                ),
            )
        )
        respx.post(f"{BASE}/v1/responses").mock(
            return_value=httpx.Response(
                200,
                content=sse(
                    (None, {"type": "response.completed", "response": {"status": "completed"}})
                ),
            )
        )

        reasons = []
        for adapter in (chat_completions, messages, GEMINI, responses):
            reasons.append((await make(adapter).complete(["x"])).stop_reason)
        assert reasons == ["end_turn"] * 4


class TestRoleVocabulary:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("user", "user"),
            ("assistant", "assistant"),
            # Gemini는 assistant를 model이라 부른다.
            ("model", "assistant"),
            (None, "assistant"),
            ("weird", "assistant"),
        ],
    )
    def test_table(self, raw: str | None, expected: str) -> None:
        assert normalize_role(raw) == expected

    def test_chat_completions_keeps_instruction_roles_inline(self) -> None:
        """``messages``가 system/developer를 받는다. 위치와 role을 접지 않는다."""
        body = make(chat_completions).build_request(
            [
                "질문",
                HubMessage.assistant("답"),
                HubMessage(role="developer", content=[TextBlock(text="중간 지침")]),
            ]
        )
        assert [turn["role"] for turn in body["messages"]] == ["user", "assistant", "developer"]

    def test_anthropic_tool_result_turn_is_user(self) -> None:
        """도구 결과가 실린 턴은 반드시 user다. 이 API의 계약이다."""
        message = HubMessage(
            role="assistant", content=[ToolResultBlock(tool_use_id="c1", content="42")]
        )
        body = make(messages).build_request([message])
        assert body["messages"][0]["role"] == "user"


class TestRefusal:
    """거부는 본문에 실리되 표시가 붙는다."""

    @respx.mock
    async def test_chat_completions_marks_refusal(self) -> None:
        payload = sse(
            (None, {"choices": [{"index": 0, "delta": {"refusal": "못 합니다"}}]}),
            (None, "[DONE]"),
        )
        respx.post(f"{BASE}/v1/chat/completions").mock(
            return_value=httpx.Response(200, content=payload)
        )
        result = await make(chat_completions).complete(["x"])
        assert result.text == f"{REFUSAL_PREFIX}못 합니다"

    @respx.mock
    async def test_responses_marks_only_the_first_delta(self) -> None:
        """델타마다 붙이면 이어붙인 본문에 표시가 여러 번 나온다."""
        payload = sse(
            (
                None,
                {
                    "type": "response.refusal.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "못 ",
                },
            ),
            (
                None,
                {
                    "type": "response.refusal.delta",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "합니다",
                },
            ),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(f"{BASE}/v1/responses").mock(return_value=httpx.Response(200, content=payload))
        result = await make(responses).complete(["x"])
        assert result.text == f"{REFUSAL_PREFIX}못 합니다"
        assert result.text.count(REFUSAL_PREFIX) == 1


class TestGeminiPromptBlocking:
    """입력이 차단되면 candidates가 오지 않는다. 빈 응답과 구분되어야 한다."""

    @respx.mock
    async def test_block_reason_is_reported(self) -> None:
        payload = sse(
            (
                None,
                {
                    "promptFeedback": {
                        "blockReason": "PROHIBITED_CONTENT",
                        "safetyRatings": [{"category": "HARM_CATEGORY_DANGEROUS_CONTENT"}],
                    },
                    "usageMetadata": {"promptTokenCount": 42},
                    "responseId": "r1",
                },
            )
        )
        respx.post(f"{BASE}{GEMINI.path}").mock(return_value=httpx.Response(200, content=payload))
        result = await make(GEMINI).complete(["x"])

        assert result.block_reason == "PROHIBITED_CONTENT"
        assert result.text == ""
        # 원본은 진단용으로 보존한다.
        raw = result.blocks_of(VendorBlock)
        assert len(raw) == 1
        assert raw[0].raw["safetyRatings"][0]["category"] == "HARM_CATEGORY_DANGEROUS_CONTENT"

    @respx.mock
    async def test_normal_empty_response_has_no_block_reason(self) -> None:
        """정상 종료된 빈 응답을 차단으로 오인하지 않는다."""
        payload = sse(
            (None, {"candidates": [{"content": {"parts": []}, "finishReason": "STOP"}]})
        )
        respx.post(f"{BASE}{GEMINI.path}").mock(return_value=httpx.Response(200, content=payload))
        result = await make(GEMINI).complete(["x"])

        assert result.block_reason is None
        assert result.stop_reason == "end_turn"


class TestGeminiPartRules:
    """판정 순서와 대응이 확립된 규칙과 같아야 한다."""

    @respx.mock
    async def test_function_response_becomes_tool_result(self) -> None:
        payload = sse(
            (
                None,
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionResponse": {
                                            "id": "c1",
                                            "name": "lookup",
                                            "response": {"result": "1000만"},
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                },
            ),
        )
        respx.post(f"{BASE}{GEMINI.path}").mock(return_value=httpx.Response(200, content=payload))
        block = (await make(GEMINI).complete(["x"])).content[0]
        assert isinstance(block, ToolResultBlock)
        assert (block.tool_use_id, block.content) == ("c1", "1000만")

    @respx.mock
    async def test_inline_image_becomes_image_block(self) -> None:
        payload = sse(
            (
                None,
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"inlineData": {"mimeType": "image/png", "data": "AA"}}]
                            }
                        }
                    ]
                },
            ),
        )
        respx.post(f"{BASE}{GEMINI.path}").mock(return_value=httpx.Response(200, content=payload))
        block = (await make(GEMINI).complete(["x"])).content[0]
        assert isinstance(block, ImageBlock)
        assert (block.media_type, block.data) == ("image/png", "AA")

    @respx.mock
    async def test_inline_audio_becomes_audio_block(self) -> None:
        payload = sse(
            (
                None,
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [{"inlineData": {"mimeType": "audio/wav", "data": "BB"}}]
                            }
                        }
                    ]
                },
            ),
        )
        respx.post(f"{BASE}{GEMINI.path}").mock(return_value=httpx.Response(200, content=payload))
        block = (await make(GEMINI).complete(["x"])).content[0]
        assert isinstance(block, AudioBlock)
        assert block.media_type == "audio/wav"


class TestResponsesReasoning:
    @respx.mock
    async def test_summary_parts_remain_distinct(self) -> None:
        payload = sse(
            (
                None,
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "type": "reasoning",
                        "summary": [{"text": "첫 줄"}, {"text": "둘째 줄"}],
                    },
                },
            ),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(f"{BASE}/v1/responses").mock(return_value=httpx.Response(200, content=payload))
        blocks = (await make(responses).complete(["x"])).content
        assert all(isinstance(block, ThinkingBlock) for block in blocks)
        assert [block.thinking for block in blocks] == ["첫 줄", "둘째 줄"]

    @respx.mock
    async def test_encrypted_content_is_kept_when_no_summary(self) -> None:
        """다른 벤더로는 못 옮기지만 발급 벤더로 되돌릴 때는 필요하다."""
        payload = sse(
            (
                None,
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {"type": "reasoning", "encrypted_content": "ENC"},
                },
            ),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(f"{BASE}/v1/responses").mock(return_value=httpx.Response(200, content=payload))
        block = (await make(responses).complete(["x"])).content[0]
        assert isinstance(block, ThinkingBlock)
        assert block.thinking == ""
        assert block.encrypted_content == "ENC"


class TestCitationSerialization:
    """네이티브 인용 채널이 없는 대상에서는 근거를 직렬화한다. 조용히 버리지 않는다."""

    def test_native_citation_replays_on_the_same_vendor(self) -> None:
        block = TextBlock(
            text="서울이다",
            source="messages",
            citations=[Citation(source="messages", native={"type": "char_location"})],
        )
        body = make(messages).build_request([HubMessage(role="assistant", content=[block])])
        part = body["messages"][0]["content"][0]
        assert part["text"] == "서울이다"
        assert part["citations"] == [{"type": "char_location"}]

    def test_native_citation_is_serialized_for_other_vendors(self) -> None:
        block = TextBlock(
            text="서울이다",
            source="messages",
            citations=[Citation(id="d1", source="messages", native={"type": "char_location"})],
        )
        message = HubMessage(role="assistant", content=[block])
        tagged = '<cite id="d1">서울이다</cite>'

        chat = make(chat_completions).build_request([message])
        assert chat["messages"][0]["content"] == tagged

        gemini = make(GEMINI).build_request([message])
        assert gemini["contents"][0]["parts"][0]["text"] == tagged

    def test_plain_text_is_untouched(self) -> None:
        message = HubMessage(role="assistant", content=[TextBlock(text="서울이다")])
        body = make(chat_completions).build_request([message])
        assert body["messages"][0]["content"] == "서울이다"


class TestEvidenceFamilies:
    """비슷해 보이는 근거 구조를 추측 변환하지 않고 원형별로 보존한다."""

    @respx.mock
    async def test_responses_final_item_keeps_annotation_and_replays_it_only_there(self) -> None:
        annotation = {
            "type": "url_citation",
            "start_index": 0,
            "end_index": 2,
            "url": "https://example.test/source",
            "title": "근거",
        }
        item = {
            "type": "message",
            "id": "m1",
            "role": "assistant",
            "status": "completed",
            "content": [
                {"type": "output_text", "text": "서울", "annotations": [annotation]}
            ],
        }
        payload = sse(
            (
                None,
                {"type": "response.output_item.done", "output_index": 0, "item": item},
            ),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(f"{BASE}/v1/responses").mock(return_value=httpx.Response(200, content=payload))
        result = await make(responses).complete(["x"])

        text = next(block for block in result.content if isinstance(block, TextBlock))
        evidence = next(
            block for block in result.content if isinstance(block, AnnotationBlock)
        )
        assert text.text == "서울"
        assert evidence.source == "responses"
        assert evidence.target_index == text.index
        assert evidence.native == annotation

        replay = make(responses).build_request([HubMessage.of_response(result)])
        assert replay["input"][0]["content"][0]["annotations"] == [annotation]
        # 타 벤더에는 native annotation 채널이 없다. 조용히 버리지 않고 직렬화한다.
        foreign = make(chat_completions).build_request([HubMessage.of_response(result)])
        content = foreign["messages"][0]["content"]
        assert content.startswith("서울\n\n<references>")
        assert 'uri="https://example.test/source"' in content
        assert 'title="근거"' in content

    @respx.mock
    async def test_streamed_annotation_is_not_duplicated_by_final_item(self) -> None:
        annotation = {
            "type": "url_citation",
            "start_index": 0,
            "end_index": 2,
            "url": "https://example.test/source",
        }
        item = {
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "output_text", "text": "서울", "annotations": [annotation]}
            ],
        }
        payload = sse(
            (
                None,
                {
                    "type": "response.output_text.annotation.added",
                    "output_index": 0,
                    "content_index": 0,
                    "annotation_index": 0,
                    "annotation": annotation,
                },
            ),
            (
                None,
                {"type": "response.output_item.done", "output_index": 0, "item": item},
            ),
            (None, {"type": "response.completed", "response": {"status": "completed"}}),
        )
        respx.post(f"{BASE}/v1/responses").mock(return_value=httpx.Response(200, content=payload))
        result = await make(responses).complete(["x"])
        assert len(result.blocks_of(AnnotationBlock)) == 1

    @respx.mock
    async def test_gemini_keeps_citation_metadata_and_grounding_graph_separate(self) -> None:
        payload = sse(
            (
                None,
                {
                    "candidates": [
                        {
                            "content": {"role": "model", "parts": [{"text": "서울"}]},
                            "citationMetadata": {
                                "citationSources": [
                                    {
                                        "startIndex": 0,
                                        "endIndex": 2,
                                        "uri": "https://simple.test",
                                        "license": "CC",
                                    }
                                ]
                            },
                            "groundingMetadata": {
                                "webSearchQueries": ["서울"],
                                "groundingChunks": [
                                    {
                                        "web": {
                                            "uri": "https://ground.test",
                                            "title": "검색 결과",
                                        }
                                    }
                                ],
                                "groundingSupports": [
                                    {
                                        "segment": {
                                            "startIndex": 0,
                                            "endIndex": 2,
                                            "text": "서울",
                                        },
                                        "groundingChunkIndices": [0],
                                        "confidenceScores": [0.9],
                                    }
                                ],
                                "searchEntryPoint": {"renderedContent": "<div>검색</div>"},
                            },
                        }
                    ]
                },
            ),
        )
        respx.post(f"{BASE}{GEMINI.path}").mock(return_value=httpx.Response(200, content=payload))
        result = await make(GEMINI).complete(["x"])

        annotation = next(
            block for block in result.content if isinstance(block, AnnotationBlock)
        )
        grounding = next(
            block for block in result.content if isinstance(block, GroundingBlock)
        )
        assert annotation.source == "generate_content"
        assert annotation.native["license"] == "CC"
        assert grounding.sources[0].uri == "https://ground.test"
        assert grounding.supports[0].source_indices == [0]
        assert grounding.supports[0].text == "서울"
        assert grounding.search_queries == ["서울"]

        # grounding 그래프는 평탄화하지 않는다. 출처와 구간-출처 관계를 함께 내린다.
        foreign = make(chat_completions).build_request([HubMessage.of_response(result)])
        content = foreign["messages"][0]["content"]
        assert '<source index="0" uri="https://ground.test" title="검색 결과"/>' in content
        assert '<support sources="0" start="0" end="2">서울</support>' in content
        assert "<query>서울</query>" in content


class TestMultimodalRequest:
    """요청 방향 멀티모달 part. 벤더마다 이름과 구조가 다르다."""

    IMAGE = ImageBlock(media_type="image/png", data="AAAA", detail="low")
    AUDIO = AudioBlock(data="BBBB", format="wav")
    PDF = DocumentBlock(id="d1", title="보고서", data="CCCC", media_type="application/pdf")

    def _message(self) -> HubMessage:
        return HubMessage(
            role="user",
            content=[self.IMAGE, self.AUDIO, self.PDF, TextBlock(text="설명해")],
        )

    def test_chat_completions_uses_image_url_and_input_audio(self) -> None:
        body = make(chat_completions).build_request([self._message()])
        parts = body["messages"][0]["content"]
        assert [p["type"] for p in parts] == ["image_url", "input_audio", "file", "text"]
        assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
        assert parts[0]["image_url"]["detail"] == "low"
        assert parts[1]["input_audio"] == {"data": "BBBB", "format": "wav"}
        assert parts[2]["file"]["filename"] == "보고서"

    def test_responses_uses_current_input_content_union(self) -> None:
        """오디오 입력 채널이 없다. 조용히 버리지 않고 자리에 표시를 남긴다."""
        body = make(responses).build_request([self._message()])
        parts = body["input"][0]["content"]
        assert [p["type"] for p in parts] == [
            "input_image",
            "input_text",  # 오디오 자리
            "input_file",
            "input_text",
        ]
        assert parts[2]["filename"] == "보고서"
        assert '<audio media-type="audio/wav" unavailable="true">' in parts[1]["text"]

    def test_anthropic_has_no_audio_channel_either(self) -> None:
        body = make(messages).build_request([self._message()])
        texts = [p["text"] for p in body["messages"][0]["content"] if p["type"] == "text"]
        assert any("<attachments>" in t and "audio/wav" in t for t in texts)

    def test_gemini_uses_inline_data(self) -> None:
        body = make(GEMINI).build_request([self._message()])
        parts = body["contents"][0]["parts"]
        mimes = [p["inlineData"]["mimeType"] for p in parts if "inlineData" in p]
        assert mimes == ["image/png", "audio/wav", "application/pdf"]
        assert any("text" in p for p in parts)

    def test_anthropic_has_no_audio_channel(self) -> None:
        """이 API는 오디오 입력을 받지 않는다. 이미지와 문서만 네이티브로 간다."""
        body = make(messages).build_request([self._message()])
        parts = body["messages"][0]["content"]
        kinds = [p["type"] for p in parts]
        assert "document" in kinds
        assert "image" in kinds
        assert "audio" not in kinds

    def test_url_image_is_not_wrapped_in_a_data_url(self) -> None:
        message = HubMessage(role="user", content=[ImageBlock(url="https://example.com/a.png")])
        body = make(chat_completions).build_request([message])
        assert body["messages"][0]["content"][0]["image_url"]["url"] == (
            "https://example.com/a.png"
        )

    def test_tool_result_can_carry_nested_blocks(self) -> None:
        """Anthropic ``tool_result.content``가 블록 리스트다. 이미지를 돌려주는 도구가 쓴다."""
        message = HubMessage(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="c1",
                    content="차트입니다",
                    blocks=[ImageBlock(media_type="image/png", data="ZZ")],
                )
            ],
        )
        body = make(messages).build_request([message])
        result = body["messages"][0]["content"][0]
        assert result["type"] == "tool_result"
        assert [b["type"] for b in result["content"]] == ["text", "image"]


class TestSerializationFallback:
    """네이티브 채널이 없거나 호출자가 고르면 텍스트로 내린다. 조용히 사라지지 않는다."""

    PDF = DocumentBlock(id="d1", data="PDFBYTES", media_type="application/pdf")

    def _content(self, doc: DocumentBlock, **kwargs: object) -> str:
        bridge = make(messages, **kwargs)
        message = HubMessage(role="user", content=[doc, TextBlock(text="요약해")])
        parts = bridge.build_request([message])["messages"][0]["content"]
        if isinstance(parts, str):
            return parts
        return " ".join(p.get("text", f"<{p['type']}>") for p in parts)

    def test_native_channel_is_used_by_default(self) -> None:
        assert "<document>" in self._content(self.PDF)

    def test_serialize_flag_overrides_the_native_channel(self) -> None:
        forced = self.PDF.model_copy(update={"serialize": True})
        content = self._content(forced)
        assert "<documents>" in content
        assert 'unavailable="true"' in content

    def test_extractor_supplies_the_text(self) -> None:
        forced = self.PDF.model_copy(update={"serialize": True})
        content = self._content(
            forced, extractors={"application/pdf": lambda block: "1장. 서울의 기후"}
        )
        assert '<content media-type="application/pdf">1장. 서울의 기후</content>' in content
        assert "unavailable" not in content

    def test_extractor_failure_is_a_distinct_state(self) -> None:
        """전처리기 실패는 전달 불가와 다르다. degrade하지 않고 명시적으로 올린다.

        없는 것은 사전에 알 수 있는 설정 상태이고, 실패는 특정 콘텐츠의 런타임 오류다.
        같은 태그로 접으면 호출자 코드의 결함이 그 뒤에 숨는다.
        """

        def boom(block: object) -> str:
            raise RuntimeError("PDF 헤더가 깨짐")

        forced = self.PDF.model_copy(update={"serialize": True})
        with pytest.raises(ExtractionError) as info:
            self._content(forced, extractors={"application/pdf": boom})
        assert info.value.media_type == "application/pdf"
        assert isinstance(info.value.__cause__, RuntimeError)

    def test_unsupported_audio_leaves_a_notice(self) -> None:
        """오디오 입력 채널이 없는 대상에서 조용히 버리지 않는다."""
        message = HubMessage(role="user", content=[AudioBlock(data="AAAA", format="wav")])
        parts = make(messages).build_request([message])["messages"][0]["content"]
        text = parts if isinstance(parts, str) else parts[0]["text"]
        assert '<audio media-type="audio/wav" unavailable="true">' in text
