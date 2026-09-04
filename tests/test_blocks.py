"""개방형 유니온. 등록되지 않은 타입이 스트림을 깨뜨리지 않아야 한다."""

from __future__ import annotations

from typing import Literal

import pytest

from enhanced_completion import (
    ContentBlock,
    HubResponse,
    TextBlock,
    ThinkingBlock,
    VendorBlock,
    register_block,
    registered_blocks,
)


class CustomCitationBlock(ContentBlock):
    type: Literal["custom_citation"] = "custom_citation"
    id: str = ""
    text: str = ""


class TestBuiltins:
    def test_builtin_types_are_registered(self) -> None:
        assert set(registered_blocks()) >= {
            "text",
            "thinking",
            "tool_use",
            "tool_result",
            "image",
        }

    def test_dict_resolves_to_concrete_class(self) -> None:
        response = HubResponse.model_validate({"content": [{"type": "text", "text": "hi"}]})
        assert isinstance(response.content[0], TextBlock)
        assert response.text == "hi"


class TestRuntimeRegistration:
    def test_registered_block_resolves(self) -> None:
        register_block(CustomCitationBlock)
        response = HubResponse.model_validate(
            {"content": [{"type": "custom_citation", "id": "doc1", "text": "서울"}]}
        )
        block = response.content[0]
        assert isinstance(block, CustomCitationBlock)
        assert block.id == "doc1"

    def test_block_without_type_default_is_rejected(self) -> None:
        class Bad(ContentBlock):
            pass

        with pytest.raises(ValueError, match="non-empty string default"):
            register_block(Bad)


class TestUnknownTypes:
    def test_unknown_type_falls_back_to_vendor_block(self) -> None:
        """벤더가 새 블록을 추가해도 스트림 전체가 깨지지 않아야 한다."""
        response = HubResponse.model_validate(
            {"content": [{"type": "web_search_call", "query": "seoul", "status": "done"}]}
        )
        block = response.content[0]
        assert isinstance(block, VendorBlock)
        assert block.type == "web_search_call"
        assert block.raw == {"query": "seoul", "status": "done"}

    def test_vendor_block_keeps_explicit_raw(self) -> None:
        response = HubResponse.model_validate(
            {"content": [{"type": "executableCode", "raw": {"language": "python"}}]}
        )
        block = response.content[0]
        assert isinstance(block, VendorBlock)
        assert block.raw == {"language": "python"}


class TestPreservation:
    def test_extra_fields_survive_validation(self) -> None:
        """같은 벤더로 되돌릴 때의 무손실 왕복이 이 설정에 달려 있다."""
        response = HubResponse.model_validate(
            {"content": [{"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}]}
        )
        dumped = response.content[0].model_dump()
        assert dumped["cache_control"] == {"type": "ephemeral"}

    def test_source_survives_dump_and_reload(self) -> None:
        block = ThinkingBlock(
            thinking="why",
            signature="signed",
            source="chat_completions",
            native={"type": "reasoning"},
        )
        response = HubResponse(content=[block])
        restored = HubResponse.model_validate(response.model_dump())
        restored_block = restored.content[0]
        assert isinstance(restored_block, ThinkingBlock)
        assert restored_block.thinking == "why"
        assert restored_block.signature == "signed"
        assert restored_block.source == "chat_completions"
        assert restored_block.native == {"type": "reasoning"}

    def test_block_instance_passes_through_unchanged(self) -> None:
        block = TextBlock(text="kept", index=3)
        response = HubResponse(content=[block])
        assert response.content[0] is block
