
"""벤더별 고정 좌표와 반복 갱신·vocabulary 분할의 순서를 검증한다."""

from typing import Any

from cite_vocabulary import CiteVocabulary

from completion_bridge import HubResponse, StreamMerger
from completion_bridge.models.hub import finalize_response
from completion_bridge.vendors import chat_completions, generate_content, messages, responses


def collect(adapter: Any, events: list[dict[str, Any]], *, cite: bool = False):
    """실제 변환기와 선택한 vocabulary를 거쳐 델타 및 최종 결과를 만든다."""
    mapper = adapter.to_hub()
    lift = CiteVocabulary().lift_mapper() if cite else None
    merger = StreamMerger(HubResponse)
    blocks = []
    deltas = []
    for event in events:
        deltas.extend(mapper.map(event))
    deltas.extend(mapper.flush())
    for delta in deltas:
        outputs = lift.map(delta) if lift else [delta]
        for output in outputs:
            blocks.extend(output.content)
            merger.apply(output)
    if lift:
        for output in lift.flush():
            blocks.extend(output.content)
            merger.apply(output)
    return blocks, finalize_response(merger.build())


def test_messages_uses_received_index_without_allocating_a_suffix():
    """역순의 독립 블록과 같은 블록의 후속 델타를 수신 index로 구분한다."""
    events = []
    kinds = ["text", "thinking", "tool_use", "server_tool_use", "future"]
    for index in reversed(range(len(kinds))):
        events.append({"type": "content_block_start", "index": index,
                       "content_block": {"type": kinds[index], "id": str(index), "name": "tool"}})
    events.extend([
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "A"}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": "B"}},
    ])
    blocks, result = collect(messages, events)
    assert blocks[-1].seq == blocks[-2].seq == (0,)
    assert [block.type for block in result.content] == [
        "text", "thinking", "tool_use", "server_tool", "future",
    ]
    assert result.text == "AB"


def test_chat_tools_share_group_and_sort_by_received_index():
    """도구 조각의 수신 순서·채널 전환과 무관하게 같은 index는 같은 블록이다."""
    fields = [
        {"reasoning": "R"}, {"reasoning": "1"}, {"content": "T"},
        {"tool_calls": [{"index": 7, "id": "seven", "function": {"name": "a"}}]},
        {"tool_calls": [{"index": 0, "id": "zero", "function": {"name": "b"}}]},
        {"content": "U"},
        {"tool_calls": [{"index": 7, "function": {"arguments": "{}"}}]},
        {"audio": {"data": "A"}}, {"audio": {"data": "B"}},
    ]
    events = []
    for field in fields:
        events.append({"choices": [{"delta": field}]})
    blocks, result = collect(chat_completions, events)
    assert blocks[0].seq == blocks[1].seq == (0, -1)
    assert blocks[3].seq == blocks[6].seq == (2, 7)
    assert blocks[4].seq == (2, 0)
    assert [block.type for block in result.content] == [
        "thinking", "text", "tool_use", "tool_use", "text", "audio",
    ]
    assert result.content[2].id == "zero"
    assert result.content[3].input_json == "{}"
    assert result.content[-1].data == "AB"


def test_responses_annotations_follow_all_cite_segments_and_snapshots_do_not_repeat():
    """늦게 온 원본 좌표·주석·완료 스냅샷을 같은 논리 좌표로 처리한다."""
    annotation = {"type": "url_citation", "url": "https://example.com", "title": "source"}
    text = 'A<cite id="doc">B</cite>C'
    events = [
        {"type": "response.output_text.delta", "output_index": 2, "content_index": 0,
         "delta": text},
        {"type": "response.audio.delta", "delta": "audio"},
        {"type": "response.reasoning_summary_text.delta", "output_index": 0,
         "summary_index": 0, "delta": "summary"},
        {"type": "response.reasoning_text.delta", "output_index": 0,
         "content_index": 0, "delta": "reason"},
        {"type": "response.output_item.added", "output_index": 1,
         "item": {"type": "function_call", "call_id": "call", "name": "lookup"}},
        {"type": "response.output_text.annotation.added", "output_index": 2,
         "content_index": 0, "annotation_index": 0, "annotation": annotation},
        {"type": "response.output_item.done", "output_index": 2,
         "item": {"type": "message", "content": [
             {"type": "output_text", "text": text, "annotations": [annotation]}]}},
        {"type": "response.audio.transcript.delta", "delta": "transcript"},
        {"type": "response.completed", "response": {"citations": ["https://whole.example"]}},
    ]
    blocks, result = collect(responses, events, cite=True)
    assert all(len(block.seq) == 5 for block in blocks)
    assert [block.type for block in result.content] == [
        "audio", "thinking", "thinking", "tool_use", "text", "text", "text",
        "annotation", "annotation",
    ]
    assert result.text == "ABC"
    assert result.content[7].target_index == 4
    assert result.content[0].transcript == "transcript"


def test_gemini_metadata_groups_are_stable_across_early_and_late_updates():
    """본문 중간의 메타데이터도 고정 그룹에 있고 반복 스냅샷은 새 블록을 만들지 않는다."""
    metadata = {
        "citationMetadata": {"citationSources": [{"uri": "https://example.com"}]},
        "groundingMetadata": {"webSearchQueries": ["query"]},
        "urlContextMetadata": {"urlMetadata": []},
        "safetyRatings": [{"category": "test", "probability": "LOW"}],
    }
    events = [
        {"candidates": [{"content": {"parts": [{"text": "R", "thought": True}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "A"}]}, **metadata}]},
        {"candidates": [{"content": {"parts": [{"text": "B"}]}, **metadata}]},
        {"candidates": [{"content": {"parts": [
            {"functionCall": {"name": "lookup", "args": {}}},
            {"functionResponse": {"name": "lookup", "response": {"result": "ok"}}},
            {"executableCode": {"code": "print(1)"}},
            {"codeExecutionResult": {"output": "1"}},
            {"inlineData": {"mimeType": "image/png", "data": "AAAA"}},
            {"audioTranscription": {"text": "voice"}},
            {"future": "value"},
        ]}}]},
    ]
    blocks, result = collect(generate_content, events)
    assert all(len(block.seq) == 2 for block in blocks)
    assert [block.type for block in result.content] == [
        "thinking", "text", "tool_use", "tool_result", "server_tool", "server_tool",
        "image", "audio", "gemini_part", "annotation", "grounding", "server_tool",
        "safetyRatings",
    ]
    assert result.text == "AB"
    assert result.content[-4].target_index == 1
    assert result.content[-3].search_queries == ["query"]
    feedback, _ = collect(generate_content, [{"promptFeedback": {"blockReason": "SAFETY"}}])
    assert feedback[0].seq == (0, -1)


def test_reasoning_item_metadata_uses_the_first_actual_part_without_an_empty_block():
    """원문 추론만 있는 item도 초기 메타데이터 때문에 빈 요약 블록이 생성되지 않는다."""
    events = [
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "reasoning", "id": "r", "encrypted_content": "ENC"}},
        {"type": "response.reasoning_text.delta", "output_index": 0,
         "content_index": 0, "delta": "reason"},
        {"type": "response.output_item.done", "output_index": 0,
         "item": {"type": "reasoning", "content": [{"text": "reason"}]}},
    ]
    _, result = collect(responses, events)
    assert len(result.content) == 1
    assert result.content[0].thinking == "reason"
    assert result.content[0].encrypted_content == "ENC"
