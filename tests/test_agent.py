"""사내 agent-studio 어댑터.

계약은 agent-studio 1.4.1 프론트 번들에서 읽었다. 벤더 특이점 넷을 고정한다. 이벤트 이름이
본문에 실릴 수 있고, 종료가 두 형태이고, 본문 위치가 아홉 갈래이고, JSON이 아닌 프레임이 있다.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from enhanced_completion import (
    Bridge,
    HubMessage,
    SseFrame,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    VendorBlock,
)
from enhanced_completion.vendors import (
    AgentActivityBlock,
    AgentErrorBlock,
    AgentSourcesBlock,
    code_agent,
    single_agent,
)
from enhanced_completion.vendors.agent import TEXT_PATHS, extract_text

BASE = "http://studio.test"
AGENT = single_agent.for_agent("a1", session_id="s1")
URL = f"{BASE}{AGENT.path}"


def frames(*chunks: str, done: str = "[DONE]") -> bytes:
    body = "".join(chunks)
    if done:
        body += f"data: {done}\n\n"
    return body.encode()


def event(name: str | None, payload: object, *, in_body: bool = False) -> str:
    data = dict(payload) if isinstance(payload, dict) else payload
    if in_body and name and isinstance(data, dict):
        data = {"event": name, **data}
        head = ""
    else:
        head = f"event: {name}\n" if name else ""
    return f"{head}data: {json.dumps(data, ensure_ascii=False)}\n\n"


def bridge(adapter: object = AGENT) -> Bridge:
    return Bridge(
        vendor=adapter,  # type: ignore[arg-type]
        base_url=BASE,
        http_client=httpx.AsyncClient(),
        headers={"X-CSRF-Token": "tok"},
    )


class TestPaths:
    def test_single_agent_path(self) -> None:
        assert AGENT.path == "/console/api/single-agents/a1/execute"

    def test_code_agent_path(self) -> None:
        assert code_agent.for_agent("c9").path == "/console/api/code-agents/c9/execute"

    def test_stop_path_is_separate(self) -> None:
        """연결 종료가 취소가 아니다. 소비 앱이 이 경로를 따로 호출한다."""
        assert AGENT.stop_path("t7") == "/console/api/single-agents/a1/tasks/t7/stop"

    def test_prefix_is_configurable(self) -> None:
        from enhanced_completion.vendors import AgentAdapter

        adapter = AgentAdapter(kind="single", agent_id="a1", prefix="/api/")
        assert adapter.path == "/api/single-agents/a1/execute"

    def test_for_agent_keeps_session(self) -> None:
        assert AGENT.for_agent("a2").session_id == "s1"
        assert AGENT.for_agent("a2", session_id="s9").session_id == "s9"


class TestEventNameResolution:
    def test_name_from_sse_field(self) -> None:
        resolved = AGENT.resolve_event(SseFrame(event="answer", data='{"content":"x"}'))
        assert resolved.name == "answer"

    def test_name_from_body_event_when_field_is_absent(self) -> None:
        """프론트가 그렇게 한다. 이름이 프레임에 없으면 본문을 본다."""
        resolved = AGENT.resolve_event(SseFrame(data='{"event":"answer","content":"x"}'))
        assert resolved.name == "answer"

    def test_name_from_body_type(self) -> None:
        resolved = AGENT.resolve_event(SseFrame(data='{"type":"tool_call","name":"bash"}'))
        assert resolved.name == "tool_call"

    def test_body_wins_over_generic_message_field(self) -> None:
        resolved = AGENT.resolve_event(SseFrame(event="message", data='{"event":"answer"}'))
        assert resolved.name == "answer"

    def test_event_field_wins_when_specific(self) -> None:
        resolved = AGENT.resolve_event(SseFrame(event="chunk", data='{"event":"answer"}'))
        assert resolved.name == "chunk"

    def test_non_json_frame_falls_back_to_raw(self) -> None:
        """JSON이 아닌 프레임이 있다. 파싱 실패가 스트림을 깨뜨리지 않는다."""
        resolved = AGENT.resolve_event(SseFrame(event="answer", data="그냥 문자열"))
        assert resolved.name == "answer"
        assert resolved.payload is None
        assert resolved.raw == "그냥 문자열"

    def test_nameless_frame_defaults_to_message(self) -> None:
        assert AGENT.resolve_event(SseFrame(data='{"content":"x"}')).name == "message"


class TestTermination:
    def test_done_payload(self) -> None:
        assert AGENT.is_terminal(SseFrame(data="[DONE]"))

    def test_done_event_name(self) -> None:
        """``[DONE]``을 안 보내고 ``done`` 이벤트로 끝내는 경로도 있다."""
        assert AGENT.is_terminal(SseFrame(event="done", data="{}"))

    def test_done_in_body(self) -> None:
        assert AGENT.is_terminal(SseFrame(data='{"event":"done"}'))

    def test_content_frame_is_not_terminal(self) -> None:
        assert not AGENT.is_terminal(SseFrame(event="answer", data='{"content":"x"}'))


class TestTextExtraction:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            ({"content": "a"}, "a"),
            ({"body": "b"}, "b"),
            ({"answer": "c"}, "c"),
            ({"delta": "d"}, "d"),
            ({"text": "e"}, "e"),
            ({"message": {"content": "f"}}, "f"),
            ({"choices": [{"delta": {"content": "g"}}]}, "g"),
            ({"choices": [{"message": {"content": "h"}}]}, "h"),
            ({"data": "i"}, "i"),
        ],
    )
    def test_every_documented_path(self, payload: dict[str, object], expected: str) -> None:
        assert extract_text(payload) == expected

    def test_nine_paths_are_declared(self) -> None:
        assert len(TEXT_PATHS) == 9

    def test_earlier_path_wins(self) -> None:
        assert extract_text({"content": "first", "answer": "second"}) == "first"

    def test_plain_string_payload(self) -> None:
        assert extract_text("문자열") == "문자열"

    def test_missing_text_is_empty(self) -> None:
        assert extract_text({"unrelated": 1}) == ""

    def test_openai_shape_mixed_in(self) -> None:
        """자체 모양과 OpenAI 모양이 한 스트림에 섞여 온다."""
        assert extract_text({"choices": [{"delta": {"content": "섞임"}}]}) == "섞임"


class TestStreaming:
    @respx.mock
    async def test_text_aliases_all_become_one_block(self) -> None:
        """본문 이벤트 별칭이 일곱 개다. 어댑터가 하나로 정규화한다."""
        payload = frames(
            event("agent_message", {"content": "가"}),
            event("message_delta", {"delta": "나"}),
            event("answer", {"answer": "다"}),
            event("chunk", {"body": "라"}),
            event("delta", {"text": "마"}),
            event("text_chunk", {"data": "바"}),
            event("message", {"message": {"content": "사"}}),
        )
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        result = await bridge().complete(["안녕"])
        assert result.text == "가나다라마바사"
        assert [b.type for b in result.content] == ["text"]

    @respx.mock
    async def test_reasoning_becomes_thinking(self) -> None:
        payload = frames(
            event("reasoning_delta", {"content": "생각"}),
            event("answer", {"content": "답"}),
        )
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        result = await bridge().complete(["x"])
        assert [b.type for b in result.content] == ["thinking", "text"]
        assert isinstance(result.content[0], ThinkingBlock)
        assert result.text == "답"

    @respx.mock
    async def test_event_name_in_body_only(self) -> None:
        payload = frames(
            event("answer", {"content": "본문"}, in_body=True),
        )
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        assert (await bridge().complete(["x"])).text == "본문"

    @respx.mock
    async def test_tool_call_and_result_pair_up(self) -> None:
        payload = frames(
            event("tool_call", {"id": "t1", "name": "search", "arguments": {"q": "서울"}}),
            event("tool_result", {"id": "t1", "content": "결과"}),
        )
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        result = await bridge().complete(["x"])
        call = next(b for b in result.content if isinstance(b, ToolUseBlock))
        res = next(b for b in result.content if isinstance(b, ToolResultBlock))
        assert (call.id, call.name) == ("t1", "search")
        assert json.loads(call.input_json) == {"q": "서울"}
        assert (res.tool_use_id, res.content) == ("t1", "결과")

    @respx.mock
    async def test_code_agent_tool_events_become_activity(self) -> None:
        """``bash``는 실행 요청이 아니라 사후 보고다. ``tool_use``로 올리면 소비 앱이 응답을
        기다리다 멈춘다."""
        payload = frames(
            event("bash", {"content": "ls -la"}),
            event("edit", {"content": "app.py"}),
            event("skill_run", {"content": "deploy"}),
            event("answer", {"content": "완료"}),
        )
        adapter = code_agent.for_agent("c1")
        respx.post(f"{BASE}{adapter.path}").mock(return_value=httpx.Response(200, content=payload))
        result = await bridge(adapter).complete(["배포해"])
        activity = [b for b in result.content if isinstance(b, AgentActivityBlock)]
        assert [b.kind for b in activity] == ["bash", "edit", "skill_run"]
        assert [b.detail for b in activity] == ["ls -la", "app.py", "deploy"]
        assert result.text == "완료"
        assert not [b for b in result.content if isinstance(b, ToolUseBlock)]

    @respx.mock
    async def test_progress_events_become_activity(self) -> None:
        payload = frames(
            event("activity", {"content": "검색 중"}),
            event("activity_done", {"content": "검색 완료"}),
        )
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        result = await bridge().complete(["x"])
        assert [b.kind for b in result.content if isinstance(b, AgentActivityBlock)] == [
            "activity",
            "activity_done",
        ]

    @respx.mock
    async def test_sources_are_preserved(self) -> None:
        payload = frames(event("sources", {"items": [{"id": "d1", "title": "문서"}]}))
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        result = await bridge().complete(["x"])
        block = next(b for b in result.content if isinstance(b, AgentSourcesBlock))
        assert block.data == {"items": [{"id": "d1", "title": "문서"}]}

    @respx.mock
    async def test_lifecycle_identifier_reaches_response_metadata(self) -> None:
        payload = frames(
            event("run_start", {"taskId": "t9"}),
            event("answer", {"content": "답"}),
        )
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        result = await bridge().complete(["x"])
        assert result.id == "t9"
        assert result.role == "assistant"
        assert [b.type for b in result.content] == ["text"]

    @respx.mock
    async def test_error_event_sets_stop_reason_and_keeps_text(self) -> None:
        """여기까지 모인 본문을 잃지 않는다. 무엇을 보일지는 소비 앱이 정한다."""
        payload = frames(
            event("answer", {"content": "앞부분"}),
            event("error", {"content": "모델 실패"}),
        )
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        result = await bridge().complete(["x"])
        assert result.text == "앞부분"
        assert result.stop_reason == "error"
        assert (
            next(b for b in result.content if isinstance(b, AgentErrorBlock)).detail == "모델 실패"
        )

    @respx.mock
    async def test_unknown_event_is_preserved_not_dropped(self) -> None:
        """서버가 어휘를 늘려도 스트림이 깨지지 않는다."""
        payload = frames(event("brand_new", {"whatever": 1}))
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        result = await bridge().complete(["x"])
        block = next(b for b in result.content if isinstance(b, VendorBlock))
        assert block.type == "agent_brand_new"
        assert block.raw == {"whatever": 1}

    @respx.mock
    async def test_stream_ending_with_done_event_only(self) -> None:
        payload = frames(event("answer", {"content": "끝"}), event("done", {}), done="")
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        assert (await bridge().complete(["x"])).text == "끝"

    @respx.mock
    async def test_data_without_space_and_heartbeat(self) -> None:
        # 공백 없는 ``data:``와 주석 전용 하트비트가 섞인 프레임.
        payload = (
            ": keep-alive\n\n"
            'event: answer\ndata:{"content":"공백없음"}\n\n'
            "data: [DONE]\n\n"
        ).encode()
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        assert (await bridge().complete(["x"])).text == "공백없음"


class TestRequestBody:
    def test_last_user_message_is_flattened(self) -> None:
        """messages 배열을 받지 않는다. 이력은 서버가 sessionId로 관리한다."""
        body = bridge().build_request(["첫 질문", HubMessage.assistant("답"), "두 번째 질문"])
        assert body["message"] == "두 번째 질문"
        assert body["sessionId"] == "s1"
        assert body["responseMode"] == "streaming"
        assert body["fileIds"] == []
        assert body["webSearchEnabled"] is False

    def test_session_can_be_overridden_per_call(self) -> None:
        body = bridge().build_request(["x"], sessionId="s9")
        assert body["sessionId"] == "s9"

    def test_attachments_and_web_search(self) -> None:
        body = bridge().build_request(
            ["x"], fileIds=["f1"], attachmentIds=["a1"], webSearchEnabled=True
        )
        assert body["fileIds"] == ["f1"]
        assert body["attachmentIds"] == ["a1"]
        assert body["webSearchEnabled"] is True

    def test_task_id_is_included_only_when_given(self) -> None:
        assert "taskId" not in bridge().build_request(["x"])
        assert bridge().build_request(["x"], taskId="t1")["taskId"] == "t1"

    def test_unknown_params_pass_through(self) -> None:
        body = bridge().build_request(["x"], futureField=7)
        assert body["futureField"] == 7

    def test_no_user_message_gives_empty_string(self) -> None:
        body = bridge().build_request([HubMessage.assistant("답만 있다")])
        assert body["message"] == ""

    @respx.mock
    async def test_csrf_header_is_sent(self) -> None:
        route = respx.post(URL).mock(
            return_value=httpx.Response(200, content=frames(event("answer", {"content": "x"})))
        )
        await bridge().complete(["x"])
        assert route.calls.last.request.headers["x-csrf-token"] == "tok"


class TestVocabularyInterop:
    @respx.mock
    async def test_cite_vocabulary_works_over_agent_stream(self) -> None:
        """어휘 축과 벤더 축이 직교한다. 어휘를 고치지 않고 벤더만 갈아끼운다."""
        from enhanced_completion import CitationBlock, CiteVocabulary

        payload = frames(
            event("answer", {"content": '서울은 <cite id="d1">'}),
            event("answer", {"content": "수도</cite>다."}),
        )
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        client = Bridge(
            vendor=AGENT,  # type: ignore[arg-type]
            base_url=BASE,
            vocabularies=[CiteVocabulary()],
            http_client=httpx.AsyncClient(),
        )
        result = await client.complete(["수도?"])
        assert result.text == "서울은 수도다."
        cite = next(b for b in result.content if isinstance(b, CitationBlock))
        assert cite.id == "d1"
        assert result.text[cite.start_index : cite.end_index] == "수도"

    @respx.mock
    async def test_text_block_index_is_stable_across_aliases(self) -> None:
        payload = frames(
            event("answer", {"content": "가"}),
            event("chunk", {"content": "나"}),
        )
        respx.post(URL).mock(return_value=httpx.Response(200, content=payload))
        stream = bridge().stream(["x"])
        deltas = [d async for d in stream]
        streamed = "".join(b.text for d in deltas for b in d.content if isinstance(b, TextBlock))
        assert streamed == stream.result.text == "가나"
