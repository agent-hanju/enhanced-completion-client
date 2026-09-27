"""Message-level authorship affects request lowering, never response streaming."""

import json

import pytest
from pydantic import ValidationError

from completion_bridge import (
    HubMessage,
    HubResponse,
    StreamMerger,
    SyncBridge,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from completion_bridge.vendors import chat_completions, generate_content, messages, responses

GEMINI = generate_content.for_model("test")


def build(history, vendor=GEMINI):
    return SyncBridge(vendor=vendor, base_url="https://test.invalid", model="test").build_request(
        history
    )


def call(**kwargs):
    return ToolUseBlock(id="c1", name="lookup", input_json='{"x":1}', **kwargs)


def test_default_and_response_contract():
    mapper = chat_completions.to_hub()
    merger = StreamMerger(HubResponse)
    for delta in mapper.map({"choices": [{"delta": {"content": "hello"}}]}):
        assert "synthetic" not in delta.model_dump()
        merger.apply(delta)
    result = merger.build()
    assert "synthetic" not in result.model_dump()
    message = HubMessage.of_response(result)
    assert message.synthetic is False
    assert message.model_dump()["synthetic"] is False
    assert HubMessage.assistant("hello").synthetic is False


@pytest.mark.parametrize("role", ["user", "system", "tool", "developer"])
def test_synthetic_requires_assistant(role):
    with pytest.raises(ValidationError, match="requires role"):
        HubMessage(role=role, synthetic=True)
    assert HubMessage(role=role).synthetic is False


def test_stored_mapping_reaches_gemini_without_mutation():
    message = HubMessage.assistant(synthetic=True, blocks=[call()])
    stored = message.model_dump(mode="json")
    restored = HubMessage.model_validate_json(message.model_dump_json())
    assert restored.synthetic is True
    result = HubMessage(role="user", content=[ToolResultBlock(tool_use_id="c1", content="ok")])
    body = build([stored, result])
    part = body["contents"][0]["parts"][0]
    assert part == {
        "functionCall": {"id": "c1", "name": "lookup", "args": {"x": 1}},
        "thoughtSignature": "skip_thought_signature_validator",
    }
    assert body["contents"][1]["parts"][0]["functionResponse"]["id"] == "c1"
    assert message.model_dump(mode="json") == stored
    assert build([restored, result]) == body
    assert '"synthetic"' not in json.dumps(body)


def test_real_signatures_and_parallel_calls_are_preserved():
    blocks = [
        call(source="generate_content", signature="real"),
        ToolUseBlock(
            id="c2",
            name="lookup",
            source="generate_content",
            native={"thoughtSignature": "native-real"},
        ),
        ToolUseBlock(id="c3", name="lookup"),
        TextBlock(text="text"),
        ThinkingBlock(thinking="thought", source="generate_content", signature="thinking-real"),
    ]
    message = HubMessage.assistant(synthetic=True, blocks=blocks)
    before = message.model_dump(mode="json")
    parts = build([message])["contents"][0]["parts"]
    assert [p.get("thoughtSignature") for p in parts] == [
        "real",
        "native-real",
        "skip_thought_signature_validator",
        None,
        "thinking-real",
    ]
    assert message.model_dump(mode="json") == before


@pytest.mark.parametrize("source", [None, "generate_content", "chat_completions"])
def test_unsigned_normal_calls_are_not_inferred_synthetic(source):
    parts = build([HubMessage.assistant(blocks=[call(source=source)])])["contents"][0]["parts"]
    assert "thoughtSignature" not in parts[0]


@pytest.mark.parametrize("vendor", [chat_completions, messages, responses])
def test_other_vendors_keep_existing_wire_contract(vendor):
    normal = HubMessage.assistant(blocks=[call()])
    synthetic = HubMessage.assistant(synthetic=True, blocks=[call()])
    assert build([normal], vendor) == build([synthetic], vendor)
    assert '"synthetic"' not in json.dumps(build([synthetic], vendor))


def test_synthetic_text_does_not_get_a_signature():
    body = build([HubMessage.assistant("hello", synthetic=True)])
    assert body["contents"] == [{"role": "model", "parts": [{"text": "hello"}]}]
