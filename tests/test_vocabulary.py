"""Vocabulary의 필수 블록 선언과 선택적인 변환 메서드 계약."""

from typing import Any

import pytest

from completion_bridge import ContentBlock, TextBlock, Vocabulary


def test_base_vocabulary_cannot_be_instantiated() -> None:
    factory: Any = Vocabulary
    with pytest.raises(TypeError, match="blocks"):
        factory()


def test_subclass_without_blocks_remains_abstract() -> None:
    class MissingBlocks(Vocabulary):
        pass

    factory: Any = MissingBlocks
    with pytest.raises(TypeError, match="blocks"):
        factory()


def test_blocks_method_satisfies_contract_and_transforms_are_optional() -> None:
    class TextVocabulary(Vocabulary):
        def blocks(self) -> tuple[type[ContentBlock], ...]:
            return (TextBlock,)

    vocabulary = TextVocabulary()
    assert vocabulary.blocks() == (TextBlock,)
    assert vocabulary.lower([TextBlock(text="a")]) is None
    assert vocabulary.lift_mapper() is None
    vocabulary.register()


def test_multiple_block_types_can_be_registered() -> None:
    class TextVocabulary(Vocabulary):
        def blocks(self) -> tuple[type[ContentBlock], ...]:
            return (ContentBlock, TextBlock)

    assert TextVocabulary().blocks() == (ContentBlock, TextBlock)
